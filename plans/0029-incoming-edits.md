# Plan 0029 — Incoming message edits (m.replace live update)

## P0 Dependency Graph

This section maps the five P0 plans (0029–0033) onto their shared infrastructure
and suggests an implementation order.

### Shared infrastructure analysis

**MessageEdited event (0029) and ReactionsChanged event (0030)** both need a
new `ClientEvent` subtype and a new `_on_*` callback in `MatrixClient`. They
share no code, but both touch `_register_callbacks` and `ClientEvent` union in
`client.py`, and both require `MessageRow` to accept an "update" signal in
`message_view.py`. They can be worked in parallel by two engineers; neither
blocks the other.

**Read receipts (0031)** needs `MatrixClient.send_read_receipt()`. It has a
dependency on knowing the latest event_id for a room, which is already
accessible via `messages()` return values. It does NOT depend on pagination
(0032): receipts mark the most-recent loaded message regardless of how many
messages are loaded. Completely independent of 0029 and 0030.

**Pagination (0032)** extends `MatrixClient.messages()` to accept a `before`
token, exposes `MatrixClient.load_older_messages()`, and requires
`MessageView` to detect scroll-to-top and trigger a load. It builds on the
existing cache and `room_messages` path. Does not depend on 0029, 0030, or
0031 at all.

**Join room (0033)** adds `MatrixClient.join_room()`, a `JoinRoomScreen` dialog,
and a command palette entry. Completely independent of all other four plans.

### Parallel pairs

The following pairs can be worked simultaneously:

- **0029 + 0030**: both are "incoming live event" handlers; different nio event
  types, different `ClientEvent` subtypes, same general pattern.
- **0031 + 0033**: no shared files at all; both touch `client.py` (different
  methods), both touch `FakeMatrixClient` (different spies).
- **0032** is self-contained and can be worked at any point after the plan
  is read.

### Suggested order

If one engineer: 0029 → 0030 → 0031 → 0033 → 0032

If two engineers: (0029, 0030) in parallel → then (0031, 0033) in parallel → then 0032

The rationale for 0032 last is that it requires the most invasive widget change
(scroll detection + prepend-to-timeline logic).

---

## Goal

When another user edits a message (`rel_type: m.replace`) during an active
session, the original message row in the open `MessageView` updates its body
in place. Currently the `_on_room_message` callback skips all `m.replace`
events with an early return, so edits from other users are silently lost.

---

## Dependencies

- Plans 0001–0028 complete.
- No additional runtime dependencies.

---

## Architecture

### What changes

**`src/telemente/matrix/client.py`**

1. Add a new `ClientEvent` subtype:

```python
@dataclass(frozen=True, slots=True)
class MessageEdited:
    """An existing message was edited (m.replace)."""

    room_id: str
    original_event_id: str  # the event that was replaced
    new_body: str
    editor_user_id: str
```

2. Extend the `ClientEvent` union:

```python
ClientEvent = RoomsChanged | NewMessage | MembersChanged | TypingChanged | MessageRedacted | MessageEdited
```

3. Add `_on_edit_message` callback in `_register_callbacks`:

```python
self._client.add_event_callback(self._on_edit_message, nio.RoomMessageText)
```

Note: nio fires `_on_room_message` and `_on_edit_message` for the same
`nio.RoomMessageText` event type. Both callbacks are registered; the existing
`_on_room_message` already returns early for `rel_type == "m.replace"`, so
there is no double-processing. The edit callback does the opposite: it returns
early if `rel_type != "m.replace"`.

4. Implement `_on_edit_message`:

```python
async def _on_edit_message(
    self, room: nio.MatrixRoom, event: nio.RoomMessageText
) -> None:
    rel = event.source.get("content", {}).get("m.relates_to", {})
    if rel.get("rel_type") != "m.replace":
        return
    original_event_id: str = rel.get("event_id", "")
    if not original_event_id:
        logger.warning("_on_edit_message: m.replace event has no event_id")
        return
    # The new body lives in m.new_content, with fallback to body.
    new_content = event.source.get("content", {}).get("m.new_content", {})
    new_body: str = new_content.get("body", event.body)
    logger.debug(
        "_on_edit_message: room=%s original=%s editor=%s",
        room.room_id, original_event_id, event.sender,
    )
    if self._cache is not None:
        await self._cache.update_body(room.room_id, original_event_id, new_body)
    await self._emit(
        MessageEdited(
            room_id=room.room_id,
            original_event_id=original_event_id,
            new_body=new_body,
            editor_user_id=event.sender,
        )
    )
```

**`src/telemente/matrix/cache.py`**

Add `update_body(room_id, event_id, new_body)` method that executes
`UPDATE messages SET body=? WHERE room_id=? AND event_id=?`. Returns
`True` if a row was updated, `False` otherwise.

**`src/telemente/tui/screens/main.py`**

1. Import `MessageEdited` from `telemente.matrix.client`.
2. Add `handle_message_edited` to `_MainClient` protocol:
   ```python
   # No change needed — events are dispatched via the subscribe callback.
   ```
   Actually, `MainScreen` subscribes to all `ClientEvent`s via `app.py`'s
   dispatch. Add a handler method mirroring the existing `handle_redaction`
   pattern:

```python
def handle_message_edited(self, event: MessageEdited) -> None:
    view = self.message_view_for(event.room_id)
    if view is not None:
        view.apply_edit(event.original_event_id, event.new_body)
    thread_panel = self.query_one(ThreadPanel)
    if self.thread_visible and thread_panel.room_id == event.room_id:
        thread_panel.apply_edit(event.original_event_id, event.new_body)
```

