"""Tests for telemente.matrix.client (plan 0003).

Unit tests inject mock nio clients; integration tests use aioresponses.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aioresponses import aioresponses

from telemente.matrix.client import (
    LoginError,
    MatrixClient,
    NewMessage,
    NotLoggedInError,
)
from telemente.matrix.models import RoomSummary

# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

_HOMESERVER = "https://matrix.example.com"
_USER = "@alice:example.com"
_PASSWORD = "s3cret"
_DEVICE_ID = "TESTDEVICE"
_TOKEN = "access_token_xyz"


def _make_login_response() -> Any:
    """A minimal fake nio LoginResponse."""
    obj = MagicMock()
    obj.user_id = _USER
    obj.device_id = _DEVICE_ID
    obj.access_token = _TOKEN
    # Make isinstance checks pass for LoginResponse
    return obj


def _make_login_error() -> Any:
    """A minimal fake nio LoginError."""
    import nio

    err = MagicMock(spec=nio.LoginError)
    return err


def _make_nio_room(
    room_id: str = "!room1:example.com",
    display_name: str = "Test Room",
    encrypted: bool = False,
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


def _make_text_event(
    event_id: str = "$ev1:example.com",
    sender: str = "@alice:example.com",
    body: str = "Hello!",
    server_timestamp: int = 1_700_000_000_000,
) -> Any:
    """A minimal fake nio RoomMessageText event."""
    import nio

    ev = MagicMock(spec=nio.RoomMessageText)
    ev.event_id = event_id
    ev.sender = sender
    ev.body = body
    ev.server_timestamp = server_timestamp
    return ev


def _build_nio_mock(
    login_return: Any = None,
    rooms: dict[str, Any] | None = None,
) -> AsyncMock:
    """Build a fully-mocked AsyncMock nio client."""
    import nio

    mock = AsyncMock(spec=nio.AsyncClient)
    mock.access_token = None
    mock.user_id = None
    mock.device_id = None
    mock.rooms = rooms or {}
    if login_return is not None:
        mock.login.return_value = login_return
    return mock


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


async def test_login_success() -> None:
    """login() with a valid response returns a Session with correct fields."""

    nio_mock = _build_nio_mock(login_return=_make_login_response())
    # The login response has user_id/device_id/access_token
    login_resp = _make_login_response()
    # Make it NOT an instance of LoginError
    nio_mock.login.return_value = login_resp

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    session = await client.login(_USER, _PASSWORD)

    assert session.user_id == _USER
    assert session.device_id == _DEVICE_ID
    assert session.access_token == _TOKEN
    assert session.homeserver == _HOMESERVER
    nio_mock.login.assert_awaited_once()


async def test_login_failure_raises() -> None:
    """login() when nio returns a LoginError raises telemente's LoginError."""
    import nio

    nio_mock = _build_nio_mock()
    nio_mock.login.return_value = MagicMock(spec=nio.LoginError)

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    with pytest.raises(LoginError):
        await client.login(_USER, _PASSWORD)


async def test_send_text_calls_room_send() -> None:
    """send_text() calls nio room_send with the correct msgtype and body."""
    import nio

    nio_mock = _build_nio_mock()
    nio_mock.access_token = _TOKEN  # simulate logged in
    nio_mock.user_id = _USER
    nio_mock.room_send.return_value = MagicMock(spec=nio.RoomSendResponse)

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True  # mark as logged in
    await client.send_text("!room:example.com", "hi")

    nio_mock.room_send.assert_awaited_once_with(
        "!room:example.com",
        "m.room.message",
        {"msgtype": "m.text", "body": "hi"},
    )


async def test_send_text_requires_login() -> None:
    """send_text() raises NotLoggedInError if the client is not logged in."""
    nio_mock = _build_nio_mock()
    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.send_text("!room:example.com", "hi")


async def test_rooms_maps_state() -> None:
    """rooms() maps nio MatrixRoom to RoomSummary with correct fields."""
    enc_room = _make_nio_room(
        room_id="!enc:example.com", display_name="Secret Room", encrypted=True
    )
    nio_mock = _build_nio_mock(rooms={"!enc:example.com": enc_room})
    nio_mock.access_token = _TOKEN
    nio_mock.user_id = _USER

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    summaries = client.rooms()
    assert len(summaries) == 1
    s = summaries[0]
    assert isinstance(s, RoomSummary)
    assert s.room_id == "!enc:example.com"
    assert s.display_name == "Secret Room"
    assert s.encrypted is True


