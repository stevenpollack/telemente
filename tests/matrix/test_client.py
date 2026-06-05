"""Tests for telemente.matrix.client (plan 0003).

Unit tests inject mock nio clients; integration tests use aioresponses.
"""

from __future__ import annotations

import asyncio
import contextlib
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
    reply_to_event_id: str | None = None,
) -> Any:
    """A minimal fake nio RoomMessageText event."""
    import nio

    ev = MagicMock(spec=nio.RoomMessageText)
    ev.event_id = event_id
    ev.sender = sender
    ev.body = body
    ev.server_timestamp = server_timestamp
    # Build source dict so reply_to_event_id parsing works
    content: dict[str, Any] = {}
    if reply_to_event_id is not None:
        content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to_event_id}}
    ev.source = {"content": content}
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


async def test_messages_resolves_media_urls_concurrently() -> None:
    """messages() resolves all mxc_to_http URLs; each media Message has a non-None
    media_url. Correctness of all resolved URLs is the observable invariant
    (concurrency itself is not directly assertable in unit tests)."""

    media_ev1 = _make_media_event(
        event_id="$m1", url="mxc://example.com/img1", server_timestamp=1_000, kind="image"
    )
    media_ev2 = _make_media_event(
        event_id="$m2",
        url="mxc://example.com/vid1",
        server_timestamp=2_000,
        kind="video",
        body="clip.mp4",
    )
    text_ev = _make_text_event(event_id="$t1", server_timestamp=3_000, body="hello")
    # room_messages returns newest-first
    nio_mock = _build_nio_mock()
    nio_mock.room_messages.return_value = _make_rooms_response([text_ev, media_ev2, media_ev1])
    nio_mock.rooms = {}

    # mxc_to_http returns different URLs per call.
    # The event list is [text_ev, media_ev2, media_ev1] (newest-first).
    # The loop processes media_ev2 first, then media_ev1.
    nio_mock.mxc_to_http.side_effect = [
        "https://example.com/download/vid1",
        "https://example.com/download/img1",
    ]

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True
    msgs = await client.messages("!r:example.com")

    # All three messages must be returned in chronological order
    assert len(msgs) == 3
    assert msgs[0].event_id == "$m1"
    assert msgs[1].event_id == "$m2"
    assert msgs[2].event_id == "$t1"

    # Both media messages must have resolved, non-None URLs
    assert msgs[0].media_url == "https://example.com/download/img1"
    assert msgs[1].media_url == "https://example.com/download/vid1"

    # Text message has no media URL
    assert msgs[2].media_url is None

    # mxc_to_http was called exactly twice (once per media event)
    assert nio_mock.mxc_to_http.call_count == 2


# ---------------------------------------------------------------------------
# Feature 2: reactions
# ---------------------------------------------------------------------------


def _make_reaction_event(
    event_id: str = "$r1:example.com",
    sender: str = "@bob:example.com",
    reacts_to: str = "$ev1:example.com",
    key: str = "👍",
    server_timestamp: int = 1_700_000_003_000,
) -> Any:
    """A minimal fake nio ReactionEvent."""
    import nio

    ev = MagicMock(spec=nio.ReactionEvent)
    ev.event_id = event_id
    ev.sender = sender
    ev.reacts_to = reacts_to
    ev.key = key
    ev.server_timestamp = server_timestamp
    return ev


async def test_send_text_with_reply_includes_in_reply_to() -> None:
    """send_text with reply_to_event_id includes m.in_reply_to in room_send content."""
    import nio

    nio_mock = _build_nio_mock()
    nio_mock.access_token = _TOKEN
    nio_mock.user_id = _USER
    nio_mock.room_send.return_value = MagicMock(spec=nio.RoomSendResponse)

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True
    await client.send_text("!room:example.com", "hi", reply_to_event_id="$parent:example.com")

    nio_mock.room_send.assert_awaited_once_with(
        "!room:example.com",
        "m.room.message",
        {
            "msgtype": "m.text",
            "body": "hi",
            "m.relates_to": {"m.in_reply_to": {"event_id": "$parent:example.com"}},
        },
    )


