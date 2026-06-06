# Plan 0032 — Pagination (load older messages)

## Goal

When the user scrolls to the top of a room's message timeline, telemente fetches
the next page of older messages and prepends them to the visible list. The
initial 50-message window is no longer a hard limit; the full conversation
history is accessible by scrolling up.

---

## Dependencies

- Plans 0001–0031 complete.
- Plan 0013 (`MessageCache`) — pagination tokens are stored per-room alongside
  the cached messages.

---

## Architecture

### Matrix protocol notes

`GET /rooms/{roomId}/messages` accepts a `from` token (the `start` field from
the previous `RoomMessagesResponse`, or `prev_batch` from a sync timeline).
Passing `dir=b` (backward) fetches older messages. The response's `end` field
is the token to use in the next page request. When `end` is `None` (or absent),
there are no more pages.

In nio:

```python
response = await self._client.room_messages(
    room_id,
    start=prev_token,          # from previous RoomMessagesResponse.end
    direction=MessageDirection.back,
    limit=50,
)
# response.end is the next pagination token (None = no more pages)
# response.chunk is newest-first; reverse for chronological prepend
```

The initial `messages()` call (cold room backfill) already calls
`room_messages` with no `start` token. The response's `end` token is the
pagination cursor for "load older" requests.

### Pagination token storage

**`src/telemente/matrix/cache.py`**

Add a `pagination_tokens` table:

```sql
CREATE TABLE IF NOT EXISTS pagination_tokens (
    room_id  TEXT PRIMARY KEY,
    token    TEXT NOT NULL
)
```

Methods:
- `set_pagination_token(room_id, token)` — upsert.
- `get_pagination_token(room_id) -> str | None` — return stored token or None.
- `clear_pagination_token(room_id)` — delete when no more pages.

### What changes

**`src/telemente/matrix/client.py`**

1. In the existing `messages()` method, after the cold backfill:

   ```python
   # Store the pagination cursor for subsequent "load older" requests.
   if self._cache is not None and isinstance(response, nio.RoomMessagesResponse):
       if response.end:
           await self._cache.set_pagination_token(room_id, response.end)
       else:
           await self._cache.clear_pagination_token(room_id)
   ```

2. Add a new public method:

```python
async def load_older_messages(
    self, room_id: str, limit: int = 50
) -> tuple[list[Message], bool]:
    """Fetch the next page of older messages for room_id.

    Returns (messages_chronological, has_more).
    Returns ([], False) if no pagination token is available (already at
    the start of history) or on error.

    The returned messages are prepend-safe: they are older than any message
    currently in the cache, in chronological order (oldest first).
    """
    if not self._logged_in:
        raise NotLoggedInError("Must be logged in to load older messages")

    if self._cache is None:
        logger.warning("load_older_messages: cache unavailable for room=%s", room_id)
        return [], False

    token = await self._cache.get_pagination_token(room_id)
    if token is None:
        logger.debug("load_older_messages: no token for room=%s (at start)", room_id)
        return [], False

    response = await self._client.room_messages(
        room_id,
        start=token,
        direction=_NioMessageDirection.back,
        limit=limit,
    )
    if not isinstance(response, nio.RoomMessagesResponse):
        logger.warning(
            "load_older_messages: room_messages failed for %s: %s", room_id, response
        )
        return [], False

    # Update or clear the pagination token.
    if response.end:
        await self._cache.set_pagination_token(room_id, response.end)
    else:
        await self._cache.clear_pagination_token(room_id)

    has_more = response.end is not None

    # Parse events the same way messages() does (text, media, redacted,
    # megolm). Skip m.replace events.
    room = self._client.rooms.get(room_id)
    result: list[Message] = _parse_room_messages_chunk(
        response.chunk, room_id, room
    )
    # response.chunk is newest-first; reverse to chronological (oldest first).
    result.reverse()

    # Populate the cache with the new page.
    if result:
        await self._cache.put_many(result)

    logger.info(
        "load_older_messages: %d messages for room=%s has_more=%s",
        len(result), room_id, has_more,
    )
    return result, has_more
```

The event-parsing logic in `messages()` is refactored into a private helper
`_parse_room_messages_chunk(chunk, room_id, room)` shared by both `messages()`
and `load_older_messages()` to avoid duplication.

Also import `MessageDirection` from nio as `_NioMessageDirection`:

```python
from nio.client.async_client import MessageDirection as _NioMessageDirection
```

Add to stubs if not present.

**`src/telemente/tui/widgets/message_view.py`**

1. Add a `_has_more_history: bool = True` instance variable (default True until
   proven otherwise by the first pagination result).

2. Add a `_loading_history: bool = False` guard to prevent concurrent loads.

3. In the `_MessageViewClient` protocol, add:

```python
async def load_older_messages(
    self, room_id: str, limit: int = 50
) -> tuple[list[Message], bool]: ...
```

4. Add scroll detection. Textual's `VerticalScroll` emits `ScrollTo` and
   `Scroll` events, but there is no built-in "reached top" event. Use a
   `on_scroll_end` handler on `VerticalScroll` — but "scroll end" means bottom.
   Instead, override `on_message_timeline_scroll` (if available) or use
   `watch` on `VerticalScroll.scroll_y`. The correct approach for "scrolled to
   top" is to check `timeline.scroll_y == 0` in `on_scroll` on the
   `VerticalScroll`:

```python
def on_vertical_scroll_scroll(self, event: events.Scroll) -> None:
    """Detect scroll-to-top and trigger history load."""
    timeline = self.query_one("#message-timeline", VerticalScroll)
    if (
        timeline.scroll_y == 0
        and self._has_more_history
        and not self._loading_history
        and self._current_room_id is not None
    ):
        self.run_worker(
            self._load_older(self._current_room_id),
            exclusive=True,
            name="load-older",
        )
```

5. Add `_load_older` worker coroutine:

```python
async def _load_older(self, room_id: str) -> None:
    if self._loading_history:
        return
    self._loading_history = True
    try:
        messages, has_more = await self._client.load_older_messages(room_id)
        self._has_more_history = has_more
        if messages:
            self._prepend_messages(messages)
    except Exception as exc:
        logger.warning("_load_older failed for %s: %s", room_id, exc)
    finally:
        self._loading_history = False
```

6. Add `_prepend_messages` to insert rows at the top of the timeline:

```python
def _prepend_messages(self, messages: list[Message]) -> None:
    """Prepend older messages at the top of the timeline without scrolling."""
    timeline = self.query_one("#message-timeline", VerticalScroll)
    # Capture scroll position before insertion.
    old_scroll_y = timeline.scroll_y
    old_height = timeline.virtual_size.height

    # Register new event IDs and bodies.
    for msg in messages:
        if msg.event_id not in self._rendered_event_ids:
            self._rendered_event_ids.add(msg.event_id)
            self._msgs_by_id[msg.event_id] = msg

    # Build widgets to prepend (oldest first — messages is already chrono).
    to_mount: list[Widget] = []
    for msg in messages:
        if msg.event_id in self._rendered_event_ids:
            reply_quoted = (
                self._msgs_by_id.get(msg.reply_to_event_id)
                if msg.reply_to_event_id
                else None
            )
            to_mount.append(MessageRow(msg, reply_quoted=reply_quoted))

    if not to_mount:
        return

    # Mount before the first existing child to prepend.
    first_child = next(
        (w for w in timeline.children if isinstance(w, (MessageRow, _DateSeparator))),
        None,
    )
    if first_child is not None:
        timeline.mount(*to_mount, before=first_child)
    else:
        timeline.mount(*to_mount)

    # Restore scroll position so the user stays at the same apparent position.
    def _restore_scroll() -> None:
        new_height = timeline.virtual_size.height
        delta = new_height - old_height
        timeline.scroll_to(y=old_scroll_y + delta, animate=False)

    self.call_after_refresh(_restore_scroll)
```

7. Reset `_has_more_history = True` and `_loading_history = False` in `clear()`.

8. In `load_room`, after fetching messages, call `load_older_messages` with
   limit=0 just to prime the pagination token — NO, that would be an
   unnecessary network call. Instead, `messages()` already stores the token
   from the backfill response. No extra call needed in `load_room`.

**`src/telemente/tui/screens/main.py`**

Add `load_older_messages` to `_MainClient` protocol.

**`tests/fakes.py`**

Add to `FakeMatrixClient`:

```python
# Scripted older messages: room_id -> (messages, has_more)
self.older_messages: dict[str, tuple[list[Message], bool]] = {}
```

Add method:

```python
async def load_older_messages(
    self, room_id: str, limit: int = 50
) -> tuple[list[Message], bool]:
    self._check_fail("load_older_messages")
    await self._maybe_block("load_older_messages")
    return self.older_messages.get(room_id, ([], False))
```

---

## Implementation steps

1. Add `pagination_tokens` table to `cache.py` with `set_pagination_token`,
   `get_pagination_token`, `clear_pagination_token`.
2. Refactor `messages()` event-parsing into `_parse_room_messages_chunk()`.
3. Store pagination token in `messages()` cold backfill path.
4. Add `load_older_messages()` to `MatrixClient`.
5. Add `load_older_messages` to stubs if `MessageDirection` is missing.
6. Add `_prepend_messages` and scroll detection to `MessageView`.
7. Add `load_older_messages` to `_MessageViewClient` protocol.
8. Add `load_older_messages` to `_MainClient` protocol.
9. Add `load_older_messages` to `FakeMatrixClient`.
10. Write tests before steps 1–9.

---

## Tests

### Tier 1 — `tests/matrix/test_client_pagination.py`

```python
async def test_load_older_messages_returns_chronological_list(
    restore_client, aioresponses_ctx
) -> None:
    """load_older_messages returns messages in chronological (oldest-first) order."""

async def test_load_older_messages_no_token_returns_empty(
    restore_client,
) -> None:
    """load_older_messages returns ([], False) when no pagination token is stored."""

async def test_load_older_messages_updates_token(
    restore_client, aioresponses_ctx
) -> None:
    """After a successful page load, the pagination token is updated to
    response.end."""

async def test_load_older_messages_clears_token_at_start_of_history(
    restore_client, aioresponses_ctx
) -> None:
    """When response.end is None, the pagination token is cleared and has_more
    is False."""

async def test_messages_backfill_stores_pagination_token(
    restore_client, aioresponses_ctx
) -> None:
    """The initial messages() cold backfill stores response.end as the
    pagination token for the room."""

async def test_load_older_messages_skips_replace_events(
    restore_client, aioresponses_ctx
) -> None:
    """m.replace events in the chunk are not included in the returned list."""

async def test_load_older_messages_not_logged_in_raises(restore_client) -> None:
    """load_older_messages raises NotLoggedInError when not logged in."""
```

### Tier 2 — `tests/tui/test_pagination.py`

```python
async def test_scroll_to_top_triggers_load_older() -> None:
    """When the message timeline is scrolled to y=0, load_older_messages is
    called on the client."""

async def test_prepended_messages_appear_above_existing() -> None:
    """After load_older_messages returns, the new MessageRow widgets are
    prepended before the existing rows (older messages at top)."""

async def test_no_load_when_has_more_is_false() -> None:
    """When _has_more_history is False, scrolling to the top does not call
    load_older_messages."""

async def test_no_concurrent_loads() -> None:
    """While a load is in progress (_loading_history=True), a second scroll-to-top
    does not start another load_older_messages call."""

async def test_scroll_position_preserved_after_prepend() -> None:
    """After prepending older messages, the timeline scrolls to preserve the user's
    position (the previously-topmost message remains visible)."""

async def test_load_older_empty_result_sets_has_more_false() -> None:
    """If load_older_messages returns ([], False), _has_more_history is set to
    False and no rows are added."""
```

Setup: build a host app with `FakeMatrixClient`, pre-populate
`fake.older_messages[room_id] = (messages, has_more)`, load a room, simulate
scroll-to-top (set `timeline.scroll_y = 0` and post a scroll event), then
`await wait_for_workers(app)` and assert row count / order.

---

## Done-when checklist

- [ ] `MessageCache` has `pagination_tokens` table with get/set/clear methods.
- [ ] `MatrixClient.messages()` stores pagination token after cold backfill.
- [ ] `MatrixClient.load_older_messages()` fetches older messages and updates
      the token.
- [ ] Event parsing is shared via `_parse_room_messages_chunk` (no duplication).
- [ ] `MessageView` detects scroll-to-top and starts a load worker.
- [ ] `_prepend_messages` inserts rows above existing content and restores
      scroll position.
- [ ] `FakeMatrixClient.load_older_messages` is scripted via `older_messages`.
- [ ] All Tier 1 and Tier 2 tests listed above pass.
- [ ] `uv run ruff check .` / `uv run ruff format .` clean.
- [ ] `uv run mypy` clean.
- [ ] `pyright src/` clean.
- [ ] `uv run pytest` all green.