async def test_subscribe_receives_new_message() -> None:
    """Subscribed handlers receive NewMessage events from _on_room_message."""

    nio_mock = _build_nio_mock()
    nio_mock.access_token = _TOKEN
    nio_mock.user_id = _USER

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    received: list[Any] = []

    async def handler(event: Any) -> None:
        received.append(event)

    client.subscribe(handler)

    room = _make_nio_room()
    room.users = {
        _USER: MagicMock(display_name="Alice", name="Alice"),
    }
    event = _make_text_event(body="Hello!")
    await client._on_room_message(room, event)

    assert len(received) == 1
    assert isinstance(received[0], NewMessage)
    assert received[0].message.body == "Hello!"


async def test_unsubscribe() -> None:
    """Unsubscribing prevents further delivery of events to that handler."""

    nio_mock = _build_nio_mock()
    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    received: list[Any] = []

    async def handler(event: Any) -> None:
        received.append(event)

    unsub = client.subscribe(handler)
    unsub()

    room = _make_nio_room()
    event = _make_text_event()
    await client._on_room_message(room, event)

    assert len(received) == 0


async def test_start_sync_requires_login() -> None:
    """start_sync() raises NotLoggedInError before login."""

    nio_mock = _build_nio_mock()
    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.start_sync()


async def test_start_sync_and_close() -> None:
    """After a fake login, start_sync() creates a task; close() cancels it."""

    nio_mock = _build_nio_mock()
    nio_mock.access_token = _TOKEN
    nio_mock.user_id = _USER
    nio_mock.device_id = _DEVICE_ID

    # sync_forever runs forever — make it block
    async def _sync_forever(**kwargs: Any) -> None:
        await asyncio.sleep(9999)

    nio_mock.sync_forever = _sync_forever

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    await client.start_sync()
    assert client._task is not None
    assert not client._task.done()

    await client.close()
    assert client._task.done()


# ---------------------------------------------------------------------------
# Integration tests (aioresponses)
# ---------------------------------------------------------------------------


async def test_login_integration_aioresponses() -> None:
    """Integration: real nio AsyncClient parses a stubbed /login response."""
    import nio

    login_json = {
        "access_token": "integration_token",
        "device_id": "INTDEVICE",
        "user_id": "@intuser:example.com",
        "home_server": "example.com",
    }
    login_url = f"{_HOMESERVER}/_matrix/client/v3/login"

    with aioresponses() as m:
        m.post(login_url, payload=login_json)
        real_nio = nio.AsyncClient(_HOMESERVER, "@intuser:example.com")
        client = MatrixClient(_HOMESERVER, nio_client=real_nio)
        session = await client.login("@intuser:example.com", "password")

    assert session.access_token == "integration_token"
    assert session.device_id == "INTDEVICE"
    assert session.user_id == "@intuser:example.com"

    await real_nio.close()


async def test_login_forbidden_integration() -> None:
    """Integration: stubbed 403 M_FORBIDDEN causes LoginError to be raised."""
    import nio

    error_json = {"errcode": "M_FORBIDDEN", "error": "Invalid password"}
    login_url = f"{_HOMESERVER}/_matrix/client/v3/login"

    with aioresponses() as m:
        m.post(login_url, payload=error_json, status=403)
        real_nio = nio.AsyncClient(_HOMESERVER, _USER)
        client = MatrixClient(_HOMESERVER, nio_client=real_nio)

        with pytest.raises(LoginError):
            await client.login(_USER, _PASSWORD)

    await real_nio.close()


# ---------------------------------------------------------------------------
# Unit tests — messages()
# ---------------------------------------------------------------------------


def _make_rooms_response(chunk: list[Any]) -> Any:
    """A fake nio.RoomMessagesResponse wrapping a given event list."""
    import nio

    resp = MagicMock(spec=nio.RoomMessagesResponse)
    resp.chunk = chunk
    return resp


def _make_media_event(
    event_id: str = "$m1:example.com",
    sender: str = "@alice:example.com",
    body: str = "photo.jpg",
    url: str = "mxc://example.com/abc123",
    server_timestamp: int = 1_700_000_001_000,
    kind: str = "image",
) -> Any:
    """A minimal fake nio RoomMessageMedia event."""
    import nio

    cls = {
        "image": nio.RoomMessageImage,
        "video": nio.RoomMessageVideo,
        "audio": nio.RoomMessageAudio,
        "file": nio.RoomMessageFile,
    }[kind]
    ev = MagicMock(spec=cls)
    ev.event_id = event_id
    ev.sender = sender
    ev.body = body
    ev.url = url
    ev.server_timestamp = server_timestamp
    return ev


