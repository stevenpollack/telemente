# Plan 0031 — Read receipt sending

## Goal

When a user opens a room or scrolls to the bottom of its timeline, telemente
sends an `m.read` read receipt to the homeserver for the most-recently visible
message. This causes the server to decrement the unread count for that room so
counts do not accumulate forever across sessions.

---

## Dependencies

- Plans 0001–0030 complete.
- No additional runtime dependencies.

---

## Architecture

### Matrix protocol notes

The receipt endpoint is:

```
POST /_matrix/client/v3/rooms/{roomId}/receipt/m.read/{eventId}
Body: {}
```

On success the server returns `200 {}`. The unread count in the next sync
response for that room drops to zero (or to the count of events received after
the receipt's event_id).

nio has no dedicated `room_read_receipt` method. The call must be made via
`aiohttp` directly, the same pattern already used for `set_room_tag` and
`remove_room_tag`.

### What changes

**`src/telemente/matrix/client.py`**

Add a new public method:

```python
async def send_read_receipt(self, room_id: str, event_id: str) -> None:
    """Send an m.read receipt for event_id in room_id.

    Fire-and-forget semantics: logs a warning on failure but does not raise,
    because a failed receipt is non-critical (the room just stays marked
    unread until the next session).

    Raises NotLoggedInError if not logged in.
    """
    if not self._logged_in:
        raise NotLoggedInError("Must be logged in to send read receipts")
    import aiohttp

    url = (
        f"{self._homeserver}/_matrix/client/v3/rooms"
        f"/{room_id}/receipt/m.read/{event_id}"
    )
    try:
        async with (
            aiohttp.ClientSession() as http_session,
            http_session.post(
                url,
                json={},
                headers={"Authorization": f"Bearer {self._client.access_token}"},
            ) as resp,
        ):
            if resp.status not in (200, 204):
                logger.warning(
                    "send_read_receipt HTTP %d for %s in %s",
                    resp.status, event_id, room_id,
                )
    except Exception as exc:
        logger.warning("send_read_receipt failed for %s in %s: %s", event_id, room_id, exc)
```

**`src/telemente/tui/screens/main.py`**

1. Add `send_read_receipt` to `_MainClient` protocol:

```python
async def send_read_receipt(self, room_id: str, event_id: str) -> None: ...
```

2. Call `send_read_receipt` from two places:

   a. `_open_tab` — after `view.load_room(room_id)` returns, if the view has
      messages, send a receipt for the newest message's event_id:

   ```python
   await view.load_room(room_id)
   newest = view.newest_event_id
   if newest and self.active_room_id == room_id:
       self.run_worker(
           self._client.send_read_receipt(room_id, newest),
           exclusive=False,
           exit_on_error=False,
       )
   ```

   b. `action_scroll_latest` — but this action lives on `MessageView`, not
      `MainScreen`. Instead, add a new Textual `Message`:
      `MessageView.ScrolledToBottom(room_id, event_id)`. `MessageView` posts
      this when `_scroll_to_bottom` is called and there is a loaded message.
      `MainScreen` handles it:

   ```python
   def on_message_view_scrolled_to_bottom(
       self, event: MessageView.ScrolledToBottom
   ) -> None:
       if event.event_id:
           self.run_worker(
               self._client.send_read_receipt(event.room_id, event.event_id),
               exclusive=False,
               exit_on_error=False,
           )
   ```

   Also clear the local unread counter and badge in this handler:

   ```python
   self._clear_unread(event.room_id)
   ```

**`src/telemente/tui/widgets/message_view.py`**

1. Add a new Textual message:

```python
class ScrolledToBottom(TextualMessage):
    """Posted when the timeline is scrolled to the bottom."""

    def __init__(self, room_id: str, event_id: str) -> None:
        super().__init__()
        self.room_id = room_id
        self.event_id = event_id
```

2. Add a `newest_event_id` property:

```python
@property
def newest_event_id(self) -> str | None:
    """The event_id of the most recently rendered message, or None."""
    if not self._msgs_by_id:
        return None
    # _msgs_by_id preserves insertion order; return the last key.
    return next(reversed(self._msgs_by_id))
```

3. Modify `_scroll_to_bottom` to post `ScrolledToBottom` when appropriate:

```python
def _scroll_to_bottom(self) -> None:
    """Scroll the timeline to the bottom."""
    timeline = self.query_one("#message-timeline", VerticalScroll)
    timeline.scroll_end(animate=False)
    newest = self.newest_event_id
    if newest and self._current_room_id:
        self.post_message(
            self.ScrolledToBottom(self._current_room_id, newest)
        )
```

**`tests/fakes.py`**

Add to `FakeMatrixClient`:

```python
# Spy: list of (room_id, event_id) tuples for send_read_receipt calls.
self.sent_receipts: list[tuple[str, str]] = []
```

Add the method:

```python
async def send_read_receipt(self, room_id: str, event_id: str) -> None:
    if not self.logged_in:
        raise NotLoggedInError("Not logged in")
    self._check_fail("send_read_receipt")
    await self._maybe_block("send_read_receipt")
    self.sent_receipts.append((room_id, event_id))
```

Update `reset_spies`:

```python
self.sent_receipts.clear()
```

---

## Implementation steps

1. Add `send_read_receipt` to `MatrixClient` in `client.py`.
2. Add `send_read_receipt` spy to `FakeMatrixClient` in `tests/fakes.py`.
3. Add `ScrolledToBottom` message and `newest_event_id` property to
   `MessageView`.
4. Modify `_scroll_to_bottom` to post `ScrolledToBottom`.
5. Add `send_read_receipt` to `_MainClient` protocol in `main.py`.
6. Wire the two call sites in `MainScreen` (`_open_tab` and
   `on_message_view_scrolled_to_bottom`).
7. Write tests before steps 1–6.

---

## Tests

### Tier 1 — `tests/matrix/test_client_receipts.py`

```python
async def test_send_read_receipt_posts_to_correct_url(
    restore_client, aioresponses_ctx
) -> None:
    """send_read_receipt POSTs to /receipt/m.read/{eventId} and returns cleanly."""

async def test_send_read_receipt_not_logged_in_raises(
    restore_client
) -> None:
    """send_read_receipt raises NotLoggedInError when not logged in."""

async def test_send_read_receipt_http_error_logs_warning_no_raise(
    restore_client, aioresponses_ctx
) -> None:
    """A non-200 HTTP response from the server logs a warning and does not raise."""

async def test_send_read_receipt_network_error_logs_warning_no_raise(
    restore_client, aioresponses_ctx
) -> None:
    """A network exception (e.g. aiohttp.ClientError) logs a warning and does
    not propagate."""
```

Setup: use `restore_client()` for an authenticated `MatrixClient`; stub the
POST endpoint with `aioresponses` returning `200 {}` (or error variants).

### Tier 2 — `tests/tui/test_read_receipts.py`

```python
async def test_read_receipt_sent_on_room_open() -> None:
    """When a room is opened (tab created), send_read_receipt is called with the
    most recent message's event_id."""

async def test_read_receipt_sent_on_scroll_to_bottom() -> None:
    """When action_scroll_latest is triggered, send_read_receipt is called."""

async def test_read_receipt_not_sent_for_empty_room() -> None:
    """Opening a room with no messages does not call send_read_receipt."""

async def test_read_receipt_clears_unread_badge() -> None:
    """After send_read_receipt is called for the active room, the unread badge
    for that room is zeroed in the RoomList."""

async def test_read_receipt_only_for_active_room() -> None:
    """send_read_receipt is called for the room that was just opened, not for
    background rooms."""
```

Setup: build a host app with `FakeMatrixClient`; pre-populate
`fake.messages_data` with at least one message; open a room and assert
`fake.sent_receipts` contains `(room_id, newest_event_id)` after
`wait_for_workers(app)`.

---

## Done-when checklist

- [ ] `MatrixClient.send_read_receipt` exists and POSTs to the correct endpoint.
- [ ] `FakeMatrixClient.send_read_receipt` records calls in `sent_receipts`.
- [ ] `MessageView.ScrolledToBottom` message is posted after every
      `_scroll_to_bottom` call when a room is loaded.
- [ ] `MainScreen._open_tab` calls `send_read_receipt` after loading a room.
- [ ] `MainScreen.on_message_view_scrolled_to_bottom` calls `send_read_receipt`.
- [ ] Unread badge is cleared when a receipt is sent.
- [ ] All Tier 1 and Tier 2 tests listed above pass.
- [ ] `uv run ruff check .` / `uv run ruff format .` clean.
- [ ] `uv run mypy` clean.
- [ ] `pyright src/` clean.
- [ ] `uv run pytest` all green.