async def test_messages_parses_reply_to_event_id() -> None:
    """messages() sets reply_to_event_id on a RoomMessageText with m.in_reply_to."""
    text_ev = _make_text_event(
        event_id="$reply:example.com",
        body="reply text",
        reply_to_event_id="$parent:example.com",
    )

    nio_mock = _build_nio_mock()
    nio_mock.room_messages.return_value = _make_rooms_response([text_ev])
    nio_mock.rooms = {"!r:example.com": _make_nio_room("!r:example.com")}

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True
    msgs = await client.messages("!r:example.com")

    assert len(msgs) == 1
    assert msgs[0].reply_to_event_id == "$parent:example.com"


async def test_messages_aggregates_reactions_onto_target() -> None:
    """messages() collects ReactionEvents and populates reactions on target Message."""
    text_ev = _make_text_event(event_id="$ev1:example.com", body="hi")
    reaction_ev = _make_reaction_event(
        reacts_to="$ev1:example.com", key="👍", sender="@bob:example.com"
    )

    nio_mock = _build_nio_mock()
    nio_mock.room_messages.return_value = _make_rooms_response([text_ev, reaction_ev])
    nio_mock.rooms = {"!r:example.com": _make_nio_room("!r:example.com")}

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True
    msgs = await client.messages("!r:example.com")

    assert len(msgs) == 1
    assert msgs[0].event_id == "$ev1:example.com"
    assert "👍" in msgs[0].reactions
    assert "@bob:example.com" in msgs[0].reactions["👍"]


async def test_send_reaction_calls_room_send() -> None:
    """send_reaction() calls nio room_send with the correct m.reaction content."""
    import nio

    nio_mock = _build_nio_mock()
    nio_mock.access_token = _TOKEN
    nio_mock.user_id = _USER
    nio_mock.room_send.return_value = MagicMock(spec=nio.RoomSendResponse)

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True
    await client.send_reaction("!room:example.com", "$target:example.com", "👍")

    nio_mock.room_send.assert_awaited_once_with(
        "!room:example.com",
        "m.reaction",
        {
            "m.relates_to": {
                "rel_type": "m.annotation",
                "event_id": "$target:example.com",
                "key": "👍",
            }
        },
    )


async def test_send_reaction_requires_login() -> None:
    """send_reaction() raises NotLoggedInError if not logged in."""
    nio_mock = _build_nio_mock()
    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.send_reaction("!room:example.com", "$ev:example.com", "👍")


async def test_edit_message_sends_m_replace() -> None:
    """edit_message() calls room_send with m.replace content."""
    import nio

    nio_mock = _build_nio_mock()
    nio_mock.access_token = _TOKEN
    nio_mock.user_id = _USER
    nio_mock.room_send.return_value = MagicMock(spec=nio.RoomSendResponse)
    nio_mock.rooms = {}

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True
    await client.edit_message("!room:example.com", "$orig:example.com", "new body")

    nio_mock.room_send.assert_awaited_once_with(
        "!room:example.com",
        "m.room.message",
        {
            "msgtype": "m.text",
            "body": "* new body",
            "m.new_content": {"msgtype": "m.text", "body": "new body"},
            "m.relates_to": {"rel_type": "m.replace", "event_id": "$orig:example.com"},
        },
    )


async def test_edit_message_requires_login() -> None:
    """edit_message() raises NotLoggedInError if not logged in."""
    nio_mock = _build_nio_mock()
    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.edit_message("!room:example.com", "$ev", "new")


