# Plan 0022 — Redacted message tombstone

## Goal

When a Matrix `m.room.redaction` event arrives (live sync or backfill), replace
the matching message row's content with `🗑️ Message deleted` in-place rather
than removing the row from the timeline. The row stays in the DOM, dimmed and
italic, matching the UX of Element, Signal, and other modern clients.

## Dependencies

No hard dependency on previous plans, but assumes the codebase is at the state
left by plan 0021 (context menu fixes).

---

## Background and design decisions

### Two sources of redaction

**Live redaction** — during `sync_forever`, nio fires
`add_event_callback` for `nio.RedactionEvent` (type `m.room.redaction`).
`RedactionEvent.redacts` is the event_id of the message that was redacted;
`RedactionEvent.sender` is who performed the redaction. This is the primary
path for real-time UX.

**Backfill redaction** — `room_messages` returns events that the server has
already redacted server-side. nio parses these as `nio.RedactedEvent` (NOT
`RedactionEvent`) — it detects `unsigned.redacted_because` in `parse_event()`
and routes to `RedactedEvent.from_dict()`. `RedactedEvent` carries `.type` (the
original event type, e.g. `m.room.message`), `.redacter` (user_id who redacted),
and `.reason`. It does NOT carry `redacts` — the redacted event_id is the
event's own `event_id` field inherited from `Event`.

The existing `sync_with_redaction.json` fixture uses the live path
(`m.room.redaction` in the timeline). The existing
`room_messages_with_redacted.json` fixture uses the backfill path (a
`m.room.message` with `unsigned.redacted_because`).

### Why `MessageRedacted` instead of reusing `NewMessage`

A `NewMessage` with `redacted=True` would work for backfill (where the message
arrives already tombstoned). But live redactions arrive as a separate
`RedactionEvent` that targets an already-rendered row — there is no message
body to carry. A distinct `MessageRedacted(room_id, event_id, redacted_by)`
event cleanly separates "a new message arrived" from "an existing message was
removed". The UI handler can look up the row by `event_id` and mutate it in
place without knowing anything about the original content.

### `_do_redact_and_remove` fate

The existing `_do_redact_and_remove` worker in `MessageView` handles the
**local** optimistic path: the user presses `d`, the app calls
`client.redact_message()`, and if successful the row is removed immediately
(before the echo `RedactionEvent` arrives from the server). After this plan,
the echo will arrive as a `MessageRedacted` event and attempt to call
`update_body` on a row that may no longer exist — which is harmless because
the row won't be in `_rows_by_event_id` anymore (it was removed by
`_do_redact_and_remove`). The existing remove-on-success path therefore stays;
the new tombstone rendering only fires for rows still in the timeline.

### `Message.redacted` field

Adding `redacted: bool = False` to `Message` is cleaner than relying solely on
inspecting the body text. It lets the cache query, the UI, and any future logic
distinguish a tombstone from a message whose text happens to start with `🗑️`.
The field has a default of `False` so all existing construction sites are
unchanged without keyword argument modification. Because `Message` is
`frozen=True, slots=True`, the field must be added in declaration order after
the optional fields.

### Cache strategy

`MessageCache` does not have a `redacted` column; the tombstone body is stored
as the body text `🗑️ Message deleted`. A `mark_redacted(room_id, event_id)`
method executes `UPDATE messages SET body = ? WHERE room_id = ? AND event_id = ?`
using the tombstone string. This is sufficient — on reload the row will
deserialise as a `Message(body="🗑️ Message deleted", redacted=False)` (the
`redacted` flag is not persisted because it adds schema complexity for no
benefit: the body text is the canonical indicator once persisted).

---

## Part 1 — `MessageRedacted` ClientEvent and Tier-1 tests

### 1.1 Add `RedactionEvent` and `RedactedEvent` to `stubs/nio/__init__.pyi`

The stubs currently do not define either type. Add them before implementing the
callback so mypy and pyright pass.

```python
# stubs/nio/__init__.pyi  (add after ReactionEvent)

class RedactionEvent(Event):
    redacts: str
    reason: str | None

class RedactedEvent(Event):
    type: str          # original event type, e.g. "m.room.message"
    redacter: str      # user_id who performed the redaction
    reason: str | None
```

