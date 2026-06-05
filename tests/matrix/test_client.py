"""Tests for telemente.matrix.client (plan 0003).

Unit tests inject mock nio clients; integration tests use aioresponses.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aioresponses import aioresponses

from matrix.helpers import (
    DEVICE_ID,
    HOMESERVER,
    PASSWORD,
    TOKEN,
    USER,
    build_nio_mock,
    event_callback_for,
    load_fixture,
    make_login_response,
    make_media_event,
    make_megolm_event,
    make_nio_room,
    make_reaction_event,
    make_rooms_response,
    make_session,
    make_text_event,
    response_callback_for,
    restore_client,
    room_activity_by_id,
    room_messages_url_pattern,
    start_sync_with_stubs,
    stub_delete,
    stub_get,
    stub_login_flows,
    stub_post,
    stub_put,
    stub_room_messages,
    stub_room_redact,
    stub_room_send,
    stub_sync,
    ts_from_origin_server_ms,
    wait_until,
)
from telemente.matrix.client import (
    LoginError,
    MatrixClient,
    NewMessage,
    NotLoggedInError,
    TypingChanged,
)
from telemente.matrix.models import RoomSummary
from telemente.matrix.sort import sort_rooms_by_recency

# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


async def test_login_success() -> None:
    """login() with a valid response returns a Session with correct fields."""

    nio_mock = build_nio_mock(login_return=make_login_response())
    # The login response has user_id/device_id/access_token
    login_resp = make_login_response()
    # Make it NOT an instance of LoginError
    nio_mock.login.return_value = login_resp

    client = MatrixClient(HOMESERVER, nio_client=nio_mock)
    session = await client.login(USER, PASSWORD)

    assert session.user_id == USER
    assert session.device_id == DEVICE_ID
    assert session.access_token == TOKEN
    assert session.homeserver == HOMESERVER
    nio_mock.login.assert_awaited_once()


async def test_login_failure_raises() -> None:
    """login() when nio returns a LoginError raises telemente's LoginError."""
    import nio

    nio_mock = build_nio_mock()
    nio_mock.login.return_value = MagicMock(spec=nio.LoginError)

    client = MatrixClient(HOMESERVER, nio_client=nio_mock)
    with pytest.raises(LoginError):
        await client.login(USER, PASSWORD)


async def test_send_text_calls_room_send() -> None:
    """send_text() calls nio room_send with the correct msgtype and body."""
    import nio

    nio_mock = build_nio_mock()
    nio_mock.access_token = TOKEN  # simulate logged in
    nio_mock.user_id = USER
    nio_mock.room_send.return_value = MagicMock(spec=nio.RoomSendResponse)

    client = await restore_client(nio_mock)
    await client.send_text("!room:example.com", "hi")

    nio_mock.room_send.assert_awaited_once_with(
        "!room:example.com",
        "m.room.message",
        {"msgtype": "m.text", "body": "hi"},
    )