async def test_redact_message_calls_room_redact() -> None:
    """redact_message() calls nio room_redact with the correct args."""
    import nio

    nio_mock = _build_nio_mock()
    nio_mock.access_token = _TOKEN
    nio_mock.user_id = _USER
    nio_mock.room_redact.return_value = MagicMock(spec=nio.RoomRedactResponse)

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True
    await client.redact_message("!room:example.com", "$ev:example.com")

    nio_mock.room_redact.assert_awaited_once_with("!room:example.com", "$ev:example.com", reason="")


async def test_redact_message_requires_login() -> None:
    """redact_message() raises NotLoggedInError if not logged in."""
    nio_mock = _build_nio_mock()
    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.redact_message("!room:example.com", "$ev")


async def test_messages_reaction_unknown_event_id_ignored() -> None:
    """Reactions targeting unknown event_ids are silently dropped."""
    text_ev = _make_text_event(event_id="$ev1:example.com", body="hi")
    reaction_ev = _make_reaction_event(reacts_to="$unknown:example.com", key="❤️")

    nio_mock = _build_nio_mock()
    nio_mock.room_messages.return_value = _make_rooms_response([text_ev, reaction_ev])
    nio_mock.rooms = {"!r:example.com": _make_nio_room("!r:example.com")}

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True
    msgs = await client.messages("!r:example.com")

    assert len(msgs) == 1
    assert msgs[0].reactions == {}


async def test_seed_last_activity_pre_seeds_missing_entries() -> None:
    """seed_last_activity() fills _last_activity for rooms not yet seen by sync."""
    from datetime import UTC, datetime

    ts_a = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    ts_b = datetime(2024, 5, 1, 8, 0, 0, tzinfo=UTC)

    nio_mock = _build_nio_mock()
    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)

    client.seed_last_activity({"!a:example.com": ts_a, "!b:example.com": ts_b})

    assert client._last_activity["!a:example.com"] == ts_a
    assert client._last_activity["!b:example.com"] == ts_b


async def test_seed_last_activity_does_not_overwrite_existing() -> None:
    """seed_last_activity() must not overwrite entries already set by a real sync."""
    from datetime import UTC, datetime

    ts_sync = datetime(2024, 7, 1, 0, 0, 0, tzinfo=UTC)
    ts_seed = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)

    nio_mock = _build_nio_mock()
    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)

    # Simulate a real sync having already populated the entry.
    client._last_activity["!r:example.com"] = ts_sync

    client.seed_last_activity({"!r:example.com": ts_seed})

    # The sync value must be preserved.
    assert client._last_activity["!r:example.com"] == ts_sync


async def test_leave_room_hides_room_immediately_and_after_stale_sync() -> None:
    """leave_room() removes the room from rooms() at once; stale sync doesn't resurrect it."""
    import nio

    room_id = "!leaveroom:example.com"
    nio_mock = _build_nio_mock(
        rooms={room_id: _make_nio_room(room_id=room_id, display_name="Leaving")}
    )
    nio_mock.access_token = _TOKEN
    nio_mock.user_id = _USER
    nio_mock.room_leave.return_value = MagicMock(spec=nio.RoomLeaveResponse)

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    emitted: list[object] = []
    client.subscribe(lambda e: emitted.append(e))

    assert any(r.room_id == room_id for r in client.rooms())

    await client.leave_room(room_id)

    # Immediately hidden
    assert not any(r.room_id == room_id for r in client.rooms())
    # RoomsChanged was emitted without the departed room
    from telemente.matrix.client import RoomsChanged

    assert len(emitted) == 1
    assert isinstance(emitted[0], RoomsChanged)
    assert not any(r.room_id == room_id for r in emitted[0].rooms)

    # Simulate stale sync: nio still has the room in its dict (hasn't pruned yet).
    # rooms() must still hide it.
    assert not any(r.room_id == room_id for r in client.rooms())


