"""End-to-end encryption tests for MatrixClient (plan 0010).

All tests are marked @pytest.mark.olm and skip cleanly when olm is unavailable.
Test 1 exercises a real nio store for persistence; tests 2-6 use mock nio
clients (DI) and assert call patterns — no real crypto needed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Guard: skip the entire module if olm is not importable.
olm = pytest.importorskip("olm")

pytestmark = pytest.mark.olm

# ---------------------------------------------------------------------------
# Helpers shared with test_client.py (reproduced here for isolation)
# ---------------------------------------------------------------------------

_HOMESERVER = "https://matrix.example.com"
_USER = "@alice:example.com"
_DEVICE_ID = "ALICEDEVICE"
_TOKEN = "access_token_xyz"


def _make_nio_room(
    room_id: str = "!enc:example.com",
    display_name: str = "Encrypted Room",
    encrypted: bool = True,
) -> Any:
    """A minimal fake nio MatrixRoom."""
    room = MagicMock()
    room.room_id = room_id
    room.display_name = display_name
    room.encrypted = encrypted
    room.users = {}
    room.power_levels = MagicMock()
    room.power_levels.users = {}
    return room


def _build_nio_mock(rooms: dict[str, Any] | None = None) -> AsyncMock:
    """Build a fully-mocked AsyncMock nio client."""
    import nio

    mock = AsyncMock(spec=nio.AsyncClient)
    mock.access_token = _TOKEN
    mock.user_id = _USER
    mock.device_id = _DEVICE_ID
    mock.rooms = rooms or {}
    mock.should_upload_keys = False
    mock.should_query_keys = False
    mock.room_send.return_value = MagicMock(spec=nio.RoomSendResponse)
    mock.keys_upload.return_value = MagicMock(spec=nio.KeysUploadResponse)
    mock.keys_query.return_value = MagicMock(spec=nio.KeysQueryResponse)
    mock.request_room_key.return_value = MagicMock(spec=nio.RoomKeyRequestResponse)
    mock.room_devices.return_value = {}
    return mock


# ---------------------------------------------------------------------------
# Test 1: store persists across restart (real nio store, no network)
# ---------------------------------------------------------------------------


async def test_store_persists_across_restart(tmp_path: Any) -> None:
    """Olm account created on first start is reloaded on second start.

    Uses a real nio.AsyncClient with a temp store directory to prove
    that load_store() actually persists and reloads the olm account.
    No network calls are made.
    """
    import nio

    from telemente.matrix.client import MatrixClient

    store_dir = tmp_path / "store"
    store_dir.mkdir()
    store_path = str(store_dir)

    # First client: restore_login then load_store
    config1 = nio.AsyncClientConfig(store_sync_tokens=True, encryption_enabled=True)
    nio_client1 = nio.AsyncClient(_HOMESERVER, user=_USER, store_path=store_path, config=config1)
    nio_client1.restore_login(user_id=_USER, device_id=_DEVICE_ID, access_token=_TOKEN)

    mc1 = MatrixClient(_HOMESERVER, store_path=store_path, nio_client=nio_client1)
    await mc1.restore(
        __import__("telemente.config", fromlist=["Session"]).Session(
            homeserver=_HOMESERVER,
            user_id=_USER,
            device_id=_DEVICE_ID,
            access_token=_TOKEN,
        )
    )

    # load_store is called during restore when store_path is set
    assert nio_client1.olm is not None
    identity_keys_1 = nio_client1.olm.account.identity_keys

    # Assert the store file was created
    store_files = list(store_dir.iterdir())
    assert len(store_files) >= 1, "Expected at least one store file"
    assert any(store_dir.name in str(f) or f.suffix in (".db",) for f in store_files)

    await nio_client1.close()

    # Second client: same store path — must reuse the same olm account
    config2 = nio.AsyncClientConfig(store_sync_tokens=True, encryption_enabled=True)
    nio_client2 = nio.AsyncClient(_HOMESERVER, user=_USER, store_path=store_path, config=config2)
    nio_client2.restore_login(user_id=_USER, device_id=_DEVICE_ID, access_token=_TOKEN)

    mc2 = MatrixClient(_HOMESERVER, store_path=store_path, nio_client=nio_client2)
    await mc2.restore(
        __import__("telemente.config", fromlist=["Session"]).Session(
            homeserver=_HOMESERVER,
            user_id=_USER,
            device_id=_DEVICE_ID,
            access_token=_TOKEN,
        )
    )

    assert nio_client2.olm is not None
    identity_keys_2 = nio_client2.olm.account.identity_keys

    assert identity_keys_1 == identity_keys_2, (
        "Second client should reuse the same olm account from the store, "
        f"but got different keys: {identity_keys_1} vs {identity_keys_2}"
    )

    await nio_client2.close()


# ---------------------------------------------------------------------------
# Test 2: encrypted room send uses ignore_unverified_devices=True
# ---------------------------------------------------------------------------


async def test_encrypted_room_uses_ignore_unverified() -> None:
    """send_text to an encrypted room calls room_send with ignore_unverified_devices=True."""

    from telemente.matrix.client import MatrixClient

    enc_room = _make_nio_room(room_id="!enc:example.com", encrypted=True)
    nio_mock = _build_nio_mock(rooms={"!enc:example.com": enc_room})

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    await client.send_text("!enc:example.com", "secret message")

    nio_mock.room_send.assert_awaited_once_with(
        "!enc:example.com",
        "m.room.message",
        {"msgtype": "m.text", "body": "secret message"},
        ignore_unverified_devices=True,
    )


# ---------------------------------------------------------------------------
# Test 3: unencrypted room send is unchanged
# ---------------------------------------------------------------------------


async def test_unencrypted_room_send_unchanged() -> None:
    """send_text to an unencrypted room calls room_send WITHOUT the e2e flag."""

    from telemente.matrix.client import MatrixClient

    plain_room = _make_nio_room(room_id="!plain:example.com", encrypted=False)
    nio_mock = _build_nio_mock(rooms={"!plain:example.com": plain_room})

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    await client.send_text("!plain:example.com", "plaintext message")

    # Must NOT pass ignore_unverified_devices
    nio_mock.room_send.assert_awaited_once_with(
        "!plain:example.com",
        "m.room.message",
        {"msgtype": "m.text", "body": "plaintext message"},
    )


# ---------------------------------------------------------------------------
# Test 4: keys_upload called after sync when should_upload_keys is True
# ---------------------------------------------------------------------------


async def test_keys_uploaded_after_sync() -> None:
    """When should_upload_keys is True after sync, keys_upload is awaited."""
    import nio

    from telemente.matrix.client import MatrixClient

    nio_mock = _build_nio_mock()
    nio_mock.should_upload_keys = True
    nio_mock.should_query_keys = False

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    # Simulate a sync response arriving
    fake_sync = MagicMock(spec=nio.SyncResponse)
    await client._on_sync(fake_sync)

    nio_mock.keys_upload.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 5: MegolmEvent triggers key request + placeholder message surfaced
# ---------------------------------------------------------------------------


async def test_megolm_undecryptable_requests_key() -> None:
    """An undecryptable MegolmEvent triggers request_room_key and surfaces a placeholder."""
    import nio

    from telemente.matrix.client import MatrixClient, NewMessage

    nio_mock = _build_nio_mock()

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    received: list[Any] = []

    async def handler(event: Any) -> None:
        received.append(event)

    client.subscribe(handler)

    # Build a fake MegolmEvent
    megolm_event = MagicMock(spec=nio.MegolmEvent)
    megolm_event.event_id = "$enc1:example.com"
    megolm_event.sender = "@bob:example.com"
    megolm_event.server_timestamp = 1_700_000_000_000
    megolm_event.room_id = "!enc:example.com"
    megolm_event.session_id = "session123"

    fake_room = _make_nio_room(room_id="!enc:example.com", encrypted=True)

    await client._on_megolm_event(fake_room, megolm_event)

    # request_room_key must have been called
    nio_mock.request_room_key.assert_awaited_once_with(megolm_event)

    # A placeholder NewMessage must have been emitted
    assert len(received) == 1
    assert isinstance(received[0], NewMessage)
    msg = received[0].message
    assert "unable to decrypt" in msg.body.lower() or "\U0001f512" in msg.body
    assert msg.room_id == "!enc:example.com"
    assert msg.event_id == "$enc1:example.com"


# ---------------------------------------------------------------------------
# Test 6: TOFU trusts room devices before send
# ---------------------------------------------------------------------------


async def test_tofu_trusts_room_devices_before_send() -> None:
    """Before sending to an encrypted room, all member devices are marked verified."""
    from nio.crypto import OlmDevice

    from telemente.matrix.client import MatrixClient

    enc_room = _make_nio_room(room_id="!enc:example.com", encrypted=True)
    nio_mock = _build_nio_mock(rooms={"!enc:example.com": enc_room})

    # Set up an unverified device in room_devices
    unverified_device = OlmDevice(
        user_id="@bob:example.com",
        device_id="BOBDEVICE",
        keys={"curve25519:BOBDEVICE": "curve_key", "ed25519:BOBDEVICE": "ed_key"},
    )
    nio_mock.room_devices.return_value = {
        "@bob:example.com": {"BOBDEVICE": unverified_device},
    }
    nio_mock.verify_device.return_value = True

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    await client.send_text("!enc:example.com", "hello")

    # verify_device must have been called for each device in the room
    nio_mock.verify_device.assert_called_with(unverified_device)

    # room_send must have been called with ignore_unverified_devices=True
    nio_mock.room_send.assert_awaited_once_with(
        "!enc:example.com",
        "m.room.message",
        {"msgtype": "m.text", "body": "hello"},
        ignore_unverified_devices=True,
    )