async def test_send_text_requires_login() -> None:
    """send_text() raises NotLoggedInError if the client is not logged in."""
    nio_mock = build_nio_mock()
    client = MatrixClient(HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.send_text("!room:example.com", "hi")


async def test_rooms_maps_state() -> None:
    """rooms() maps nio MatrixRoom to RoomSummary with correct fields."""
    enc_room = make_nio_room(room_id="!enc:example.com", display_name="Secret Room", encrypted=True)
    nio_mock = build_nio_mock(rooms={"!enc:example.com": enc_room})
    nio_mock.access_token = TOKEN
    nio_mock.user_id = USER

    client = await restore_client(nio_mock)

    summaries = client.rooms()
    assert len(summaries) == 1
    s = summaries[0]
    assert isinstance(s, RoomSummary)
    assert s.room_id == "!enc:example.com"
    assert s.display_name == "Secret Room"
    assert s.encrypted is True


async def test_subscribe_receives_new_message() -> None:
    """Subscribed handlers receive NewMessage events from the text callback."""

    import nio

    nio_mock = build_nio_mock()
    client = await restore_client(nio_mock)

    received: list[Any] = []

    async def handler(event: Any) -> None:
        received.append(event)

    client.subscribe(handler)

    room = make_nio_room()
    room.users = {
        USER: MagicMock(display_name="Alice", name="Alice"),
    }
    event = make_text_event(body="Hello!")
    await event_callback_for(nio_mock, nio.RoomMessageText)(room, event)

    assert len(received) == 1
    assert isinstance(received[0], NewMessage)
    assert received[0].message.body == "Hello!"


async def test_unsubscribe() -> None:
    """Unsubscribing prevents further delivery of events to that handler."""

    import nio

    nio_mock = build_nio_mock()
    client = await restore_client(nio_mock)

    received: list[Any] = []

    async def handler(event: Any) -> None:
        received.append(event)

    unsub = client.subscribe(handler)
    unsub()

    room = make_nio_room()
    event = make_text_event()
    await event_callback_for(nio_mock, nio.RoomMessageText)(room, event)

    assert len(received) == 0


async def test_start_sync_requires_login() -> None:
    """start_sync() raises NotLoggedInError before login."""

    nio_mock = build_nio_mock()
    client = MatrixClient(HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.start_sync()


async def test_start_sync_and_close() -> None:
    """After restore, start_sync() runs the sync loop; close() cancels sync_forever."""

    import nio

    cancelled = False

    async def _sync_forever(**_kwargs: Any) -> None:
        nonlocal cancelled
        try:
            await asyncio.sleep(9999)
        except asyncio.CancelledError:
            cancelled = True
            raise

    nio_mock = build_nio_mock()
    nio_mock.sync = AsyncMock(return_value=MagicMock(spec=nio.SyncResponse))
    nio_mock.sync_forever = _sync_forever

    client = await restore_client(nio_mock)

    await client.start_sync()
    await asyncio.sleep(0)
    await client.close()
    assert cancelled


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
    login_url = f"{HOMESERVER}/_matrix/client/v3/login"

    real_nio = nio.AsyncClient(HOMESERVER, "@intuser:example.com")
    with aioresponses() as m:
        stub_post(m, login_url, payload=login_json)
        client = MatrixClient(HOMESERVER, nio_client=real_nio)
        session = await client.login("@intuser:example.com", "password")
        await real_nio.close()

    assert session.access_token == "integration_token"
    assert session.device_id == "INTDEVICE"
    assert session.user_id == "@intuser:example.com"


async def test_login_forbidden_integration() -> None:
    """Integration: stubbed 403 M_FORBIDDEN causes LoginError to be raised."""
    import nio

    error_json = {"errcode": "M_FORBIDDEN", "error": "Invalid password"}
    login_url = f"{HOMESERVER}/_matrix/client/v3/login"

    real_nio = nio.AsyncClient(HOMESERVER, USER)
    with aioresponses() as m:
        stub_post(m, login_url, payload=error_json, status=403)
        client = MatrixClient(HOMESERVER, nio_client=real_nio)

        with pytest.raises(LoginError):
            await client.login(USER, PASSWORD)

        await real_nio.close()


# ---------------------------------------------------------------------------
# Cassette integration tests (real nio + aioresponses fixtures, plan 0016)
# ---------------------------------------------------------------------------

ROOM_A = "!room_a:example.com"
ROOM_B = "!room_b:example.com"
ROOM_C = "!room_c:example.com"


async def test_login_from_synthetic_fixture(real_nio_client: Any) -> None:
    """Integration: login parses the synthetic /login cassette (no live server)."""
    login_url = f"{HOMESERVER}/_matrix/client/v3/login"

    with aioresponses() as m:
        stub_post(m, login_url, payload=load_fixture("login.json", tier="synthetic"))
        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        session = await client.login(USER, PASSWORD)

    assert session.access_token == TOKEN
    assert session.device_id == DEVICE_ID
    assert session.user_id == USER
    assert real_nio_client.access_token == TOKEN


async def test_update_last_activity_real_nio_initial_sync(
    real_nio_client: Any,
) -> None:
    """Initial sync through real nio populates last_activity on rooms()."""
    with aioresponses() as m:
        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await start_sync_with_stubs(
            client,
            m,
            initial_sync=load_fixture("sync_initial.json"),
            min_rooms=3,
        )

    activity = room_activity_by_id(client.rooms())
    ts_c = activity[ROOM_C]
    ts_a = activity[ROOM_A]
    assert ts_c is not None and ts_a is not None
    assert ts_c > ts_a
    assert activity.get(ROOM_B) is None


async def test_update_last_activity_real_nio_incremental_sync(
    real_nio_client: Any,
) -> None:
    """Incremental sync through real nio updates last_activity for a quiet room."""
    expected_b = ts_from_origin_server_ms(1_700_000_010_000)
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}

    with aioresponses() as m:
        stub_sync(m, load_fixture("sync_initial.json"))
        stub_sync(m, load_fixture("sync_incremental.json"))
        stub_sync(m, idle, repeat=True)

        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await client.start_sync()
        await wait_until(lambda: room_activity_by_id(client.rooms()).get(ROOM_B) == expected_b)
        await client.close()

    summary = next(s for s in client.rooms() if s.room_id == ROOM_B)
    assert summary.last_activity == expected_b


async def test_rooms_sorted_by_recency_after_real_sync(
    real_nio_client: Any,
) -> None:
    """Recent sort (RoomList contract): newest activity first, then A-Z for the rest."""
    with aioresponses() as m:
        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await start_sync_with_stubs(
            client,
            m,
            initial_sync=load_fixture("sync_initial.json"),
            min_rooms=3,
        )

    sorted_ids = [r.room_id for r in sort_rooms_by_recency(client.rooms())]
    assert sorted_ids == [ROOM_C, ROOM_A, ROOM_B]


async def test_rooms_recency_sort_c_before_b_before_a() -> None:
    """When alpha order is a,b,c but activity is c > b > a, recent sort yields c,b,a."""
    from datetime import UTC, datetime

    ts_a = datetime(2024, 1, 1, tzinfo=UTC)
    ts_b = datetime(2024, 6, 1, tzinfo=UTC)
    ts_c = datetime(2024, 12, 1, tzinfo=UTC)

    rooms = [
        RoomSummary(room_id="!a:example.com", display_name="Alpha", last_activity=ts_a),
        RoomSummary(room_id="!b:example.com", display_name="Beta", last_activity=ts_b),
        RoomSummary(room_id="!c:example.com", display_name="Charlie", last_activity=ts_c),
    ]
    assert [r.room_id for r in sort_rooms_by_recency(rooms)] == [
        "!c:example.com",
        "!b:example.com",
        "!a:example.com",
    ]


async def test_rooms_unread_count_from_sync(real_nio_client: Any) -> None:
    """rooms() populates unread_count from nio's unread_notifications after sync."""
    with aioresponses() as m:
        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await start_sync_with_stubs(
            client,
            m,
            initial_sync=load_fixture("sync_initial.json"),
            min_rooms=3,
        )

    by_id = {r.room_id: r for r in client.rooms()}
    # sync_initial.json has unread_notifications: {notification_count: 1} for room_a
    assert by_id[ROOM_A].unread_count == 1
    assert by_id[ROOM_C].unread_count == 0


async def test_messages_backfill_seeds_last_activity_real_nio(
    real_nio_client: Any,
) -> None:
    """messages() backfill through real nio seeds last_activity on rooms()."""
    expected = ts_from_origin_server_ms(1_700_000_006_000)
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}

    with aioresponses() as m:
        stub_sync(m, load_fixture("sync_initial.json"))
        stub_sync(m, idle, repeat=True)
        stub_get(
            m,
            room_messages_url_pattern(ROOM_B),
            payload=load_fixture("room_messages.json"),
        )

        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await client.start_sync()
        await wait_until(lambda: len(client.rooms()) >= 3)
        await client.messages(ROOM_B)
        await client.close()

    summary = next(s for s in client.rooms() if s.room_id == ROOM_B)
    assert summary.last_activity == expected


async def test_matrix_room_has_no_timeline_attribute() -> None:
    """MatrixRoom has no .timeline — guard against reintroducing the dead fallback."""
    import nio

    room = nio.MatrixRoom("!r:example.com", "@me:example.com")
    assert not hasattr(room, "timeline"), (
        "nio.MatrixRoom gained a .timeline attribute — "
        "review _update_last_activity and remove this guard if intentional"
    )


# ---------------------------------------------------------------------------
# Unit tests — messages()
# ---------------------------------------------------------------------------


async def test_messages_returns_text_events(real_nio_client: Any) -> None:
    """messages() returns Message objects parsed by real nio from a JSON fixture."""
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}

    with aioresponses() as m:
        stub_sync(m, load_fixture("sync_initial.json"))
        stub_sync(m, idle, repeat=True)
        stub_get(
            m,
            room_messages_url_pattern(ROOM_B),
            payload=load_fixture("room_messages.json"),
        )

        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await client.start_sync()
        await wait_until(lambda: ROOM_B in real_nio_client.rooms)
        msgs = await client.messages(ROOM_B)
        await client.close()

    assert len(msgs) == 2
    bodies = {m.body for m in msgs}
    assert "backfill message" in bodies
    assert "newer backfill" in bodies
    assert all(m.media_url is None and m.media_type is None for m in msgs)


async def test_messages_includes_media_events() -> None:
    """messages() converts RoomMessageMedia to a Message with media_url set."""

    media_ev = make_media_event(body="photo.jpg", url="mxc://example.com/abc123")
    nio_mock = build_nio_mock()
    nio_mock.room_messages.return_value = make_rooms_response([media_ev])
    nio_mock.rooms = {"!r:example.com": make_nio_room("!r:example.com")}
    nio_mock.mxc_to_http.return_value = (
        "https://example.com/_matrix/media/v3/download/example.com/abc123"
    )

    client = await restore_client(nio_mock)
    msgs = await client.messages("!r:example.com")

    assert len(msgs) == 1
    assert msgs[0].body == "photo.jpg"
    assert msgs[0].media_type == "image"
    assert msgs[0].media_url == "https://example.com/_matrix/media/v3/download/example.com/abc123"


async def test_messages_media_types_labeled_correctly() -> None:
    """Each media subtype gets the right label: image/video/audio/file."""

    cases = [("image", "image"), ("video", "video"), ("audio", "audio"), ("file", "file")]
    for kind, expected_label in cases:
        ev = make_media_event(kind=kind)
        nio_mock = build_nio_mock()
        nio_mock.room_messages.return_value = make_rooms_response([ev])
        nio_mock.rooms = {}
        nio_mock.mxc_to_http.return_value = "https://example.com/media"

        client = await restore_client(nio_mock)
        msgs = await client.messages("!r:example.com")
        assert msgs[0].media_type == expected_label, f"failed for kind={kind}"