def _make_megolm_event(
    event_id: str = "$enc1:example.com",
    sender: str = "@alice:example.com",
    server_timestamp: int = 1_700_000_002_000,
) -> Any:
    """A minimal fake nio MegolmEvent."""
    import nio

    ev = MagicMock(spec=nio.MegolmEvent)
    ev.event_id = event_id
    ev.sender = sender
    ev.server_timestamp = server_timestamp
    ev.session_id = "fake_session"
    return ev


async def test_messages_returns_text_events() -> None:
    """messages() returns Message objects for RoomMessageText events."""

    text_ev = _make_text_event(body="hello world")
    nio_mock = _build_nio_mock()
    nio_mock.room_messages.return_value = _make_rooms_response([text_ev])
    nio_mock.rooms = {"!r:example.com": _make_nio_room("!r:example.com")}

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True
    msgs = await client.messages("!r:example.com")

    assert len(msgs) == 1
    assert msgs[0].body == "hello world"
    assert msgs[0].media_url is None
    assert msgs[0].media_type is None


async def test_messages_includes_media_events() -> None:
    """messages() converts RoomMessageMedia to a Message with media_url set."""

    media_ev = _make_media_event(body="photo.jpg", url="mxc://example.com/abc123")
    nio_mock = _build_nio_mock()
    nio_mock.room_messages.return_value = _make_rooms_response([media_ev])
    nio_mock.rooms = {"!r:example.com": _make_nio_room("!r:example.com")}
    nio_mock.mxc_to_http.return_value = (
        "https://example.com/_matrix/media/v3/download/example.com/abc123"
    )

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True
    msgs = await client.messages("!r:example.com")

    assert len(msgs) == 1
    assert msgs[0].body == "photo.jpg"
    assert msgs[0].media_type == "image"
    assert msgs[0].media_url == "https://example.com/_matrix/media/v3/download/example.com/abc123"


async def test_messages_media_types_labeled_correctly() -> None:
    """Each media subtype gets the right label: image/video/audio/file."""

    cases = [("image", "image"), ("video", "video"), ("audio", "audio"), ("file", "file")]
    for kind, expected_label in cases:
        ev = _make_media_event(kind=kind)
        nio_mock = _build_nio_mock()
        nio_mock.room_messages.return_value = _make_rooms_response([ev])
        nio_mock.rooms = {}
        nio_mock.mxc_to_http.return_value = "https://example.com/media"

        client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
        client._logged_in = True
        msgs = await client.messages("!r:example.com")
        assert msgs[0].media_type == expected_label, f"failed for kind={kind}"


async def test_messages_megolm_events_become_placeholders() -> None:
    """MegolmEvent produces a Message with the 🔒 placeholder body."""
    enc_ev = _make_megolm_event()
    nio_mock = _build_nio_mock()
    nio_mock.room_messages.return_value = _make_rooms_response([enc_ev])
    nio_mock.rooms = {}
    nio_mock.request_room_key = AsyncMock()

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True
    msgs = await client.messages("!r:example.com")

    assert len(msgs) == 1
    assert "\U0001f512" in msgs[0].body
    assert msgs[0].media_url is None


async def test_messages_mixed_events_all_included() -> None:
    """Text, media, and encrypted events all appear in chronological order."""
    text_ev = _make_text_event(event_id="$t1", server_timestamp=1_000)
    media_ev = _make_media_event(event_id="$m1", server_timestamp=2_000)
    enc_ev = _make_megolm_event(event_id="$e1", server_timestamp=3_000)
    # room_messages returns newest-first; client.messages() reverses to chrono
    nio_mock = _build_nio_mock()
    nio_mock.room_messages.return_value = _make_rooms_response([enc_ev, media_ev, text_ev])
    nio_mock.rooms = {}
    nio_mock.mxc_to_http.return_value = "https://example.com/media"
    nio_mock.request_room_key = AsyncMock()

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True
    msgs = await client.messages("!r:example.com")

    assert len(msgs) == 3
    assert msgs[0].event_id == "$t1"
    assert msgs[1].event_id == "$m1"
    assert msgs[2].event_id == "$e1"