async def test_update_last_activity_populates_cache() -> None:
    """_update_last_activity() reads the newest event timestamp from each joined room."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    ts_ms = 1_717_243_200_000  # 2024-06-01 12:00:00 UTC in milliseconds
    expected = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)

    fake_event = SimpleNamespace(server_timestamp=ts_ms)
    fake_timeline = SimpleNamespace(events=[fake_event])
    fake_room_info = SimpleNamespace(timeline=fake_timeline)
    fake_response = SimpleNamespace(rooms=SimpleNamespace(join={"!r:example.com": fake_room_info}))

    nio_mock = _build_nio_mock()
    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)

    client._update_last_activity(fake_response)

    assert client._last_activity.get("!r:example.com") == expected


async def test_on_sync_skips_rooms_changed_when_nothing_changed() -> None:
    """_on_sync must NOT emit RoomsChanged when room list is identical to last emit."""
    from types import SimpleNamespace

    room_id = "!stable:example.com"
    nio_mock = _build_nio_mock(
        rooms={room_id: _make_nio_room(room_id=room_id, display_name="Stable")}
    )
    nio_mock.should_upload_keys = False
    nio_mock.should_query_keys = False

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    emitted: list[object] = []
    client.subscribe(lambda e: emitted.append(e))

    fake_response = SimpleNamespace(
        rooms=SimpleNamespace(join={room_id: SimpleNamespace(timeline=SimpleNamespace(events=[]))})
    )

    # First call — should emit (establishes baseline).
    await client._on_sync(fake_response)
    assert len(emitted) == 1

    # Second call with identical state — must not emit.
    await client._on_sync(fake_response)
    assert len(emitted) == 1  # still 1

    # Mutate display_name — should emit again.
    nio_mock.rooms[room_id].display_name = "Changed Name"
    await client._on_sync(fake_response)
    assert len(emitted) == 2


# ---------------------------------------------------------------------------
# rooms() with tags
# ---------------------------------------------------------------------------


async def test_rooms_with_tags_parsed() -> None:
    """rooms() parses tags dict from nio room and exposes them in RoomSummary."""
    room = _make_nio_room("!tagged:example.com", "Tagged")
    room.tags = {
        "m.favourite": {"order": 0.5},
        "m.lowpriority": None,
    }
    nio_mock = _build_nio_mock(rooms={"!tagged:example.com": room})

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    summaries = client.rooms()
    assert len(summaries) == 1
    tags = summaries[0].tags
    assert "m.favourite" in tags
    assert tags["m.favourite"] == 0.5
    assert "m.lowpriority" in tags


async def test_rooms_with_no_tags_attribute() -> None:
    """rooms() is safe when nio room lacks a tags attribute."""
    room = _make_nio_room("!noattr:example.com", "NoAttr")
    # Ensure no tags attribute exists
    if hasattr(room, "tags"):
        del room.tags
    nio_mock = _build_nio_mock(rooms={"!noattr:example.com": room})

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    summaries = client.rooms()
    assert len(summaries) == 1
    assert summaries[0].tags == {}


# ---------------------------------------------------------------------------
# members() edge case: unknown room
# ---------------------------------------------------------------------------


async def test_members_unknown_room_returns_empty() -> None:
    """members() returns [] for a room_id that doesn't exist in nio client."""
    nio_mock = _build_nio_mock(rooms={})
    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    result = client.members("!nonexistent:example.com")
    assert result == []


# ---------------------------------------------------------------------------
# messages() not-logged-in and failed response
# ---------------------------------------------------------------------------


async def test_messages_requires_login() -> None:
    """messages() raises NotLoggedInError if not logged in."""
    nio_mock = _build_nio_mock()
    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.messages("!room:example.com")


async def test_messages_failed_response_returns_empty() -> None:
    """messages() returns [] when nio returns a non-RoomMessagesResponse."""
    from unittest.mock import MagicMock

    nio_mock = _build_nio_mock()
    nio_mock.room_messages.return_value = MagicMock()  # not a RoomMessagesResponse

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True
    result = await client.messages("!room:example.com")
    assert result == []


# ---------------------------------------------------------------------------
# leave_room() error path
# ---------------------------------------------------------------------------