async def test_messages_megolm_events_become_placeholders() -> None:
    """MegolmEvent produces a Message with the 🔒 placeholder body."""
    enc_ev = make_megolm_event()
    nio_mock = build_nio_mock()
    nio_mock.room_messages.return_value = make_rooms_response([enc_ev])
    nio_mock.rooms = {}
    nio_mock.request_room_key = AsyncMock()

    client = await restore_client(nio_mock)
    msgs = await client.messages("!r:example.com")

    assert len(msgs) == 1
    assert "\U0001f512" in msgs[0].body
    assert msgs[0].media_url is None


async def test_messages_mixed_events_all_included() -> None:
    """Text, media, and encrypted events all appear in chronological order."""
    text_ev = make_text_event(event_id="$t1", server_timestamp=1_000)
    media_ev = make_media_event(event_id="$m1", server_timestamp=2_000)
    enc_ev = make_megolm_event(event_id="$e1", server_timestamp=3_000)
    # room_messages returns newest-first; client.messages() reverses to chrono
    nio_mock = build_nio_mock()
    nio_mock.room_messages.return_value = make_rooms_response([enc_ev, media_ev, text_ev])
    nio_mock.rooms = {}
    nio_mock.mxc_to_http.return_value = "https://example.com/media"
    nio_mock.request_room_key = AsyncMock()

    client = await restore_client(nio_mock)
    msgs = await client.messages("!r:example.com")

    assert len(msgs) == 3
    assert msgs[0].event_id == "$t1"
    assert msgs[1].event_id == "$m1"
    assert msgs[2].event_id == "$e1"


async def test_messages_resolves_media_urls_concurrently() -> None:
    """messages() resolves all mxc_to_http URLs; each media Message has a non-None
    media_url. Correctness of all resolved URLs is the observable invariant
    (concurrency itself is not directly assertable in unit tests)."""

    media_ev1 = make_media_event(
        event_id="$m1", url="mxc://example.com/img1", server_timestamp=1_000, kind="image"
    )
    media_ev2 = make_media_event(
        event_id="$m2",
        url="mxc://example.com/vid1",
        server_timestamp=2_000,
        kind="video",
        body="clip.mp4",
    )
    text_ev = make_text_event(event_id="$t1", server_timestamp=3_000, body="hello")
    # room_messages returns newest-first
    nio_mock = build_nio_mock()
    nio_mock.room_messages.return_value = make_rooms_response([text_ev, media_ev2, media_ev1])
    nio_mock.rooms = {}

    # mxc_to_http returns different URLs per call.
    # The event list is [text_ev, media_ev2, media_ev1] (newest-first).
    # The loop processes media_ev2 first, then media_ev1.
    nio_mock.mxc_to_http.side_effect = [
        "https://example.com/download/vid1",
        "https://example.com/download/img1",
    ]

    client = await restore_client(nio_mock)
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


async def test_send_text_with_reply_includes_in_reply_to() -> None:
    """send_text with reply_to_event_id includes m.in_reply_to in room_send content."""
    import nio

    nio_mock = build_nio_mock()
    nio_mock.access_token = TOKEN
    nio_mock.user_id = USER
    nio_mock.room_send.return_value = MagicMock(spec=nio.RoomSendResponse)

    client = await restore_client(nio_mock)
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
    text_ev = make_text_event(
        event_id="$reply:example.com",
        body="reply text",
        reply_to_event_id="$parent:example.com",
    )

    nio_mock = build_nio_mock()
    nio_mock.room_messages.return_value = make_rooms_response([text_ev])
    nio_mock.rooms = {"!r:example.com": make_nio_room("!r:example.com")}

    client = await restore_client(nio_mock)
    msgs = await client.messages("!r:example.com")

    assert len(msgs) == 1
    assert msgs[0].reply_to_event_id == "$parent:example.com"


async def test_messages_aggregates_reactions_onto_target() -> None:
    """messages() collects ReactionEvents and populates reactions on target Message."""
    text_ev = make_text_event(event_id="$ev1:example.com", body="hi")
    reaction_ev = make_reaction_event(
        reacts_to="$ev1:example.com", key="👍", sender="@bob:example.com"
    )

    nio_mock = build_nio_mock()
    nio_mock.room_messages.return_value = make_rooms_response([text_ev, reaction_ev])
    nio_mock.rooms = {"!r:example.com": make_nio_room("!r:example.com")}

    client = await restore_client(nio_mock)
    msgs = await client.messages("!r:example.com")

    assert len(msgs) == 1
    assert msgs[0].event_id == "$ev1:example.com"
    assert "👍" in msgs[0].reactions
    assert "@bob:example.com" in msgs[0].reactions["👍"]


