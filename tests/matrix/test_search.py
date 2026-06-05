"""Tier-1 tests for MessageCache.search_room and MatrixClient.search_messages (plan 0024).

All tests use real MessageCache with in-memory SQLite — no aioresponses needed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from matrix.helpers import HOMESERVER, make_session
from telemente.matrix.cache import MessageCache
from telemente.matrix.client import MatrixClient
from telemente.matrix.models import Message


def make_message(
    event_id: str = "$ev1:example.com",
    room_id: str = "!r:s",
    body: str = "hello",
    timestamp_ms: int = 1_700_000_000_000,
) -> Message:
    return Message(
        event_id=event_id,
        room_id=room_id,
        sender="@alice:example.com",
        sender_display_name="Alice",
        body=body,
        timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
    )


@pytest.fixture
async def cache() -> MessageCache:
    c = MessageCache()
    await c.open(":memory:")
    return c


# ---------------------------------------------------------------------------
# Test 1: search_room on empty cache returns empty list
# ---------------------------------------------------------------------------


async def test_search_room_empty_cache_returns_empty(cache: MessageCache) -> None:
    result = await cache.search_room("!r:s", "foo")
    assert result == []


# ---------------------------------------------------------------------------
# Test 2: search_room single match
# ---------------------------------------------------------------------------


async def test_search_room_single_match(cache: MessageCache) -> None:
    msg = make_message(body="hello world")
    await cache.put(msg)
    result = await cache.search_room(msg.room_id, "world")
    assert result == [msg.event_id]


# ---------------------------------------------------------------------------
# Test 3: search_room is case-insensitive
# ---------------------------------------------------------------------------


async def test_search_room_case_insensitive(cache: MessageCache) -> None:
    msg = make_message(body="Hello World")
    await cache.put(msg)
    result = await cache.search_room(msg.room_id, "hello")
    assert result == [msg.event_id]


# ---------------------------------------------------------------------------
# Test 4: search_room no match returns empty list
# ---------------------------------------------------------------------------


async def test_search_room_no_match(cache: MessageCache) -> None:
    msg = make_message(body="hello")
    await cache.put(msg)
    result = await cache.search_room(msg.room_id, "goodbye")
    assert result == []


# ---------------------------------------------------------------------------
# Test 5: search_room multiple matches returned in chronological order
# ---------------------------------------------------------------------------


async def test_search_room_multiple_matches_in_order(cache: MessageCache) -> None:
    m1 = make_message(event_id="$e1", body="foo", timestamp_ms=1_000)
    m2 = make_message(event_id="$e2", body="bar", timestamp_ms=2_000)
    m3 = make_message(event_id="$e3", body="foo bar", timestamp_ms=3_000)
    for m in [m1, m2, m3]:
        await cache.put(m)
    result = await cache.search_room("!r:s", "foo")
    assert result == ["$e1", "$e3"]


# ---------------------------------------------------------------------------
# Test 6: search_room is scoped to the given room
# ---------------------------------------------------------------------------


async def test_search_room_scoped_to_room(cache: MessageCache) -> None:
    m_a = make_message(event_id="$a1", room_id="!room_a:s", body="shared text")
    m_b = make_message(event_id="$b1", room_id="!room_b:s", body="shared text")
    await cache.put(m_a)
    await cache.put(m_b)

    result = await cache.search_room("!room_a:s", "shared")
    assert result == ["$a1"]

    result_b = await cache.search_room("!room_b:s", "shared")
    assert result_b == ["$b1"]


# ---------------------------------------------------------------------------
# Test 7: search_room with empty query returns empty immediately (no DB call)
# ---------------------------------------------------------------------------


async def test_search_room_empty_query_returns_empty(cache: MessageCache) -> None:
    msg = make_message(body="some content")
    await cache.put(msg)
    result = await cache.search_room(msg.room_id, "")
    assert result == []


# ---------------------------------------------------------------------------
# Test 8: MatrixClient.search_messages delegates to cache
# ---------------------------------------------------------------------------


async def test_matrix_client_search_messages_delegates_to_cache() -> None:
    nio_mock = AsyncMock()
    nio_mock.rooms = {}
    nio_mock.should_upload_keys = False
    nio_mock.should_query_keys = False

    cache = MessageCache()
    await cache.open(":memory:")

    msg = make_message(event_id="$x1", room_id="!r:s", body="search me")
    await cache.put(msg)

    # Build client with injected cache
    client = MatrixClient(HOMESERVER, nio_client=nio_mock)
    client._cache = cache  # inject pre-populated cache
    await client.restore(make_session(homeserver=HOMESERVER))

    result = await client.search_messages("!r:s", "search")
    assert result == ["$x1"]


# ---------------------------------------------------------------------------
# Test 9: MatrixClient.search_messages with no cache returns empty list
# ---------------------------------------------------------------------------


async def test_matrix_client_search_messages_no_cache_returns_empty() -> None:
    nio_mock = AsyncMock()
    nio_mock.rooms = {}
    nio_mock.should_upload_keys = False
    nio_mock.should_query_keys = False

    # Build client without cache_path → _cache is None
    client = MatrixClient(HOMESERVER, nio_client=nio_mock)
    await client.restore(make_session(homeserver=HOMESERVER))

    assert client._cache is None
    result = await client.search_messages("!r:s", "foo")
    assert result == []