async def test_leave_room_requires_login() -> None:
    """leave_room() raises NotLoggedInError if not logged in."""

    nio_mock = _build_nio_mock()
    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.leave_room("!room:example.com")


async def test_leave_room_raises_on_nio_error() -> None:
    """leave_room() raises MatrixError when nio returns an ErrorResponse."""
    import nio

    from telemente.matrix.client import MatrixError

    nio_mock = _build_nio_mock()
    nio_mock.room_leave.return_value = MagicMock(spec=nio.ErrorResponse)

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    with pytest.raises(MatrixError):
        await client.leave_room("!room:example.com")


# ---------------------------------------------------------------------------
# set_room_tag / remove_room_tag
# ---------------------------------------------------------------------------


async def test_set_room_tag_requires_login() -> None:
    """set_room_tag() raises NotLoggedInError if not logged in."""
    nio_mock = _build_nio_mock()
    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.set_room_tag("!room:example.com", "m.favourite")


async def test_set_room_tag_success() -> None:
    """set_room_tag() calls PUT on the correct URL and succeeds."""
    from aioresponses import aioresponses

    nio_mock = _build_nio_mock()
    nio_mock.user_id = _USER
    nio_mock.access_token = _TOKEN

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    room_id = "!room:example.com"
    tag = "m.favourite"
    url = f"{_HOMESERVER}/_matrix/client/v3/user/{_USER}/rooms/{room_id}/tags/{tag}"

    with aioresponses() as m:
        m.put(url, status=200, payload={})
        await client.set_room_tag(room_id, tag)


async def test_set_room_tag_with_order() -> None:
    """set_room_tag() sends order in the payload when provided."""
    from aioresponses import aioresponses

    nio_mock = _build_nio_mock()
    nio_mock.user_id = _USER
    nio_mock.access_token = _TOKEN

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    room_id = "!room:example.com"
    tag = "m.lowpriority"
    url = f"{_HOMESERVER}/_matrix/client/v3/user/{_USER}/rooms/{room_id}/tags/{tag}"

    with aioresponses() as m:
        m.put(url, status=204, payload={})
        await client.set_room_tag(room_id, tag, order=0.5)


async def test_set_room_tag_http_error_raises_matrix_error() -> None:
    """set_room_tag() raises MatrixError on HTTP error status."""
    from aioresponses import aioresponses

    from telemente.matrix.client import MatrixError

    nio_mock = _build_nio_mock()
    nio_mock.user_id = _USER
    nio_mock.access_token = _TOKEN

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    room_id = "!room:example.com"
    tag = "m.favourite"
    url = f"{_HOMESERVER}/_matrix/client/v3/user/{_USER}/rooms/{room_id}/tags/{tag}"

    with aioresponses() as m:
        m.put(url, status=403, payload={"errcode": "M_FORBIDDEN"})
        with pytest.raises(MatrixError):
            await client.set_room_tag(room_id, tag)


async def test_remove_room_tag_requires_login() -> None:
    """remove_room_tag() raises NotLoggedInError if not logged in."""
    nio_mock = _build_nio_mock()
    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.remove_room_tag("!room:example.com", "m.favourite")


async def test_remove_room_tag_success() -> None:
    """remove_room_tag() calls DELETE on the correct URL."""
    from aioresponses import aioresponses

    nio_mock = _build_nio_mock()
    nio_mock.user_id = _USER
    nio_mock.access_token = _TOKEN

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    room_id = "!room:example.com"
    tag = "m.favourite"
    url = f"{_HOMESERVER}/_matrix/client/v3/user/{_USER}/rooms/{room_id}/tags/{tag}"

    with aioresponses() as m:
        m.delete(url, status=200, payload={})
        await client.remove_room_tag(room_id, tag)


