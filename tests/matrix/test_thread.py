"""Tier-1 tests for thread message fetching (plan 0023).

Uses real nio HTTP parsing via aioresponses cassettes.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from aioresponses import aioresponses

from matrix.helpers import (
    HOMESERVER,
    build_nio_mock,
    event_callback_for,
    load_fixture,
    make_nio_room,
    make_session,
    make_text_event,
    restore_client,
    stub_get,
    stub_room_messages,
    stub_sync,
    wait_until,
)
from telemente.matrix.client import (
    MatrixClient,
    NewMessage,
    NotLoggedInError,
)

# ---------------------------------------------------------------------------
# URL helper
# ---------------------------------------------------------------------------

ROOM_ID = "!r:s"
ROOT_EVENT_ID = "$root:example.com"

# /_matrix/client/v1/rooms/{room_id}/relations/{event_id}/m.thread?...
_THREAD_RELATIONS_RE = re.compile(
    rf"^{re.escape(HOMESERVER)}/_matrix/client/v1/rooms/"
    r"[^/]+/relations/[^/]+/m\.thread(\?.*)?$"
)


# ---------------------------------------------------------------------------
# Test 1: chronological order + has_more=False
# ---------------------------------------------------------------------------


async def test_get_thread_messages_returns_chronological_messages(real_nio_client: Any) -> None:
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}
    sync_payload: dict[str, Any] = {
        "next_batch": "s1",
        "rooms": {
            "join": {
                ROOM_ID: {
                    "timeline": {"events": [], "limited": False, "prev_batch": "p1"},
                    "state": {"events": []},
                    "account_data": {"events": []},
                }
            },
            "invite": {},
            "leave": {},
        },
    }

    with aioresponses() as m:
        stub_sync(m, sync_payload)
        stub_sync(m, idle, repeat=True)
        stub_get(m, _THREAD_RELATIONS_RE, payload=load_fixture("thread_relations_page1.json"))

        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await client.start_sync()
        await wait_until(lambda: ROOM_ID in real_nio_client.rooms)
        messages, has_more = await client.get_thread_messages(ROOM_ID, ROOT_EVENT_ID)
        await client.close()

    assert len(messages) == 2
    # Chronological: oldest first (fixture returns newest-first "back" direction)
    assert messages[0].event_id == "$reply1:example.com"
    assert messages[1].event_id == "$reply2:example.com"
    assert messages[0].body == "First reply"
    assert messages[0].thread_root_id == ROOT_EVENT_ID
    assert has_more is False


# ---------------------------------------------------------------------------
# Test 2: thread_root_id set on every returned message
# ---------------------------------------------------------------------------


async def test_get_thread_messages_sets_thread_root_id(real_nio_client: Any) -> None:
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}
    sync_payload: dict[str, Any] = {
        "next_batch": "s1",
        "rooms": {
            "join": {
                ROOM_ID: {
                    "timeline": {"events": [], "limited": False, "prev_batch": "p1"},
                    "state": {"events": []},
                    "account_data": {"events": []},
                }
            },
            "invite": {},
            "leave": {},
        },
    }

    with aioresponses() as m:
        stub_sync(m, sync_payload)
        stub_sync(m, idle, repeat=True)
        stub_get(m, _THREAD_RELATIONS_RE, payload=load_fixture("thread_relations_page1.json"))

        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await client.start_sync()
        await wait_until(lambda: ROOM_ID in real_nio_client.rooms)
        messages, _ = await client.get_thread_messages(ROOM_ID, ROOT_EVENT_ID)
        await client.close()

    for msg in messages:
        assert msg.thread_root_id == ROOT_EVENT_ID


# ---------------------------------------------------------------------------
# Test 3: server error → graceful ([], False)
# ---------------------------------------------------------------------------


async def test_get_thread_messages_server_error_returns_empty(real_nio_client: Any) -> None:
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}
    sync_payload: dict[str, Any] = {
        "next_batch": "s1",
        "rooms": {
            "join": {
                ROOM_ID: {
                    "timeline": {"events": [], "limited": False, "prev_batch": "p1"},
                    "state": {"events": []},
                    "account_data": {"events": []},
                }
            },
            "invite": {},
            "leave": {},
        },
    }

    with aioresponses() as m:
        stub_sync(m, sync_payload)
        stub_sync(m, idle, repeat=True)
        stub_get(
            m,
            _THREAD_RELATIONS_RE,
            status=404,
            payload={"errcode": "M_NOT_FOUND", "error": "Not found"},
        )

        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await client.start_sync()
        await wait_until(lambda: ROOM_ID in real_nio_client.rooms)
        messages, has_more = await client.get_thread_messages(ROOM_ID, ROOT_EVENT_ID)
        await client.close()

    assert messages == []
    assert has_more is False


# ---------------------------------------------------------------------------
# Test 4: not logged in → NotLoggedInError
# ---------------------------------------------------------------------------


async def test_get_thread_messages_not_logged_in_raises() -> None:
    nio_mock = build_nio_mock()
    # Build a client WITHOUT calling restore() — not logged in
    client = MatrixClient(HOMESERVER, nio_client=nio_mock)

    with pytest.raises(NotLoggedInError):
        await client.get_thread_messages(ROOM_ID, ROOT_EVENT_ID)


# ---------------------------------------------------------------------------
# Test 5: backfill messages() sets thread_root_id for thread replies
# ---------------------------------------------------------------------------


async def test_backfill_thread_reply_sets_thread_root_id(real_nio_client: Any) -> None:
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}
    sync_payload: dict[str, Any] = {
        "next_batch": "s1",
        "rooms": {
            "join": {
                ROOM_ID: {
                    "timeline": {"events": [], "limited": False, "prev_batch": "p1"},
                    "state": {"events": []},
                    "account_data": {"events": []},
                }
            },
            "invite": {},
            "leave": {},
        },
    }
    backfill_payload: dict[str, Any] = {
        "start": "t1",
        "end": "t0",
        "chunk": [
            {
                "type": "m.room.message",
                "event_id": "$backfill_reply:example.com",
                "sender": "@bob:example.com",
                "origin_server_ts": 1700000020000,
                "content": {
                    "msgtype": "m.text",
                    "body": "Backfill thread reply",
                    "m.relates_to": {
                        "rel_type": "m.thread",
                        "event_id": "$root:example.com",
                        "m.in_reply_to": {"event_id": "$root:example.com"},
                        "is_falling_back": True,
                    },
                },
            }
        ],
    }

    with aioresponses() as m:
        stub_sync(m, sync_payload)
        stub_sync(m, idle, repeat=True)
        stub_room_messages(m, ROOM_ID, backfill_payload)

        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await client.start_sync()
        await wait_until(lambda: ROOM_ID in real_nio_client.rooms)
        msgs = await client.messages(ROOM_ID)
        await client.close()

    assert len(msgs) == 1
    assert msgs[0].thread_root_id == "$root:example.com"


# ---------------------------------------------------------------------------
# Test 6: live sync delivers thread reply with thread_root_id set
# ---------------------------------------------------------------------------


async def test_live_sync_thread_reply_sets_thread_root_id() -> None:
    import nio

    nio_mock = build_nio_mock()
    nio_mock.rooms = {"!r:s": make_nio_room("!r:s")}
    client = await restore_client(nio_mock)

    received: list[NewMessage] = []

    def handler(event: object) -> None:
        if isinstance(event, NewMessage):
            received.append(event)

    client.subscribe(handler)

    room = make_nio_room("!r:s")
    # Build an event with rel_type m.thread
    ev = make_text_event(
        event_id="$live_thread_reply:example.com",
        sender="@bob:example.com",
        body="Live thread reply",
    )
    ev.source = {
        "content": {
            "msgtype": "m.text",
            "body": "Live thread reply",
            "m.relates_to": {
                "rel_type": "m.thread",
                "event_id": "$root:example.com",
                "m.in_reply_to": {"event_id": "$root:example.com"},
                "is_falling_back": True,
            },
        }
    }

    await event_callback_for(nio_mock, nio.RoomMessageText)(room, ev)

    assert len(received) == 1
    assert received[0].message.thread_root_id == "$root:example.com"


# ---------------------------------------------------------------------------
# Test 7: has_more=True when limit+1 events returned
# ---------------------------------------------------------------------------


async def test_get_thread_messages_has_more_when_limit_exceeded(real_nio_client: Any) -> None:
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}
    sync_payload: dict[str, Any] = {
        "next_batch": "s1",
        "rooms": {
            "join": {
                ROOM_ID: {
                    "timeline": {"events": [], "limited": False, "prev_batch": "p1"},
                    "state": {"events": []},
                    "account_data": {"events": []},
                }
            },
            "invite": {},
            "leave": {},
        },
    }

    # Build a payload with 3 events (limit=2 → limit+1=3 → has_more).
    # next_batch presence triggers generator pagination; we break after limit+1 events.
    three_events_payload: dict[str, Any] = {
        "chunk": [
            {
                "type": "m.room.message",
                "event_id": f"$reply{i}:example.com",
                "room_id": ROOM_ID,
                "sender": "@bob:example.com",
                "origin_server_ts": 1700000000000 + i * 1000,
                "content": {
                    "msgtype": "m.text",
                    "body": f"Reply {i}",
                    "m.relates_to": {
                        "rel_type": "m.thread",
                        "event_id": "$root:example.com",
                    },
                },
            }
            for i in range(3)
        ],
        "next_batch": "t123",
    }

    with aioresponses() as m:
        stub_sync(m, sync_payload)
        stub_sync(m, idle, repeat=True)
        stub_get(m, _THREAD_RELATIONS_RE, payload=three_events_payload)

        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        await client.start_sync()
        await wait_until(lambda: ROOM_ID in real_nio_client.rooms)
        messages, has_more = await client.get_thread_messages(ROOM_ID, ROOT_EVENT_ID, limit=2)
        await client.close()

    assert has_more is True
    assert len(messages) == 2