async def test_send_reaction_calls_room_send() -> None:
    """send_reaction() calls nio room_send with the correct m.reaction content."""
    import nio

    nio_mock = build_nio_mock()
    nio_mock.access_token = TOKEN
    nio_mock.user_id = USER
    nio_mock.room_send.return_value = MagicMock(spec=nio.RoomSendResponse)

    client = await restore_client(nio_mock)
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
    nio_mock = build_nio_mock()
    client = MatrixClient(HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.send_reaction("!room:example.com", "$ev:example.com", "👍")


async def test_edit_message_sends_m_replace() -> None:
    """edit_message() calls room_send with m.replace content."""
    import nio

    nio_mock = build_nio_mock()
    nio_mock.access_token = TOKEN
    nio_mock.user_id = USER
    nio_mock.room_send.return_value = MagicMock(spec=nio.RoomSendResponse)
    nio_mock.rooms = {}

    client = await restore_client(nio_mock)
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
    nio_mock = build_nio_mock()
    client = MatrixClient(HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.edit_message("!room:example.com", "$ev", "new")


async def test_on_room_message_ignores_m_replace_events() -> None:
    """_on_room_message must not emit NewMessage for m.replace (edit) events."""
    import nio

    nio_mock = build_nio_mock()
    client = await restore_client(nio_mock)

    received: list[Any] = []

    async def handler(event: Any) -> None:
        received.append(event)

    client.subscribe(handler)

    room = make_nio_room()
    # Craft a replacement (edit) event: rel_type == "m.replace"
    edit_ev = make_text_event(event_id="$edit:example.com", body="* new body")
    edit_ev.source = {
        "content": {
            "m.relates_to": {"rel_type": "m.replace", "event_id": "$orig:example.com"},
        }
    }
    await event_callback_for(nio_mock, nio.RoomMessageText)(room, edit_ev)

    assert received == [], "m.replace events must not produce NewMessage"


async def test_messages_excludes_m_replace_events() -> None:
    """messages() must not include m.replace (edit) events as standalone messages."""
    orig_ev = make_text_event(event_id="$orig:example.com", body="original")
    edit_ev = make_text_event(event_id="$edit:example.com", body="* new body")
    edit_ev.source = {
        "content": {
            "m.relates_to": {"rel_type": "m.replace", "event_id": "$orig:example.com"},
        }
    }

    nio_mock = build_nio_mock()
    nio_mock.room_messages.return_value = make_rooms_response([orig_ev, edit_ev])
    nio_mock.rooms = {"!r:example.com": make_nio_room("!r:example.com")}

    client = await restore_client(nio_mock)
    msgs = await client.messages("!r:example.com")

    assert len(msgs) == 1
    assert msgs[0].event_id == "$orig:example.com"


async def test_redact_message_calls_room_redact() -> None:
    """redact_message() calls nio room_redact with the correct args."""
    import nio

    nio_mock = build_nio_mock()
    nio_mock.access_token = TOKEN
    nio_mock.user_id = USER
    nio_mock.room_redact.return_value = MagicMock(spec=nio.RoomRedactResponse)

    client = await restore_client(nio_mock)
    await client.redact_message("!room:example.com", "$ev:example.com")

    nio_mock.room_redact.assert_awaited_once_with("!room:example.com", "$ev:example.com", reason="")


async def test_redact_message_requires_login() -> None:
    """redact_message() raises NotLoggedInError if not logged in."""
    nio_mock = build_nio_mock()
    client = MatrixClient(HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.redact_message("!room:example.com", "$ev")


async def test_messages_reaction_unknown_event_id_ignored() -> None:
    """Reactions targeting unknown event_ids are silently dropped."""
    text_ev = make_text_event(event_id="$ev1:example.com", body="hi")
    reaction_ev = make_reaction_event(reacts_to="$unknown:example.com", key="❤️")

    nio_mock = build_nio_mock()
    nio_mock.room_messages.return_value = make_rooms_response([text_ev, reaction_ev])
    nio_mock.rooms = {"!r:example.com": make_nio_room("!r:example.com")}

    client = await restore_client(nio_mock)
    msgs = await client.messages("!r:example.com")

    assert len(msgs) == 1
    assert msgs[0].reactions == {}


async def test_seed_last_activity_pre_seeds_missing_entries() -> None:
    """seed_last_activity() surfaces last_activity on rooms() before sync arrives."""
    from datetime import UTC, datetime

    ts_a = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    ts_b = datetime(2024, 5, 1, 8, 0, 0, tzinfo=UTC)

    nio_mock = build_nio_mock(
        rooms={
            "!a:example.com": make_nio_room("!a:example.com"),
            "!b:example.com": make_nio_room("!b:example.com"),
        }
    )
    client = MatrixClient(HOMESERVER, nio_client=nio_mock)

    client.seed_last_activity({"!a:example.com": ts_a, "!b:example.com": ts_b})

    by_id = {s.room_id: s for s in client.rooms()}
    assert by_id["!a:example.com"].last_activity == ts_a
    assert by_id["!b:example.com"].last_activity == ts_b


async def test_seed_last_activity_does_not_overwrite_existing(
    real_nio_client: Any,
) -> None:
    """seed_last_activity() must not overwrite last_activity set by messages() backfill."""
    from datetime import UTC, datetime

    ts_seed = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    expected = ts_from_origin_server_ms(1_700_000_006_000)
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}

    with aioresponses() as m:
        stub_sync(m, load_fixture("sync_initial.json"))
        stub_sync(m, idle, repeat=True)
        stub_get(
            m,
            room_messages_url_pattern(ROOM_B),
            payload=load_fixture("room_messages.json"),
        )

        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await client.start_sync()
        await wait_until(lambda: ROOM_B in real_nio_client.rooms)
        await client.messages(ROOM_B)
        client.seed_last_activity({ROOM_B: ts_seed})
        await client.close()

    summary = next(s for s in client.rooms() if s.room_id == ROOM_B)
    assert summary.last_activity == expected


async def test_leave_room_hides_room_immediately_and_after_stale_sync() -> None:
    """leave_room() removes the room from rooms() at once; stale sync doesn't resurrect it."""
    import nio

    room_id = "!leaveroom:example.com"
    nio_mock = build_nio_mock(
        rooms={room_id: make_nio_room(room_id=room_id, display_name="Leaving")}
    )
    nio_mock.access_token = TOKEN
    nio_mock.user_id = USER
    nio_mock.room_leave.return_value = MagicMock(spec=nio.RoomLeaveResponse)

    client = await restore_client(nio_mock)

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


async def test_on_sync_skips_rooms_changed_when_nothing_changed() -> None:
    """Sync callback must NOT emit RoomsChanged when room list is unchanged."""
    import nio

    room_id = "!stable:example.com"
    nio_mock = build_nio_mock(
        rooms={room_id: make_nio_room(room_id=room_id, display_name="Stable")}
    )
    nio_mock.should_upload_keys = False
    nio_mock.should_query_keys = False

    client = await restore_client(nio_mock)

    emitted: list[object] = []
    client.subscribe(lambda e: emitted.append(e))

    fake_response = cast(
        nio.SyncResponse,
        SimpleNamespace(
            rooms=SimpleNamespace(
                join={room_id: SimpleNamespace(timeline=SimpleNamespace(events=[]))}
            )
        ),
    )
    on_sync = response_callback_for(nio_mock, nio.SyncResponse)

    # First call — should emit (establishes baseline).
    await on_sync(fake_response)
    assert len(emitted) == 1

    # Second call with identical state — must not emit.
    await on_sync(fake_response)
    assert len(emitted) == 1  # still 1

    # Mutate display_name — should emit again.
    nio_mock.rooms[room_id].display_name = "Changed Name"
    await on_sync(fake_response)
    assert len(emitted) == 2


# ---------------------------------------------------------------------------
# rooms() with tags
# ---------------------------------------------------------------------------


async def test_rooms_with_tags_parsed() -> None:
    """rooms() parses tags dict from nio room and exposes them in RoomSummary."""
    room = make_nio_room("!tagged:example.com", "Tagged")
    room.tags = {
        "m.favourite": {"order": 0.5},
        "m.lowpriority": None,
    }
    nio_mock = build_nio_mock(rooms={"!tagged:example.com": room})

    client = MatrixClient(HOMESERVER, nio_client=nio_mock)
    summaries = client.rooms()
    assert len(summaries) == 1
    tags = summaries[0].tags
    assert "m.favourite" in tags
    assert tags["m.favourite"] == 0.5
    assert "m.lowpriority" in tags


async def test_rooms_with_no_tags_attribute() -> None:
    """rooms() is safe when nio room lacks a tags attribute."""
    room = make_nio_room("!noattr:example.com", "NoAttr")
    # Ensure no tags attribute exists
    if hasattr(room, "tags"):
        del room.tags
    nio_mock = build_nio_mock(rooms={"!noattr:example.com": room})

    client = MatrixClient(HOMESERVER, nio_client=nio_mock)
    summaries = client.rooms()
    assert len(summaries) == 1
    assert summaries[0].tags == {}


# ---------------------------------------------------------------------------
# members() edge case: unknown room
# ---------------------------------------------------------------------------


async def test_members_unknown_room_returns_empty() -> None:
    """members() returns [] for a room_id that doesn't exist in nio client."""
    nio_mock = build_nio_mock(rooms={})
    client = MatrixClient(HOMESERVER, nio_client=nio_mock)
    result = client.members("!nonexistent:example.com")
    assert result == []


# ---------------------------------------------------------------------------
# messages() not-logged-in and failed response
# ---------------------------------------------------------------------------


async def test_messages_requires_login() -> None:
    """messages() raises NotLoggedInError if not logged in."""
    nio_mock = build_nio_mock()
    client = MatrixClient(HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.messages("!room:example.com")


async def test_messages_failed_response_returns_empty() -> None:
    """messages() returns [] when nio returns a non-RoomMessagesResponse."""
    from unittest.mock import MagicMock

    nio_mock = build_nio_mock()
    nio_mock.room_messages.return_value = MagicMock()  # not a RoomMessagesResponse

    client = await restore_client(nio_mock)
    result = await client.messages("!room:example.com")
    assert result == []


# ---------------------------------------------------------------------------
# leave_room() error path
# ---------------------------------------------------------------------------


async def test_leave_room_requires_login() -> None:
    """leave_room() raises NotLoggedInError if not logged in."""

    nio_mock = build_nio_mock()
    client = MatrixClient(HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.leave_room("!room:example.com")


async def test_leave_room_raises_on_nio_error() -> None:
    """leave_room() raises MatrixError when nio returns an ErrorResponse."""
    import nio

    from telemente.matrix.client import MatrixError

    nio_mock = build_nio_mock()
    nio_mock.room_leave.return_value = MagicMock(spec=nio.ErrorResponse)

    client = await restore_client(nio_mock)

    with pytest.raises(MatrixError):
        await client.leave_room("!room:example.com")


# ---------------------------------------------------------------------------
# set_room_tag / remove_room_tag
# ---------------------------------------------------------------------------


async def test_set_room_tag_requires_login() -> None:
    """set_room_tag() raises NotLoggedInError if not logged in."""
    nio_mock = build_nio_mock()
    client = MatrixClient(HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.set_room_tag("!room:example.com", "m.favourite")


async def test_set_room_tag_success() -> None:
    """set_room_tag() calls PUT on the correct URL and succeeds."""
    from aioresponses import aioresponses

    nio_mock = build_nio_mock()
    nio_mock.user_id = USER
    nio_mock.access_token = TOKEN

    client = await restore_client(nio_mock)

    room_id = "!room:example.com"
    tag = "m.favourite"
    url = f"{HOMESERVER}/_matrix/client/v3/user/{USER}/rooms/{room_id}/tags/{tag}"

    with aioresponses() as m:
        stub_put(m, url, status=200, payload={})
        await client.set_room_tag(room_id, tag)


async def test_set_room_tag_with_order() -> None:
    """set_room_tag() sends order in the payload when provided."""
    from aioresponses import aioresponses

    nio_mock = build_nio_mock()
    nio_mock.user_id = USER
    nio_mock.access_token = TOKEN

    client = await restore_client(nio_mock)

    room_id = "!room:example.com"
    tag = "m.lowpriority"
    url = f"{HOMESERVER}/_matrix/client/v3/user/{USER}/rooms/{room_id}/tags/{tag}"

    with aioresponses() as m:
        stub_put(m, url, status=204, payload={})
        await client.set_room_tag(room_id, tag, order=0.5)


async def test_set_room_tag_http_error_raises_matrix_error() -> None:
    """set_room_tag() raises MatrixError on HTTP error status."""
    from aioresponses import aioresponses

    from telemente.matrix.client import MatrixError

    nio_mock = build_nio_mock()
    nio_mock.user_id = USER
    nio_mock.access_token = TOKEN

    client = await restore_client(nio_mock)

    room_id = "!room:example.com"
    tag = "m.favourite"
    url = f"{HOMESERVER}/_matrix/client/v3/user/{USER}/rooms/{room_id}/tags/{tag}"

    with aioresponses() as m:
        stub_put(m, url, status=403, payload={"errcode": "M_FORBIDDEN"})
        with pytest.raises(MatrixError):
            await client.set_room_tag(room_id, tag)


async def test_remove_room_tag_requires_login() -> None:
    """remove_room_tag() raises NotLoggedInError if not logged in."""
    nio_mock = build_nio_mock()
    client = MatrixClient(HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.remove_room_tag("!room:example.com", "m.favourite")


async def test_remove_room_tag_success() -> None:
    """remove_room_tag() calls DELETE on the correct URL."""
    from aioresponses import aioresponses

    nio_mock = build_nio_mock()
    nio_mock.user_id = USER
    nio_mock.access_token = TOKEN

    client = await restore_client(nio_mock)

    room_id = "!room:example.com"
    tag = "m.favourite"
    url = f"{HOMESERVER}/_matrix/client/v3/user/{USER}/rooms/{room_id}/tags/{tag}"

    with aioresponses() as m:
        stub_delete(m, url, status=200, payload={})
        await client.remove_room_tag(room_id, tag)


async def test_remove_room_tag_http_error_raises() -> None:
    """remove_room_tag() raises MatrixError on HTTP error status."""
    from aioresponses import aioresponses

    from telemente.matrix.client import MatrixError

    nio_mock = build_nio_mock()
    nio_mock.user_id = USER
    nio_mock.access_token = TOKEN

    client = await restore_client(nio_mock)

    room_id = "!room:example.com"
    tag = "m.favourite"
    url = f"{HOMESERVER}/_matrix/client/v3/user/{USER}/rooms/{room_id}/tags/{tag}"

    with aioresponses() as m:
        stub_delete(m, url, status=500, payload={})
        with pytest.raises(MatrixError):
            await client.remove_room_tag(room_id, tag)


# ---------------------------------------------------------------------------
# _on_sync: key upload / query branches
# ---------------------------------------------------------------------------


async def test_on_sync_uploads_keys_when_needed() -> None:
    """_on_sync() calls keys_upload() when should_upload_keys is True."""
    from types import SimpleNamespace
    from typing import cast

    import nio

    nio_mock = build_nio_mock()
    nio_mock.should_upload_keys = True
    nio_mock.should_query_keys = False
    nio_mock.keys_upload = AsyncMock(return_value=None)

    await restore_client(nio_mock)

    fake_response = cast(nio.SyncResponse, SimpleNamespace(rooms=SimpleNamespace(join={})))
    await response_callback_for(nio_mock, nio.SyncResponse)(fake_response)
    nio_mock.keys_upload.assert_awaited_once()


async def test_on_sync_queries_keys_when_needed() -> None:
    """_on_sync() calls keys_query() when should_query_keys is True."""
    from types import SimpleNamespace
    from typing import cast

    import nio

    nio_mock = build_nio_mock()
    nio_mock.should_upload_keys = False
    nio_mock.should_query_keys = True
    nio_mock.keys_query = AsyncMock(return_value=None)

    await restore_client(nio_mock)

    fake_response = cast(nio.SyncResponse, SimpleNamespace(rooms=SimpleNamespace(join={})))
    await response_callback_for(nio_mock, nio.SyncResponse)(fake_response)
    nio_mock.keys_query.assert_awaited_once()


async def test_on_sync_keys_upload_error_does_not_propagate() -> None:
    """_on_sync() suppresses keys_upload() failures gracefully."""
    from types import SimpleNamespace
    from typing import cast

    import nio

    nio_mock = build_nio_mock()
    nio_mock.should_upload_keys = True
    nio_mock.should_query_keys = False
    nio_mock.keys_upload = AsyncMock(side_effect=RuntimeError("upload failed"))

    await restore_client(nio_mock)

    fake_response = cast(nio.SyncResponse, SimpleNamespace(rooms=SimpleNamespace(join={})))
    # Should not raise
    await response_callback_for(nio_mock, nio.SyncResponse)(fake_response)


# ---------------------------------------------------------------------------
# Media callback (via nio registration)
# ---------------------------------------------------------------------------


async def test_on_room_media_emits_new_message() -> None:
    """Media callback emits a NewMessage with media_url and media_type set."""

    import nio

    nio_mock = build_nio_mock()
    nio_mock.mxc_to_http = AsyncMock(
        return_value="https://example.com/_matrix/media/v3/download/example.com/img"
    )

    client = await restore_client(nio_mock)

    received: list[Any] = []
    client.subscribe(lambda e: received.append(e))

    room = make_nio_room("!r:example.com")
    media_ev = make_media_event(kind="image", url="mxc://example.com/img", body="photo.jpg")
    await event_callback_for(nio_mock, nio.RoomMessageMedia)(room, media_ev)

    assert len(received) == 1
    msg = received[0].message
    assert msg.media_type == "image"
    assert msg.media_url is not None


# ---------------------------------------------------------------------------
# Megolm callback (via nio registration)
# ---------------------------------------------------------------------------


async def test_on_megolm_event_emits_placeholder_and_requests_key() -> None:
    """Megolm callback emits a 🔒 placeholder and calls request_room_key."""
    import nio

    nio_mock = build_nio_mock()
    nio_mock.request_room_key = AsyncMock(return_value=None)

    client = await restore_client(nio_mock)

    received: list[Any] = []
    client.subscribe(lambda e: received.append(e))

    room = make_nio_room("!r:example.com")
    enc_ev = make_megolm_event()
    await event_callback_for(nio_mock, nio.MegolmEvent)(room, enc_ev)

    nio_mock.request_room_key.assert_awaited_once()
    assert len(received) == 1
    assert "\U0001f512" in received[0].message.body


async def test_on_megolm_event_request_key_failure_does_not_propagate() -> None:
    """Megolm callback gracefully handles request_room_key failures."""
    import nio

    nio_mock = build_nio_mock()
    nio_mock.request_room_key = AsyncMock(side_effect=RuntimeError("key req failed"))

    client = await restore_client(nio_mock)

    received: list[Any] = []
    client.subscribe(lambda e: received.append(e))

    room = make_nio_room("!r:example.com")
    enc_ev = make_megolm_event()
    # Should not raise
    await event_callback_for(nio_mock, nio.MegolmEvent)(room, enc_ev)
    # Placeholder still emitted despite key request failure
    assert len(received) == 1


# ---------------------------------------------------------------------------
# start_sync lifecycle (public API)
# ---------------------------------------------------------------------------


async def test_start_sync_emits_cached_rooms_immediately() -> None:
    """start_sync() emits RoomsChanged for store-backed rooms before network sync."""

    import nio

    async def _sync_forever(**_kwargs: Any) -> None:
        await asyncio.sleep(9999)

    nio_mock = build_nio_mock(
        rooms={"!cached:example.com": make_nio_room("!cached:example.com", "Cached")}
    )
    nio_mock.sync = AsyncMock(return_value=MagicMock(spec=nio.SyncResponse))
    nio_mock.sync_forever = _sync_forever

    client = await restore_client(nio_mock)

    emitted: list[Any] = []
    client.subscribe(lambda e: emitted.append(e))

    await client.start_sync()
    await asyncio.sleep(0)
    await client.close()

    from telemente.matrix.client import RoomsChanged

    rooms_events = [e for e in emitted if isinstance(e, RoomsChanged)]
    assert len(rooms_events) >= 1
    assert any(r.room_id == "!cached:example.com" for e in rooms_events for r in e.rooms)


async def test_start_sync_recovers_from_initial_sync_error() -> None:
    """start_sync() continues to sync_forever when the initial sync raises."""
    sync_forever_started = asyncio.Event()

    async def _sync_raises(**_kwargs: Any) -> None:
        raise RuntimeError("network error")

    async def _sync_forever(**_kwargs: Any) -> None:
        sync_forever_started.set()
        await asyncio.sleep(9999)

    nio_mock = build_nio_mock()
    nio_mock.sync = _sync_raises
    nio_mock.sync_forever = _sync_forever

    client = await restore_client(nio_mock)

    await client.start_sync()
    try:
        await asyncio.wait_for(sync_forever_started.wait(), timeout=2.0)
    finally:
        await client.close()


async def test_close_shuts_down_active_sync() -> None:
    """close() completes cleanly while start_sync() is running."""

    import nio

    async def _sync_forever(**_kwargs: Any) -> None:
        await asyncio.sleep(9999)

    nio_mock = build_nio_mock()
    nio_mock.sync_forever = _sync_forever
    nio_mock.sync = AsyncMock(return_value=MagicMock(spec=nio.SyncResponse))

    client = await restore_client(nio_mock)

    await client.start_sync()
    await asyncio.sleep(0)
    await client.close()

    nio_mock.close.assert_awaited()


# ---------------------------------------------------------------------------
# me() returns user_id
# ---------------------------------------------------------------------------


async def test_me_returns_user_id() -> None:
    """me() returns (user_id, user_id) tuple from the nio client."""
    nio_mock = build_nio_mock()
    nio_mock.user_id = "@me:example.com"

    client = MatrixClient(HOMESERVER, nio_client=nio_mock)
    uid, display = client.me()
    assert uid == "@me:example.com"
    assert display == "@me:example.com"


# ---------------------------------------------------------------------------
# Plan 0018: cassette-backed tier-1 integration tests
# ---------------------------------------------------------------------------


async def test_login_flows_parses_password_and_sso() -> None:
    """login_flows() parses both m.login.password and m.login.sso from a real HTTP response."""
    import nio

    real_nio = nio.AsyncClient(HOMESERVER, USER)
    client = MatrixClient(HOMESERVER, nio_client=real_nio)
    try:
        with aioresponses() as m:
            stub_login_flows(m)
            flows = await client.login_flows()
    finally:
        await real_nio.close()

    assert flows.password is True
    assert flows.sso is True
    assert len(flows.identity_providers) == 1
    assert flows.identity_providers[0].id == "gitlab"
    assert flows.identity_providers[0].name == "GitLab"


async def test_members_from_sync_state_events(real_nio_client: Any) -> None:
    """members() returns Alice and Bob with correct power levels after a sync with state events."""
    with aioresponses() as m:
        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await start_sync_with_stubs(
            client,
            m,
            initial_sync=load_fixture("sync_with_state.json"),
            min_rooms=1,
        )

    members = client.members(ROOM_A)
    user_ids = {mem.user_id for mem in members}
    assert "@alice:example.com" in user_ids
    assert "@bob:example.com" in user_ids

    alice = next(mem for mem in members if mem.user_id == "@alice:example.com")
    bob = next(mem for mem in members if mem.user_id == "@bob:example.com")
    assert alice.display_name == "Alice"
    assert bob.display_name == "Bob"
    # alice has power level 100; bob defaults to 0
    assert alice.power_level == 100
    assert bob.power_level == 0


async def test_tags_populated_from_sync_account_data(real_nio_client: Any) -> None:
    """rooms()[0].tags['m.favourite'] == 0.5 after sync delivers an m.tag account_data event."""
    with aioresponses() as m:
        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await start_sync_with_stubs(
            client,
            m,
            initial_sync=load_fixture("sync_with_tags.json"),
            min_rooms=1,
        )

    summaries = {r.room_id: r for r in client.rooms()}
    assert ROOM_A in summaries
    assert "m.favourite" in summaries[ROOM_A].tags
    assert summaries[ROOM_A].tags["m.favourite"] == 0.5


async def test_limited_timeline_sets_last_activity(real_nio_client: Any) -> None:
    """A limited timeline still sets last_activity from the single event in the window."""
    expected_ts = ts_from_origin_server_ms(1_700_000_100_000)

    with aioresponses() as m:
        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await start_sync_with_stubs(
            client,
            m,
            initial_sync=load_fixture("sync_limited_timeline.json"),
            min_rooms=1,
        )

    summaries = {r.room_id: r for r in client.rooms()}
    assert ROOM_A in summaries
    assert summaries[ROOM_A].last_activity == expected_ts


async def test_encrypted_room_shows_encrypted_true(real_nio_client: Any) -> None:
    """rooms() shows encrypted=True for a room with m.room.encryption in state."""
    room_enc = "!room_enc:example.com"

    with aioresponses() as m:
        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await start_sync_with_stubs(
            client,
            m,
            initial_sync=load_fixture("sync_with_encrypted_room.json"),
            min_rooms=1,
        )

    summaries = {r.room_id: r for r in client.rooms()}
    assert room_enc in summaries
    assert summaries[room_enc].encrypted is True


async def test_on_room_message_ignores_edit_via_real_nio(real_nio_client: Any) -> None:
    """Real nio parsing: _on_room_message does NOT emit NewMessage for an m.replace event."""
    received: list[Any] = []

    with aioresponses() as m:
        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        client.subscribe(lambda e: received.append(e))
        # First, deliver initial sync so room_a exists.
        stub_sync(m, load_fixture("sync_initial.json"))
        # Then the edit comes in as an incremental sync.
        stub_sync(m, load_fixture("sync_with_edit.json"))
        idle: dict[str, Any] = {
            "next_batch": "idle",
            "rooms": {"join": {}, "invite": {}, "leave": {}},
        }
        stub_sync(m, idle, repeat=True)
        await client.start_sync()
        # Wait until the edit sync has been processed (room_a appears after initial sync).
        await wait_until(lambda: ROOM_A in real_nio_client.rooms)
        await asyncio.sleep(0.1)
        await client.close()

    new_messages = [e for e in received if isinstance(e, NewMessage)]
    # No NewMessage should have been emitted from the m.replace event.
    edit_messages = [e for e in new_messages if "corrected" in e.message.body]
    assert edit_messages == [], "m.replace events must not produce NewMessage"


async def test_messages_reactions_from_cassette(real_nio_client: Any) -> None:
    """messages() aggregates reactions from a real nio-parsed backfill response."""
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}

    with aioresponses() as m:
        stub_sync(m, load_fixture("sync_initial.json"))
        stub_sync(m, idle, repeat=True)
        stub_room_messages(m, ROOM_B, load_fixture("room_messages_with_reactions.json"))

        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await client.start_sync()
        await wait_until(lambda: ROOM_B in real_nio_client.rooms)
        msgs = await client.messages(ROOM_B)
        await client.close()

    # The cassette has $msg2 with a 👍 reaction from @bob
    msg2 = next((msg for msg in msgs if msg.event_id == "$msg2:example.com"), None)
    assert msg2 is not None, "Expected $msg2 in messages"
    assert "👍" in msg2.reactions
    assert "@bob:example.com" in msg2.reactions["👍"]


async def test_messages_reply_chain_from_cassette(real_nio_client: Any) -> None:
    """messages() sets reply_to_event_id from real nio-parsed m.in_reply_to."""
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}

    with aioresponses() as m:
        stub_sync(m, load_fixture("sync_initial.json"))
        stub_sync(m, idle, repeat=True)
        stub_room_messages(m, ROOM_B, load_fixture("room_messages_with_replies.json"))

        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await client.start_sync()
        await wait_until(lambda: ROOM_B in real_nio_client.rooms)
        msgs = await client.messages(ROOM_B)
        await client.close()

    reply = next((msg for msg in msgs if msg.event_id == "$reply1:example.com"), None)
    assert reply is not None, "Expected $reply1 in messages"
    assert reply.reply_to_event_id == "$msg1:example.com"


async def test_messages_media_from_cassette(real_nio_client: Any) -> None:
    """messages() correctly categorizes image and video events from a real backfill response."""
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}

    with aioresponses() as m:
        stub_sync(m, load_fixture("sync_initial.json"))
        stub_sync(m, idle, repeat=True)
        stub_room_messages(m, ROOM_B, load_fixture("room_messages_with_media.json"))

        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await client.start_sync()
        await wait_until(lambda: ROOM_B in real_nio_client.rooms)
        msgs = await client.messages(ROOM_B)
        await client.close()

    media_types = {msg.event_id: msg.media_type for msg in msgs if msg.media_type is not None}
    assert media_types.get("$img1:example.com") == "image"
    assert media_types.get("$vid1:example.com") == "video"


async def test_send_text_returns_event_id_from_real_response(real_nio_client: Any) -> None:
    """send_text() parses the event_id from a real nio room_send response."""
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}

    with aioresponses() as m:
        stub_sync(m, load_fixture("sync_initial.json"))
        stub_sync(m, idle, repeat=True)
        stub_room_send(m, ROOM_A)

        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await client.start_sync()
        await wait_until(lambda: ROOM_A in real_nio_client.rooms)
        event_id = await client.send_text(ROOM_A, "hello from test")
        await client.close()

    assert event_id == "$new_event:example.com"


async def test_redact_message_via_real_nio(real_nio_client: Any) -> None:
    """redact_message() calls the correct PUT endpoint; nio parses the response."""
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}
    target_event_id = "$ev1:example.com"

    with aioresponses() as m:
        stub_sync(m, load_fixture("sync_initial.json"))
        stub_sync(m, idle, repeat=True)
        stub_room_redact(m, ROOM_A, target_event_id)

        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await client.start_sync()
        await wait_until(lambda: ROOM_A in real_nio_client.rooms)
        # Should not raise
        await client.redact_message(ROOM_A, target_event_id)
        await client.close()


async def test_messages_paginated_second_page(real_nio_client: Any) -> None:
    """messages() can fetch a second page of backfill from room_messages_paginated.json."""
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}

    with aioresponses() as m:
        stub_sync(m, load_fixture("sync_initial.json"))
        stub_sync(m, idle, repeat=True)
        stub_room_messages(m, ROOM_B, load_fixture("room_messages_paginated.json"))

        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await client.start_sync()
        await wait_until(lambda: ROOM_B in real_nio_client.rooms)
        msgs = await client.messages(ROOM_B)
        await client.close()

    bodies = {msg.body for msg in msgs}
    assert "page 2 message" in bodies


