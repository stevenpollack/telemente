# Plan 0013 — Message Cache

## Goal

Eliminate the per-room-switch network round-trip in `MessageView.load_room`.
After this plan, opening a room that has been visited before (or whose messages
arrived via sync) loads from a local SQLite cache and returns in well under
100 ms. Messages persist across app restarts. Backfill from the server still
happens on first visit and optionally on scroll-up to load older history.

## Background

Currently `MessageView.load_room` calls `await self._client.messages(room_id)`
every time the user switches to a tab. That method issues `room_messages` over
HTTP — one full network round-trip, typically 200–800 ms, every switch.

nio's built-in `SqliteStore` is E2EE-only: it persists Olm/Megolm sessions,
device keys, and the sync token, but **does not cache room timeline events**.
There is no nio facility to replay a cached timeline.

iamb (the Rust reference client at `../iamb/`) uses `sled` (an embedded
key-value tree) to store the full event timeline per room. For Python at this
scale that is over-engineering.

## Recommended approach: telemente-owned `aiosqlite` cache

A thin `MessageCache` class, owned by `MatrixClient`, backed by a single
`aiosqlite` database. On `load_room`, the cache is read first; a backfill HTTP
call is made only when the cache is cold (first visit ever, or after an
explicit refresh). New messages arriving through `_on_room_message`,
`_on_room_media`, and `_on_megolm_event` are written immediately. The cache is
capped at a configurable per-room limit (default 500 rows).

**Why not nio SqliteStore?** nio's store does not cache timeline events.
Adding it would require forking or monkey-patching nio internals.

**Why not in-memory LRU (Option C)?** Does not survive restarts — the primary
pain point.

**Why Option B over a more elaborate solution?** YAGNI and KISS. `aiosqlite`
is readily available, the schema is trivial, and the surface added to
`MatrixClient` is five methods. The entire implementation is ~100 lines
excluding tests.

## Dependencies

- `aiosqlite` — add to `pyproject.toml` as a runtime dependency.
- Plans 0003 (`MatrixClient`, `Message` model), 0009 (sync callbacks).

## Schema

Single table, stored at `{xdg_data_dir}/telemente/message_cache.db`:

```sql
CREATE TABLE IF NOT EXISTS messages (
    room_id               TEXT    NOT NULL,
    event_id              TEXT    NOT NULL,
    sender                TEXT    NOT NULL,
    sender_display_name   TEXT    NOT NULL,
    body                  TEXT    NOT NULL,
    timestamp_ms          INTEGER NOT NULL,   -- Unix epoch ms
    media_url             TEXT,               -- NULL for text messages
    media_type            TEXT,               -- NULL for text messages
    reactions             TEXT    NOT NULL DEFAULT '{}',  -- JSON
    reply_to_event_id     TEXT,               -- NULL if not a reply
    PRIMARY KEY (room_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_room_ts
    ON messages (room_id, timestamp_ms DESC);
```

No migration table needed at this scale. On schema mismatch (detected via
`PRAGMA table_info`) the table is dropped and recreated — the cache is a
pure performance optimization, all data is recoverable from the homeserver.

## Data model: `MessageCache` class

New file: `src/telemente/matrix/cache.py`

```python
class MessageCache:
    async def open(self, db_path: str) -> None: ...
    async def close(self) -> None: ...
    async def put(self, message: Message) -> None: ...
    async def put_many(self, messages: list[Message]) -> None: ...
    async def get_room(self, room_id: str, limit: int = 50) -> list[Message]: ...
    async def evict_old(self, room_id: str, keep: int = 500) -> None: ...
    async def is_cold(self, room_id: str) -> bool: ...
```

- `open` / `close` — manage the `aiosqlite` connection; `open` runs `CREATE TABLE IF NOT EXISTS`.
- `put` / `put_many` — idempotent `INSERT OR REPLACE`.
- `get_room` — returns rows sorted `timestamp_ms ASC`, limited to `limit`.
- `evict_old` — deletes rows beyond `keep` newest per room (called after backfill).
- `is_cold` — `True` if the room has zero rows.

## Integration points

### `src/telemente/matrix/client.py`

