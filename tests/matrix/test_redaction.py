"""Tier-1 tests for MessageRedacted ClientEvent and redaction callbacks (plan 0022).

All callback-driven tests use restore_client() + event_callback_for() from helpers.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from matrix.helpers import HOMESERVER, build_nio_mock, restore_client
from telemente.matrix.cache import MessageCache
from telemente.matrix.client import MessageRedacted

# ---------------------------------------------------------------------------
# Test 1: _on_redaction callback emits MessageRedacted
# ---------------------------------------------------------------------------


async def test_redaction_callback_emits_message_redacted() -> None:
    import nio

    from matrix.helpers import event_callback_for

    nio_mock = build_nio_mock()
    client = await restore_client(nio_mock)

    events: list[object] = []
    client.subscribe(lambda e: events.append(e))

    cb = event_callback_for(nio_mock, nio.RedactionEvent)

    room = MagicMock()
    room.room_id = "!r:s"
    room.users = {}

    redaction_event = MagicMock(spec=nio.RedactionEvent)
    redaction_event.event_id = "$r1"
    redaction_event.redacts = "$ev1"
    redaction_event.sender = "@alice:example.com"
    redaction_event.server_timestamp = 1_700_000_000_000

    await cb(room, redaction_event)

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, MessageRedacted)
    assert ev.event_id == "$ev1"
    assert ev.redacted_by == "$r1"
    assert ev.room_id == "!r:s"


# ---------------------------------------------------------------------------
# Test 2: _on_redaction calls cache.mark_redacted
# ---------------------------------------------------------------------------


async def test_redaction_callback_calls_cache_mark_redacted() -> None:
    import nio

    from matrix.helpers import event_callback_for

    nio_mock = build_nio_mock()
    client = await restore_client(nio_mock)

    cache_mock = AsyncMock(spec=MessageCache)
    client._cache = cache_mock  # inject mock cache

    cb = event_callback_for(nio_mock, nio.RedactionEvent)

    room = MagicMock()
    room.room_id = "!r:s"
    room.users = {}

    redaction_event = MagicMock(spec=nio.RedactionEvent)
    redaction_event.event_id = "$r1"
    redaction_event.redacts = "$ev1"
    redaction_event.sender = "@alice:example.com"
    redaction_event.server_timestamp = 1_700_000_000_000

    await cb(room, redaction_event)

    cache_mock.mark_redacted.assert_awaited_once_with("!r:s", "$ev1")


# ---------------------------------------------------------------------------
# Test 3: messages() backfill returns tombstone for RedactedEvent
# ---------------------------------------------------------------------------


async def test_backfill_redacted_event_returns_tombstone() -> None:
    import nio

    from matrix.helpers import make_rooms_response

    nio_mock = build_nio_mock()

    # Build a fake RedactedEvent
    redacted_event = MagicMock(spec=nio.RedactedEvent)
    redacted_event.event_id = "$redacted_msg:example.com"
    redacted_event.sender = "@alice:example.com"
    redacted_event.server_timestamp = 1_700_000_000_000
    redacted_event.type = "m.room.message"
    redacted_event.redacter = "@bob:example.com"
    redacted_event.reason = None
    redacted_event.source = {}

    room = MagicMock()
    room.room_id = "!room:example.com"
    room.users = {}
    nio_mock.rooms = {"!room:example.com": room}

    nio_mock.room_messages.return_value = make_rooms_response([redacted_event])

    client = await restore_client(nio_mock)

    msgs = await client.messages("!room:example.com")

    assert len(msgs) == 1
    assert msgs[0].body == "\U0001f5d1️ Message deleted"
    assert msgs[0].redacted is True
    assert msgs[0].event_id == "$redacted_msg:example.com"


# ---------------------------------------------------------------------------
# Test 4: MessageRedacted event fires during sync (uses sync_with_redaction fixture)
# ---------------------------------------------------------------------------


async def test_redaction_event_fires_during_sync() -> None:
    from aioresponses import aioresponses

    from matrix.helpers import (
        load_fixture,
        make_session,
        start_sync_with_stubs,
    )
    from telemente.matrix.client import MatrixClient

    events: list[object] = []

    fixture = load_fixture("sync_with_redaction.json")
    client = MatrixClient(HOMESERVER)
    await client.restore(make_session(homeserver=HOMESERVER))
    client.subscribe(lambda e: events.append(e))

    with aioresponses() as m:
        await start_sync_with_stubs(
            client,
            m,
            initial_sync=fixture,
            min_rooms=0,
            close_client=True,
        )

    redaction_events = [e for e in events if isinstance(e, MessageRedacted)]
    assert len(redaction_events) >= 1
    ev = redaction_events[0]
    assert ev.event_id == "$ev1:example.com"
    assert ev.room_id == "!room_a:example.com"