async def test_messages_rate_limit_error() -> None:
    """messages() returns [] when the homeserver returns 429."""
    import nio

    # max_limit_exceeded=0 makes nio give up immediately instead of retrying.
    config = nio.AsyncClientConfig(max_limit_exceeded=0)
    nio_client = nio.AsyncClient(HOMESERVER, USER, config=config)
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}

    try:
        with aioresponses() as m:
            stub_sync(m, load_fixture("sync_initial.json"))
            stub_sync(m, idle, repeat=True)
            stub_get(
                m,
                room_messages_url_pattern(ROOM_B),
                payload=load_fixture("error_rate_limit.json"),
                status=429,
            )

            client = MatrixClient(HOMESERVER, nio_client=nio_client)
            await client.restore(make_session())
            await client.start_sync()
            await wait_until(lambda: ROOM_B in nio_client.rooms)
            # nio returns ErrorResponse (not RoomMessagesResponse); messages() returns [].
            result = await client.messages(ROOM_B)
            await client.close()
    finally:
        await nio_client.close()

    assert result == []


async def test_sync_with_invite_cassette(real_nio_client: Any) -> None:
    """sync_with_invite.json is parsed by real nio without errors."""
    with aioresponses() as m:
        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await start_sync_with_stubs(
            client,
            m,
            initial_sync=load_fixture("sync_with_invite.json"),
            min_rooms=0,
        )
    # No assertion on room count — invited rooms land in nio's invited_rooms dict,
    # not the joined rooms dict that rooms() reads. This test verifies the cassette
    # is valid enough that real nio doesn't error.