1. `__init__` gains `cache_path: str | None = None`. When set, a `MessageCache`
   instance is stored as `self._cache`.
2. `_open_cache()` — called from `restore()` and `_finalize_login()` alongside
   `_load_store()`. Calls `self._cache.open(cache_path)`.
3. `messages(room_id, limit)` — cache-first strategy:
   - Warm room → `return await self._cache.get_room(room_id, limit)` immediately.
   - Cold room → HTTP `room_messages`, `put_many`, `evict_old`, return.
4. `_on_room_message`, `_on_room_media`, `_on_megolm_event` — each gains
   `if self._cache: await self._cache.put(message)` after constructing the
   `Message` and before `_emit(NewMessage(...))`.
5. `close()` — calls `await self._cache.close()` when cache is configured.

### `src/telemente/config.py`

Add `cache_db_path() -> str` returning the XDG data path for
`message_cache.db`, consistent with how `store_path` is computed.

### `src/telemente/tui/app.py`

Pass `cache_path=settings.cache_db_path()` when constructing `MatrixClient`.
One-line change.

### `src/telemente/tui/widgets/message_view.py`

No changes required. `load_room` already calls
`await self._client.messages(room_id)`. The cache is entirely encapsulated
in `MatrixClient`.

## Cold-start and migration

On first run the cache is empty for all rooms. The first `load_room` call for
each room follows the existing HTTP path and populates the cache. Subsequent
visits are served from cache. After the first app restart, all previously
visited rooms load instantly.

## Cache invalidation

This plan does not attempt smart invalidation. The cache grows by append (new
messages from sync) and is trimmed to `keep=500` per room after each backfill.
Edits and redactions arrive in near-real-time via sync callbacks and are
already handled in-place by `MessageView`; the cache's stale copy is simply
overwritten at next reload. A future plan can add event-level patching.

## Test cases (TDD — write first)

### `tests/matrix/test_message_cache.py` (new)

1. `test_is_cold_empty_room` — fresh cache; `is_cold("!r:s")` returns `True`.
2. `test_put_and_get_round_trip` — put a `Message`; `get_room` returns it with all fields intact.
3. `test_put_idempotent` — put the same message twice; `get_room` returns exactly one row.
4. `test_get_room_ordered_oldest_first` — 3 messages with different timestamps; `get_room` returns them ascending.
5. `test_get_room_limit` — 10 messages; `get_room(limit=3)` returns 3.
6. `test_put_many_marks_room_warm` — `put_many([msg1, msg2])`; `is_cold` returns `False`.
7. `test_evict_old_keeps_newest` — 10 messages; `evict_old(keep=5)`; 5 newest survive.
8. `test_reactions_serialized` — put message with `reactions={"👍": ["@a:s"]}`; round-trip preserves it.
9. `test_reply_to_event_id_round_trip` — `reply_to_event_id` survives round-trip.
10. `test_media_message_round_trip` — `media_url` and `media_type` survive round-trip.
11. `test_open_recreates_on_schema_mismatch` — stale schema; `open()` succeeds and table has all columns.

### `tests/matrix/test_client.py` (extend)

12. `test_messages_cold_room_hits_network` — temp-file cache; mock HTTP; assert network call made.
13. `test_messages_warm_room_skips_network` — call `messages` twice; assert HTTP called exactly once.
14. `test_on_room_message_writes_to_cache` — trigger `_on_room_message`; assert cache is warm.
15. `test_close_closes_cache` — `client.close()`; assert cache connection closed.

## Done-when

- [ ] `MessageCache` at `src/telemente/matrix/cache.py`; fully typed; `mypy --strict` passes.
- [ ] `MatrixClient` accepts `cache_path`; wires through login, restore, sync callbacks, close.
- [ ] `messages()` is cache-first: warm rooms return from SQLite; cold rooms fetch and populate.
- [ ] Sync callbacks write to cache.
- [ ] All 15 test cases pass.
- [ ] `aiosqlite` added to `pyproject.toml` runtime dependencies.
- [ ] `ruff check .` and `mypy` pass with no new suppressions.
- [ ] Subjectively: switching between two previously loaded rooms feels instant.
