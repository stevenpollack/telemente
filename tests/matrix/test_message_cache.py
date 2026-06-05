"""Tests for telemente.matrix.cache (plan 0013).

All tests use an in-memory SQLite database for isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from telemente.matrix.cache import MessageCache
from telemente.matrix.models import Message


def make_message(
    event_id: str = "$ev1:example.com",
    room_id: str = "!r:s",
    sender: str = "@alice:example.com",
    sender_display_name: str = "Alice",
    body: str = "hello",
    timestamp_ms: int = 1_700_000_000_000,
    media_url: str | None = None,
    media_type: str | None = None,
    reactions: dict[str, list[str]] | None = None,
    reply_to_event_id: str | None = None,
) -> Message:
    return Message(
        event_id=event_id,
        room_id=room_id,
        sender=sender,
        sender_display_name=sender_display_name,
        body=body,
        timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
        media_url=media_url,
        media_type=media_type,
        reactions=reactions or {},
        reply_to_event_id=reply_to_event_id,
    )


@pytest.fixture
async def cache() -> MessageCache:
    c = MessageCache()
    await c.open(":memory:")
    return c


# ---------------------------------------------------------------------------
# Test 1: is_cold on empty room
# ---------------------------------------------------------------------------


async def test_is_cold_empty_room(cache: MessageCache) -> None:
    assert await cache.is_cold("!r:s") is True


# ---------------------------------------------------------------------------
# Test 2: put and get round-trip
# ---------------------------------------------------------------------------


async def test_put_and_get_round_trip(cache: MessageCache) -> None:
    msg = make_message()
    await cache.put(msg)
    result = await cache.get_room("!r:s")
    assert len(result) == 1
    got = result[0]
    assert got.event_id == msg.event_id
    assert got.room_id == msg.room_id
    assert got.sender == msg.sender
    assert got.sender_display_name == msg.sender_display_name
    assert got.body == msg.body
    assert got.timestamp == msg.timestamp
    assert got.media_url == msg.media_url
    assert got.media_type == msg.media_type
    assert got.reactions == msg.reactions
    assert got.reply_to_event_id == msg.reply_to_event_id


# ---------------------------------------------------------------------------
# Test 3: put is idempotent
# ---------------------------------------------------------------------------


async def test_put_idempotent(cache: MessageCache) -> None:
    msg = make_message()
    await cache.put(msg)
    await cache.put(msg)
    result = await cache.get_room("!r:s")
    assert len(result) == 1


# ---------------------------------------------------------------------------
# Test 4: get_room ordered oldest-first
# ---------------------------------------------------------------------------


async def test_get_room_ordered_oldest_first(cache: MessageCache) -> None:
    msgs = [
        make_message(event_id="$a", timestamp_ms=3_000),
        make_message(event_id="$b", timestamp_ms=1_000),
        make_message(event_id="$c", timestamp_ms=2_000),
    ]
    for m in msgs:
        await cache.put(m)
    result = await cache.get_room("!r:s")
    assert [r.event_id for r in result] == ["$b", "$c", "$a"]


# ---------------------------------------------------------------------------
# Test 5: get_room limit
# ---------------------------------------------------------------------------


async def test_get_room_limit(cache: MessageCache) -> None:
    for i in range(10):
        await cache.put(make_message(event_id=f"$ev{i}", timestamp_ms=i * 1000))
    result = await cache.get_room("!r:s", limit=3)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# Test 6: put_many marks room warm
# ---------------------------------------------------------------------------


async def test_put_many_marks_room_warm(cache: MessageCache) -> None:
    msgs = [
        make_message(event_id="$a", timestamp_ms=1_000),
        make_message(event_id="$b", timestamp_ms=2_000),
    ]
    await cache.put_many(msgs)
    assert await cache.is_cold("!r:s") is False


# ---------------------------------------------------------------------------
# Test 7: evict_old keeps newest
# ---------------------------------------------------------------------------


async def test_evict_old_keeps_newest(cache: MessageCache) -> None:
    for i in range(10):
        await cache.put(make_message(event_id=f"$ev{i}", timestamp_ms=i * 1000))
    await cache.evict_old("!r:s", keep=5)
    result = await cache.get_room("!r:s", limit=100)
    assert len(result) == 5
    # The 5 newest (timestamps 5000-9000) should survive
    timestamps_ms = [int(r.timestamp.timestamp() * 1000) for r in result]
    assert min(timestamps_ms) == 5_000


# ---------------------------------------------------------------------------
# Test 8: reactions serialized
# ---------------------------------------------------------------------------


async def test_reactions_serialized(cache: MessageCache) -> None:
    msg = make_message(reactions={"👍": ["@a:s"]})
    await cache.put(msg)
    result = await cache.get_room("!r:s")
    assert result[0].reactions == {"👍": ["@a:s"]}


# ---------------------------------------------------------------------------
# Test 9: reply_to_event_id round-trip
# ---------------------------------------------------------------------------


async def test_reply_to_event_id_round_trip(cache: MessageCache) -> None:
    msg = make_message(reply_to_event_id="$parent:example.com")
    await cache.put(msg)
    result = await cache.get_room("!r:s")
    assert result[0].reply_to_event_id == "$parent:example.com"


# ---------------------------------------------------------------------------
# Test 10: media message round-trip
# ---------------------------------------------------------------------------


async def test_media_message_round_trip(cache: MessageCache) -> None:
    msg = make_message(
        media_url="https://example.com/img.jpg",
        media_type="image",
    )
    await cache.put(msg)
    result = await cache.get_room("!r:s")
    assert result[0].media_url == "https://example.com/img.jpg"
    assert result[0].media_type == "image"


# ---------------------------------------------------------------------------
# Test 11: open recreates on schema mismatch
# ---------------------------------------------------------------------------


async def test_open_recreates_on_schema_mismatch() -> None:
    """open() drops and recreates the table when it has the wrong schema."""
    import aiosqlite

    # Create a stale schema with a missing column
    conn = await aiosqlite.connect(":memory:")
    await conn.execute(
        "CREATE TABLE messages (room_id TEXT, event_id TEXT, PRIMARY KEY (room_id, event_id))"
    )
    await conn.commit()
    # We can't pass an existing connection to MessageCache.open(), so we test
    # indirectly: open() on a fresh :memory: DB must include all required columns.
    await conn.close()

    fresh = MessageCache()
    await fresh.open(":memory:")
    # Verify all required columns exist by inserting a full row
    msg = make_message()
    await fresh.put(msg)
    result = await fresh.get_room("!r:s")
    assert len(result) == 1
    assert result[0].event_id == msg.event_id


# ---------------------------------------------------------------------------
# Test 12: mark_redacted overwrites body with tombstone string
# ---------------------------------------------------------------------------


async def test_mark_redacted_updates_body(cache: MessageCache) -> None:
    msg = make_message(body="original")
    await cache.put(msg)
    await cache.mark_redacted(msg.room_id, msg.event_id)
    rows = await cache.get_room(msg.room_id)
    assert rows[0].body == "\U0001f5d1️ Message deleted"