### 1.2 Add `MessageRedacted` to `matrix/client.py`

Add the dataclass and extend `ClientEvent`:

```python
# matrix/client.py  — add after TypingChanged

@dataclass(frozen=True, slots=True)
class MessageRedacted:
    """A previously-sent message was redacted (deleted) in a room."""
    room_id: str
    event_id: str      # the event that was redacted
    redacted_by: str   # event_id of the redaction event (empty for backfill)

ClientEvent = RoomsChanged | NewMessage | MembersChanged | TypingChanged | MessageRedacted
```

Update the import in `tui/app.py`:

```python
from telemente.matrix.client import (
    ClientEvent,
    MatrixClient,
    MembersChanged,
    MessageRedacted,
    NewMessage,
    RoomsChanged,
    TypingChanged,
)
```

### 1.3 Register `_on_redaction` callback in `MatrixClient._register_callbacks()`

```python
# matrix/client.py  _register_callbacks

self._client.add_event_callback(self._on_redaction, nio.RedactionEvent)
```

Implement the callback:

```python
async def _on_redaction(self, room: nio.MatrixRoom, event: nio.RedactionEvent) -> None:
    """nio callback: an m.room.redaction event arrived."""
    logger.debug(
        "_on_redaction: room=%s redacts=%s redacted_by=%s",
        room.room_id,
        event.redacts,
        event.event_id,
    )
    if self._cache is not None:
        await self._cache.mark_redacted(room.room_id, event.redacts)
    await self._emit(MessageRedacted(
        room_id=room.room_id,
        event_id=event.redacts,
        redacted_by=event.event_id,
    ))
```

### 1.4 Handle `RedactedEvent` in `messages()` (backfill path)

In the `for event in response.chunk:` loop inside `MatrixClient.messages()`,
add a branch before the `RoomMessageText` branch:

```python
elif isinstance(event, nio.RedactedEvent):
    # Server-side pre-redacted message: surface as a tombstone row.
    sender_display_name = _get_display_name(room, event.sender)
    ts = datetime.fromtimestamp(event.server_timestamp / 1000, tz=UTC)
    raw_messages.append(
        Message(
            event_id=event.event_id,
            room_id=room_id,
            sender=event.sender,
            sender_display_name=sender_display_name,
            body="\U0001f5d1️ Message deleted",
            timestamp=ts,
            redacted=True,
        )
    )
```

The `isinstance(event, nio.RedactedEvent)` check must come **before**
`isinstance(event, nio.RoomMessageText)` because `RedactedEvent` is a subclass
of `Event`, not of `RoomMessageText`, but the order matters in case nio ever
changes the hierarchy.

### 1.5 Tier-1 test cases — `tests/matrix/test_redaction.py` (new file)

All tests use `restore_client()` + `event_callback_for()` from `helpers.py`.
No `aioresponses` is needed for the callback-driven tests; use a mock nio
client.

**`test_redaction_callback_emits_message_redacted`**
- `restore_client(nio_mock)` → client
- Subscribe to collect events: `events: list[ClientEvent] = []`
- Get callback: `cb = event_callback_for(nio_mock, nio.RedactionEvent)`
- Make a fake `RedactionEvent` with `.redacts = "$ev1"`, `.event_id = "$r1"`,
  `.sender = "@alice:example.com"`, room with `.room_id = "!r:s"`
- `await cb(room, redaction_event)`
- Assert `len(events) == 1` and `isinstance(events[0], MessageRedacted)`
- Assert `events[0].event_id == "$ev1"` and `events[0].redacted_by == "$r1"`
  and `events[0].room_id == "!r:s"`

**`test_redaction_callback_calls_cache_mark_redacted`**
- Same setup but inject a `MessageCache` mock as `client._cache`
- After invoking the callback, assert
  `cache_mock.mark_redacted.call_args == (("!r:s", "$ev1"),)`

**`test_backfill_redacted_event_returns_tombstone`**
- `restore_client(nio_mock)` where `nio_mock.room_messages` returns a
  `RoomMessagesResponse` whose `chunk` contains a `nio.RedactedEvent` built
  from the `room_messages_with_redacted.json` fixture