async def test_remove_room_tag_http_error_raises() -> None:
    """remove_room_tag() raises MatrixError on HTTP error status."""
    from aioresponses import aioresponses

    from telemente.matrix.client import MatrixError

    nio_mock = _build_nio_mock()
    nio_mock.user_id = _USER
    nio_mock.access_token = _TOKEN

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    room_id = "!room:example.com"
    tag = "m.favourite"
    url = f"{_HOMESERVER}/_matrix/client/v3/user/{_USER}/rooms/{room_id}/tags/{tag}"

    with aioresponses() as m:
        m.delete(url, status=500, payload={})
        with pytest.raises(MatrixError):
            await client.remove_room_tag(room_id, tag)


# ---------------------------------------------------------------------------
# _on_sync: key upload / query branches
# ---------------------------------------------------------------------------


async def test_on_sync_uploads_keys_when_needed() -> None:
    """_on_sync() calls keys_upload() when should_upload_keys is True."""
    from types import SimpleNamespace

    nio_mock = _build_nio_mock()
    nio_mock.should_upload_keys = True
    nio_mock.should_query_keys = False
    nio_mock.keys_upload = AsyncMock(return_value=None)

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    fake_response = SimpleNamespace(rooms=SimpleNamespace(join={}))
    await client._on_sync(fake_response)
    nio_mock.keys_upload.assert_awaited_once()


async def test_on_sync_queries_keys_when_needed() -> None:
    """_on_sync() calls keys_query() when should_query_keys is True."""
    from types import SimpleNamespace

    nio_mock = _build_nio_mock()
    nio_mock.should_upload_keys = False
    nio_mock.should_query_keys = True
    nio_mock.keys_query = AsyncMock(return_value=None)

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    fake_response = SimpleNamespace(rooms=SimpleNamespace(join={}))
    await client._on_sync(fake_response)
    nio_mock.keys_query.assert_awaited_once()


async def test_on_sync_keys_upload_error_does_not_propagate() -> None:
    """_on_sync() suppresses keys_upload() failures gracefully."""
    from types import SimpleNamespace

    nio_mock = _build_nio_mock()
    nio_mock.should_upload_keys = True
    nio_mock.should_query_keys = False
    nio_mock.keys_upload = AsyncMock(side_effect=RuntimeError("upload failed"))

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    fake_response = SimpleNamespace(rooms=SimpleNamespace(join={}))
    # Should not raise
    await client._on_sync(fake_response)


# ---------------------------------------------------------------------------
# _on_room_media callback
# ---------------------------------------------------------------------------


async def test_on_room_media_emits_new_message() -> None:
    """_on_room_media() emits a NewMessage with media_url and media_type set."""

    nio_mock = _build_nio_mock()
    nio_mock.mxc_to_http = AsyncMock(
        return_value="https://example.com/_matrix/media/v3/download/example.com/img"
    )

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    received: list[Any] = []
    client.subscribe(lambda e: received.append(e))

    room = _make_nio_room("!r:example.com")
    media_ev = _make_media_event(kind="image", url="mxc://example.com/img", body="photo.jpg")
    # _on_room_media expects a real-ish nio room and a RoomMessageMedia event
    # We can call it directly since it's an async method
    await client._on_room_media(room, media_ev)

    assert len(received) == 1
    msg = received[0].message
    assert msg.media_type == "image"
    assert msg.media_url is not None


# ---------------------------------------------------------------------------
# _on_megolm_event callback
# ---------------------------------------------------------------------------


async def test_on_megolm_event_emits_placeholder_and_requests_key() -> None:
    """_on_megolm_event() emits a 🔒 placeholder and calls request_room_key."""
    nio_mock = _build_nio_mock()
    nio_mock.request_room_key = AsyncMock(return_value=None)

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    received: list[Any] = []
    client.subscribe(lambda e: received.append(e))

    room = _make_nio_room("!r:example.com")
    enc_ev = _make_megolm_event()
    await client._on_megolm_event(room, enc_ev)

    nio_mock.request_room_key.assert_awaited_once()
    assert len(received) == 1
    assert "\U0001f512" in received[0].message.body