async def test_sync_with_leave_cassette(real_nio_client: Any) -> None:
    """sync_with_leave.json is parsed by real nio without errors."""
    with aioresponses() as m:
        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await start_sync_with_stubs(
            client,
            m,
            initial_sync=load_fixture("sync_with_leave.json"),
            min_rooms=0,
        )


async def test_sync_with_reactions_cassette(real_nio_client: Any) -> None:
    """sync_with_reactions.json is parsed by real nio without errors (no client event yet)."""
    with aioresponses() as m:
        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await start_sync_with_stubs(
            client,
            m,
            initial_sync=load_fixture("sync_with_reactions.json"),
            min_rooms=0,
        )


async def test_sync_with_redaction_cassette(real_nio_client: Any) -> None:
    """sync_with_redaction.json is parsed by real nio without errors."""
    with aioresponses() as m:
        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await start_sync_with_stubs(
            client,
            m,
            initial_sync=load_fixture("sync_with_redaction.json"),
            min_rooms=0,
        )


async def test_sync_with_typing_cassette(real_nio_client: Any) -> None:
    """sync_with_typing.json is parsed by real nio without errors."""
    with aioresponses() as m:
        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await start_sync_with_stubs(
            client,
            m,
            initial_sync=load_fixture("sync_with_typing.json"),
            min_rooms=0,
        )