**`src/telemente/tui/widgets/message_view.py`**

Add a public `apply_edit(original_event_id, new_body)` method:

```python
def apply_edit(self, original_event_id: str, new_body: str) -> None:
    """Apply an incoming edit from another user to an already-rendered row."""
    for row in self.query(MessageRow):
        if row.message.event_id == original_event_id:
            row.update_body(new_body)
            self._msgs_by_id[original_event_id] = row.message
            return
    logger.debug(
        "apply_edit: event_id=%s not in current view (room=%s)",
        original_event_id,
        self._current_room_id,
    )
```

`MessageRow.update_body` already exists and handles the in-place body refresh.

**`src/telemente/tui/widgets/thread_panel.py`**

Add `apply_edit(original_event_id, new_body)` with the same pattern as
`MessageView.apply_edit`.

**`src/telemente/tui/app.py`**

Route `MessageEdited` events to `MainScreen.handle_message_edited` in the
subscriber dispatch (same pattern used for `MessageRedacted`).

**`tests/fakes.py`**

Add to `FakeMatrixClient`:
- `sent_edits_received: list[tuple[str, str, str]] = []` spy (not needed —
  `MessageEdited` is emitted _by_ the client on incoming events, not outgoing.
  The fake's `emit()` method covers pushing scripted `MessageEdited` events.)

No new methods needed on `FakeMatrixClient`; tests use `fake.emit(MessageEdited(...))`.

---

## Implementation steps

1. Add `MessageEdited` dataclass to `client.py`; extend `ClientEvent` union.
2. Add `update_body` to `cache.py`.
3. Register `_on_edit_message` callback in `_register_callbacks`; implement the
   callback body.
4. Add `apply_edit` to `MessageView`.
5. Add `apply_edit` to `ThreadPanel`.
6. Add `handle_message_edited` to `MainScreen`; route in `app.py`.
7. Add `MessageEdited` import to `main.py` and add it to `_MainClient`-adjacent
   imports.
8. Add `MessageEdited` to nio stubs if necessary (it is a `ClientEvent`, not a
   nio type — no stubs change needed).
9. Write tests (see below) before implementing steps 2–8.

---

## Tests

### Tier 1 — `tests/matrix/test_client_edits.py`

```python
async def test_edit_event_emits_message_edited(
    restore_client, event_callback_for
) -> None:
    """When a m.replace RoomMessageText arrives, MatrixClient emits MessageEdited."""

async def test_edit_event_updates_cache(
    restore_client, event_callback_for
) -> None:
    """An incoming m.replace event calls cache.update_body for the original event_id."""

async def test_non_replace_event_does_not_emit_message_edited(
    restore_client, event_callback_for
) -> None:
    """A plain RoomMessageText (no m.replace rel_type) does not emit MessageEdited."""

async def test_edit_event_missing_event_id_is_ignored(
    restore_client, event_callback_for
) -> None:
    """A m.replace event with no event_id in m.relates_to is silently dropped."""

async def test_edit_uses_new_content_body_when_present(
    restore_client, event_callback_for
) -> None:
    """MessageEdited.new_body comes from m.new_content.body, not the fallback body."""
```

Setup for all Tier 1 tests: use `restore_client()` to get an authenticated
client with a test room; use `event_callback_for(client, nio.RoomMessageText)`
to get the callback; call it with a fabricated `nio.RoomMessageText` event whose
`source["content"]["m.relates_to"]["rel_type"] == "m.replace"` and
`"event_id": "$original:example.org"`.

### Tier 2 — `tests/tui/test_incoming_edits.py`

```python
async def test_incoming_edit_updates_message_row() -> None:
    """When a MessageEdited event arrives via fake.emit, the original MessageRow
    updates its displayed body in place."""

async def test_incoming_edit_for_other_room_is_ignored() -> None:
    """A MessageEdited event for a room that is not open does not cause errors."""

async def test_incoming_edit_for_unknown_event_id_is_silent() -> None:
    """A MessageEdited event whose original_event_id is not in the current view
    logs at debug and does not raise."""

async def test_incoming_edit_does_not_add_extra_rows() -> None:
    """After applying an edit, the message timeline contains the same number of
    rows as before the edit."""

async def test_incoming_edit_updates_thread_panel_row() -> None:
    """If a thread panel is open and the edited message belongs to that thread,
    the thread panel row is also updated."""
```

Setup for Tier 2 tests: build a minimal `App` subclass that owns a
`FakeMatrixClient`, mounts a `MessageView` or `MainScreen`, loads a room with
one message, then calls `await fake.emit(MessageEdited(...))` and asserts the
rendered text.

---

## Done-when checklist

- [ ] `MessageEdited` dataclass exists in `client.py` and is in `ClientEvent`.
- [ ] `MatrixClient._on_edit_message` is registered and emits `MessageEdited`
      for incoming `m.replace` events.
- [ ] `MatrixClient._on_room_message` continues to skip `m.replace` events
      (no regression).
- [ ] `cache.update_body` updates the SQLite row when called.
- [ ] `MessageView.apply_edit` updates the `MessageRow` body in place.
- [ ] `ThreadPanel.apply_edit` updates thread rows in place.
- [ ] `MainScreen.handle_message_edited` routes to the correct view/panel.
- [ ] All Tier 1 and Tier 2 tests listed above pass.
- [ ] `uv run ruff check .` / `uv run ruff format .` clean.
- [ ] `uv run mypy` clean.
- [ ] `pyright src/` clean.
- [ ] `uv run pytest` all green.