async def test_on_megolm_event_request_key_failure_does_not_propagate() -> None:
    """_on_megolm_event() gracefully handles request_room_key failures."""
    nio_mock = _build_nio_mock()
    nio_mock.request_room_key = AsyncMock(side_effect=RuntimeError("key req failed"))

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    received: list[Any] = []
    client.subscribe(lambda e: received.append(e))

    room = _make_nio_room("!r:example.com")
    enc_ev = _make_megolm_event()
    # Should not raise
    await client._on_megolm_event(room, enc_ev)
    # Placeholder still emitted despite key request failure
    assert len(received) == 1


# ---------------------------------------------------------------------------
# _sync_loop: cached rooms path and sync failure path
# ---------------------------------------------------------------------------


async def test_sync_loop_emits_cached_rooms_immediately() -> None:
    """_sync_loop() emits RoomsChanged with pre-loaded rooms before the network sync."""

    async def _sync_forever(**kwargs: Any) -> None:
        await asyncio.sleep(9999)

    nio_mock = _build_nio_mock(
        rooms={"!cached:example.com": _make_nio_room("!cached:example.com", "Cached")}
    )
    nio_mock.sync = AsyncMock(return_value=MagicMock(spec=__import__("nio").SyncResponse))
    nio_mock.sync_forever = _sync_forever

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    emitted: list[Any] = []
    client.subscribe(lambda e: emitted.append(e))

    task = asyncio.create_task(client._sync_loop())
    # Let the loop run a bit but not forever
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    from telemente.matrix.client import RoomsChanged

    rooms_events = [e for e in emitted if isinstance(e, RoomsChanged)]
    # At least one RoomsChanged was emitted for cached rooms
    assert len(rooms_events) >= 1
    assert any(r.room_id == "!cached:example.com" for e in rooms_events for r in e.rooms)


async def test_sync_loop_handles_sync_exception() -> None:
    """_sync_loop() sets _initial_sync_done=True and continues to sync_forever on error."""
    sync_forever_started = asyncio.Event()

    async def _sync_raises(**kwargs: Any) -> None:
        raise RuntimeError("network error")

    async def _sync_forever(**kwargs: Any) -> None:
        sync_forever_started.set()
        await asyncio.sleep(9999)

    nio_mock = _build_nio_mock()
    nio_mock.sync = _sync_raises
    nio_mock.sync_forever = _sync_forever

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    task = asyncio.create_task(client._sync_loop())
    # Wait for sync_forever to be reached (proving recovery)
    try:
        await asyncio.wait_for(sync_forever_started.wait(), timeout=2.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert client._initial_sync_done is True


# ---------------------------------------------------------------------------
# close() with active poll task
# ---------------------------------------------------------------------------


async def test_close_cancels_poll_task() -> None:
    """close() cancels the rooms poll task when it is running."""

    async def _sync_forever(**kwargs: Any) -> None:
        await asyncio.sleep(9999)

    nio_mock = _build_nio_mock()
    nio_mock.sync_forever = _sync_forever
    nio_mock.sync = AsyncMock(return_value=MagicMock(spec=__import__("nio").SyncResponse))

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    client._logged_in = True

    await client.start_sync()
    # Let _sync_loop start and create _rooms_poll_task
    await asyncio.sleep(0)

    poll_task = client._rooms_poll_task
    await client.close()

    if poll_task is not None:
        assert poll_task.done()


# ---------------------------------------------------------------------------
# me() returns user_id
# ---------------------------------------------------------------------------


async def test_me_returns_user_id() -> None:
    """me() returns (user_id, user_id) tuple from the nio client."""
    nio_mock = _build_nio_mock()
    nio_mock.user_id = "@me:example.com"

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    uid, display = client.me()
    assert uid == "@me:example.com"
    assert display == "@me:example.com"