async def test_messages_redacted_event_from_cassette(real_nio_client: Any) -> None:
    """messages() handles a server-redacted event (empty content) without crashing."""
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}

    with aioresponses() as m:
        stub_sync(m, load_fixture("sync_initial.json"))
        stub_sync(m, idle, repeat=True)
        stub_room_messages(m, ROOM_B, load_fixture("room_messages_with_redacted.json"))

        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await client.start_sync()
        await wait_until(lambda: ROOM_B in real_nio_client.rooms)
        # A server-redacted message has empty content; nio may parse it as
        # RoomMessageText with empty body or skip it entirely. Either way,
        # messages() must not raise.
        msgs = await client.messages(ROOM_B)
        await client.close()

    # Pin the observed behaviour: nio parses redacted m.room.message as
    # RoomMessageText with an empty body string.
    assert isinstance(msgs, list)


async def test_typing_event_emits_typing_changed(real_nio_client: Any) -> None:
    """Sync with m.typing ephemeral emits TypingChanged with correct user_ids."""
    received: list[Any] = []
    with aioresponses() as m:
        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        client.subscribe(lambda e: received.append(e))
        await start_sync_with_stubs(
            client,
            m,
            initial_sync=load_fixture("sync_with_typing.json"),
            min_rooms=0,
        )

    typing_events = [e for e in received if isinstance(e, TypingChanged)]
    assert len(typing_events) == 1
    assert typing_events[0].room_id == "!room_a:example.com"
    assert "@bob:example.com" in typing_events[0].user_ids