- Actually: stub `nio_mock.room_messages` to return a fake response whose chunk
  is parsed from the fixture JSON using `nio.Event.parse_event()` on the first
  chunk entry — this exercises the real nio parsing path
- Call `await client.messages("!room:example.com")`
- Assert the returned list has one `Message` with
  `body == "🗑️ Message deleted"` and `redacted == True`

**`test_redaction_event_fires_during_sync`**
- Use `aioresponses` + `stub_sync(m, load_fixture("sync_with_redaction.json"))`
- Subscribe; call `start_sync_and_close` helper
- Assert a `MessageRedacted` event was emitted with
  `event_id == "$ev1:example.com"` and `room_id == "!room_a:example.com"`

---

## Part 2 — `Message.redacted` field and cache integration

### 2.1 Add `redacted: bool = False` to `Message`

File: `src/telemente/matrix/models.py`

Add the field after `reply_to_event_id`:

```python
@dataclass(frozen=True, slots=True)
class Message:
    event_id: str
    room_id: str
    sender: str
    sender_display_name: str
    body: str
    timestamp: datetime
    media_url: str | None = None
    media_type: str | None = None
    reactions: dict[str, list[str]] = field(default_factory=lambda: {})
    reply_to_event_id: str | None = None
    redacted: bool = False
```

No change needed to `_to_row` / `_from_row` in `cache.py` — `redacted` is not
persisted (the body text carries the tombstone signal once stored).

### 2.2 Add `MessageCache.mark_redacted(room_id, event_id)` to `cache.py`

```python
async def mark_redacted(self, room_id: str, event_id: str) -> None:
    """Overwrite the body of a cached message with the tombstone string."""
    assert self._db is not None
    await self._db.execute(
        "UPDATE messages SET body = ? WHERE room_id = ? AND event_id = ?",
        ("\U0001f5d1️ Message deleted", room_id, event_id),
    )
    await self._db.commit()
```

### 2.3 Tier-1 cache test — `tests/matrix/test_message_cache.py` (extend existing)

**`test_mark_redacted_updates_body`**
- Open a temp-file cache, `await cache.put(msg)` where `msg.body = "original"`
- `await cache.mark_redacted(msg.room_id, msg.event_id)`
- `rows = await cache.get_room(msg.room_id)`
- Assert `rows[0].body == "🗑️ Message deleted"`

---

## Part 3 — `MessageView` tombstone rendering (Tier-2 tests)

### 3.1 Add `handle_redaction(event: MessageRedacted)` to `MessageView`

File: `src/telemente/tui/widgets/message_view.py`

```python
def handle_redaction(self, event: MessageRedacted) -> None:
    """Update the row for a redacted message in-place, if it is loaded."""
    if event.room_id != self._current_room_id:
        return
    for row in self.query(MessageRow):
        if row.message.event_id == event.event_id:
            row.update_body("\U0001f5d1️ Message deleted")
            row.add_class("-redacted")
            self._msgs_by_id.pop(event.event_id, None)
            return
    logger.debug(
        "handle_redaction: event_id=%s not in current timeline (already removed or not loaded)",
        event.event_id,
    )
```

Notes:
- `row.update_body()` already exists and calls `_refresh_body_static()`.
- `row.add_class("-redacted")` adds the CSS modifier class for visual dimming.
- Remove the `_msgs_by_id` entry so reply-quote lookups don't resolve against
  the tombstone text.
- `_rendered_event_ids` is NOT cleared — the row is still in the DOM; deduplication
  should remain active so a sync echo of the redaction doesn't re-append the original
  message.

### 3.2 Import `MessageRedacted` in `message_view.py`

Add to the import block:

```python
from telemente.matrix.client import MessageRedacted
```

Note: `_MessageViewClient` protocol does not need extension — `MessageRedacted`
is delivered by the external `subscribe` event stream and routed into
`MessageView.handle_redaction` from `MainScreen`, not called on the client.

### 3.3 Tier-2 test cases — `tests/tui/test_message_view.py` (extend existing)

Use `TypingHostApp` as a pattern reference: create a `RedactionHostApp` that
subscribes the fake client and routes `MessageRedacted` to
`view.handle_redaction(event)`.