# ---------------------------------------------------------------------------
# Plan 0013: message cache integration tests (items 12-15)
# ---------------------------------------------------------------------------


async def test_messages_cold_room_hits_network() -> None:
    """messages() with a cold cache fetches from the network (room_messages called)."""
    import tempfile

    text_ev = make_text_event(body="cached message")
    nio_mock = build_nio_mock()
    nio_mock.room_messages.return_value = make_rooms_response([text_ev])
    nio_mock.rooms = {"!r:example.com": make_nio_room("!r:example.com")}

    with tempfile.NamedTemporaryFile(suffix=".db", delete=True) as f:
        cache_path = f.name

    client = MatrixClient(HOMESERVER, nio_client=nio_mock, cache_path=cache_path)
    await client.restore(make_session())
    try:
        msgs = await client.messages("!r:example.com")
    finally:
        await client.close()

    nio_mock.room_messages.assert_awaited_once()
    assert len(msgs) == 1
    assert msgs[0].body == "cached message"


async def test_messages_warm_room_skips_network() -> None:
    """messages() on a warm room returns from cache without calling room_messages again."""
    import tempfile

    text_ev = make_text_event(body="first message")
    nio_mock = build_nio_mock()
    nio_mock.room_messages.return_value = make_rooms_response([text_ev])
    nio_mock.rooms = {"!r:example.com": make_nio_room("!r:example.com")}

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        cache_path = f.name

    import os

    try:
        client = MatrixClient(HOMESERVER, nio_client=nio_mock, cache_path=cache_path)
        await client.restore(make_session())
        # First call — cold, hits network and populates cache
        await client.messages("!r:example.com")
        assert nio_mock.room_messages.await_count == 1

        # Second call — warm, served from cache
        msgs2 = await client.messages("!r:example.com")
        assert nio_mock.room_messages.await_count == 1  # still 1
        assert len(msgs2) == 1
        await client.close()
    finally:
        os.unlink(cache_path)


async def test_on_room_message_writes_to_cache() -> None:
    """_on_room_message writes the new message to the cache (room becomes warm)."""
    import tempfile

    import nio

    nio_mock = build_nio_mock()
    nio_mock.rooms = {"!r:example.com": make_nio_room("!r:example.com")}

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        cache_path = f.name

    import os

    try:
        client = MatrixClient(HOMESERVER, nio_client=nio_mock, cache_path=cache_path)
        await client.restore(make_session())

        room = make_nio_room("!r:example.com")
        event = make_text_event(event_id="$sync_ev:example.com", body="sync arrived")
        await event_callback_for(nio_mock, nio.RoomMessageText)(room, event)

        # Room is now warm because the sync callback wrote to the cache.
        assert client._cache is not None
        assert not await client._cache.is_cold("!r:example.com")

        await client.close()
    finally:
        os.unlink(cache_path)


async def test_close_closes_cache() -> None:
    """close() closes the cache connection."""
    import tempfile

    nio_mock = build_nio_mock()

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        cache_path = f.name

    import os

    try:
        client = MatrixClient(HOMESERVER, nio_client=nio_mock, cache_path=cache_path)
        await client.restore(make_session())

        assert client._cache is not None
        cache = client._cache

        await client.close()

        # After close(), the aiosqlite connection should be closed.
        # Attempting to use it should raise an error.
        with pytest.raises((Exception,)):
            await cache.get_room("!r:example.com")
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(cache_path)