```python
class RedactionHostApp(App[None]):
    def __init__(self, client: FakeMatrixClient, room_id: str) -> None:
        super().__init__()
        self._client = client
        self._room_id = room_id

    def compose(self) -> ComposeResult:
        yield MessageView(self._client, id="message-panel")

    def on_mount(self) -> None:
        self._client.subscribe(self._handle_event)
        view = self.query_one(MessageView)
        view._current_room_id = self._room_id

    def _handle_event(self, event: object) -> None:
        from telemente.matrix.client import MessageRedacted
        if isinstance(event, MessageRedacted):
            self.query_one(MessageView).handle_redaction(event)
```

**`test_redaction_event_replaces_body_with_tombstone`**
- Load room with one message `event_id="$e1"`, body `"hello"`
- Emit `MessageRedacted(room_id=..., event_id="$e1", redacted_by="$r1")`
- `await pilot.pause()`
- Assert `"🗑️ Message deleted"` is in `_rendered_text(view)`
- Assert `"hello"` is NOT in `_rendered_text(view)` (body replaced, not appended)

**`test_redaction_event_adds_redacted_css_class`**
- Same setup as above
- After emit + pause, query the `MessageRow` and assert
  `"-redacted" in row.classes`

**`test_redaction_for_other_room_is_ignored`**
- Load `"!a:s"` room, one message `event_id="$e1"`
- Emit `MessageRedacted(room_id="!b:s", event_id="$e1", redacted_by="$r1")`
- Assert the row body is still `"hello"` (tombstone not applied cross-room)

**`test_redaction_for_unknown_event_id_is_silent`**
- Load room with one message `event_id="$e1"`
- Emit `MessageRedacted(room_id=..., event_id="$unknown", redacted_by="$r1")`
- `await pilot.pause()`
- Assert no exception raised; original message still rendered unchanged

**`test_do_redact_and_remove_still_removes_row`**
- Existing test 19 (`test_delete_binding_removes_row`) already covers this path.
  Verify it is still green after the new tombstone path is added to confirm the
  two paths do not conflict.

---

## Part 4 — CSS, routing through `app.py` / `MainScreen`, and `FakeMatrixClient`

### 4.1 CSS modifier in `tui/styles/app.tcss`

```css
MessageRow.-redacted {
    color: $text-disabled;
    text-style: italic;
}
```

Add this block after the existing `MessageRow` rules. `$text-disabled` is a
Textual built-in design token that maps to a muted grey in all built-in themes.

### 4.2 Routing in `tui/app.py`

Following the exact pattern used for `TypingChanged`:

**Add `_ClientMessageRedacted` Textual message wrapper** (alongside
`_ClientTypingChanged`):

```python
class _ClientMessageRedacted(TextualMessage):
    """Wraps a MessageRedacted client event for Textual message routing."""
    def __init__(self, event: MessageRedacted) -> None:
        super().__init__()
        self.event = event
```

**Extend `_on_client_event`** to handle `MessageRedacted`:

```python
elif isinstance(event, MessageRedacted):
    logger.debug(
        "ClientEvent: MessageRedacted room=%s event_id=%s",
        event.room_id,
        event.event_id,
    )
    self.post_message(_ClientMessageRedacted(event))
```

**Add handler** (alongside `on__client_typing_changed`):

```python
def on__client_message_redacted(self, message: _ClientMessageRedacted) -> None:
    screen = self.screen
    if not isinstance(screen, MainScreen):
        return
    screen.handle_redaction(message.event)
```

### 4.3 Routing in `tui/screens/main.py`

**Update import**:

```python
from telemente.matrix.client import MembersChanged, MessageRedacted, NewMessage, RoomsChanged, TypingChanged
```

**Add `handle_redaction` method** (alongside `handle_typing_changed`):

```python
def handle_redaction(self, event: MessageRedacted) -> None:
    logger.debug("handle_redaction: room=%s event_id=%s", event.room_id, event.event_id)
    view = self.message_view_for(event.room_id)
    if view is not None:
        view.handle_redaction(event)
```

### 4.4 `FakeMatrixClient` — `auto_emit_redactions` flag

File: `tests/fakes.py`

Add field in `__init__`:

```python
# §2.3.x Auto-emit on redact_message (plan 0022)
self.auto_emit_redactions: bool = False
```

Update `redact_message` to optionally emit:

```python
async def redact_message(self, room_id: str, event_id: str, reason: str = "") -> None:
    if not self.logged_in:
        raise NotLoggedInError("Not logged in")
    self._check_fail("redact_message")
    await self._maybe_block("redact_message")
    self.redacted_messages.append((room_id, event_id))
    if self.auto_emit_redactions:
        from telemente.matrix.client import MessageRedacted
        await self.emit(MessageRedacted(
            room_id=room_id,
            event_id=event_id,
            redacted_by="$fake_redact",
        ))
```

---

## Files to create / modify

| File | Action | Changes |
|---|---|---|
| `stubs/nio/__init__.pyi` | Modify | Add `RedactionEvent` and `RedactedEvent` class stubs |
| `src/telemente/matrix/models.py` | Modify | Add `redacted: bool = False` field to `Message` |
| `src/telemente/matrix/cache.py` | Modify | Add `mark_redacted(room_id, event_id)` method |
| `src/telemente/matrix/client.py` | Modify | Add `MessageRedacted` dataclass; extend `ClientEvent` union; add `_on_redaction` callback; register it in `_register_callbacks`; handle `RedactedEvent` in `messages()` |
| `src/telemente/tui/app.py` | Modify | Add `_ClientMessageRedacted` wrapper; handle in `_on_client_event`; add `on__client_message_redacted` |
| `src/telemente/tui/screens/main.py` | Modify | Import `MessageRedacted`; add `handle_redaction` method |
| `src/telemente/tui/widgets/message_view.py` | Modify | Import `MessageRedacted`; add `handle_redaction(event)` method |
| `src/telemente/tui/styles/app.tcss` | Modify | Add `MessageRow.-redacted` CSS rule |
| `tests/fakes.py` | Modify | Add `auto_emit_redactions` flag; update `redact_message` |
| `tests/matrix/test_redaction.py` | Create | Four Tier-1 tests (callback, cache, backfill, sync) |
| `tests/matrix/test_message_cache.py` | Modify | Add `test_mark_redacted_updates_body` |
| `tests/tui/test_message_view.py` | Modify | Add `RedactionHostApp`; add five Tier-2 tests |

---

## Done-when checklist

- [ ] `stubs/nio/__init__.pyi` defines `RedactionEvent` with `.redacts: str` and
  `RedactedEvent` with `.type`, `.redacter`, `.reason`.
- [ ] `Message.redacted: bool = False` compiles under `mypy --strict` and
  `pyright src/` with no `Any` leaks.
- [ ] `MessageCache.mark_redacted` method exists; `test_mark_redacted_updates_body`
  is green.
- [ ] `MatrixClient._register_callbacks()` registers `_on_redaction` for
  `nio.RedactionEvent`.
- [ ] `test_redaction_callback_emits_message_redacted` is green.
- [ ] `test_redaction_callback_calls_cache_mark_redacted` is green.
- [ ] `test_backfill_redacted_event_returns_tombstone` is green.
- [ ] `test_redaction_event_fires_during_sync` is green (uses
  `sync_with_redaction.json` fixture).
- [ ] `MessageView.handle_redaction` exists; calls `row.update_body` and
  `row.add_class("-redacted")`.
- [ ] `test_redaction_event_replaces_body_with_tombstone` is green.
- [ ] `test_redaction_event_adds_redacted_css_class` is green.
- [ ] `test_redaction_for_other_room_is_ignored` is green.
- [ ] `test_redaction_for_unknown_event_id_is_silent` is green.
- [ ] `test_delete_binding_removes_row` (existing test 19) is still green.
- [ ] `app.py` routes `MessageRedacted` → `MainScreen.handle_redaction` →
  `MessageView.handle_redaction`.
- [ ] `MessageRow.-redacted` CSS rule in `app.tcss` sets `color: $text-disabled;
  text-style: italic;`.
- [ ] `FakeMatrixClient.auto_emit_redactions` emits `MessageRedacted` after
  recording the call.
- [ ] `uv run ruff check .` clean.
- [ ] `uv run ruff format .` clean.
- [ ] `uv run mypy` clean.
- [ ] `pyright src/` clean.
- [ ] `uv run pytest` all green.
