# Plan 0023 — Reply thread panel

## Goal

When a user opens a thread from the context menu on a message, a `ThreadPanel`
slides in between the active `MessageView` and the `MembersPanel`, showing all
messages in that thread in chronological order.  The panel is closeable (Escape
or a close button) and does not replace the main timeline.

---

## Dependencies

- Plans 0001–0022 complete (assumes codebase state after plan 0022).
- Plan 0013 (`MessageCache` / SQLite) — cache integration for thread messages.
- No dependency on plan 0024 (both touch `MessageView` but independently).

---

## Background and motivation

Matrix has two related mechanisms for threading:

### Legacy replies (`m.in_reply_to`)

Any event may carry `content["m.relates_to"]["m.in_reply_to"]["event_id"]`
pointing at another event.  This is a flat, one-level reference: the reply
knows its parent but the parent does not know its children.  To reconstruct a
"reply chain" starting from any message, the client must walk parent pointers
hop-by-hop, fetching each ancestor event individually.  There is no server-side
aggregation for this mechanism.

`send_text` in `client.py` already sets `m.in_reply_to` when
`reply_to_event_id` is passed.  `Message.reply_to_event_id` carries this
pointer.  The `messages()` backfill path already parses it from
`event.source["content"]["m.relates_to"]["m.in_reply_to"]["event_id"]`.

### MSC3440 threads (`rel_type: m.thread`, stable since Matrix 1.4)

When a user explicitly sends a message into a thread, the event carries:

```json
{
  "m.relates_to": {
    "rel_type": "m.thread",
    "event_id": "<thread_root_event_id>",
    "m.in_reply_to": {"event_id": "<last_event_in_thread>"},
    "is_falling_back": true
  }
}
```

All events in a thread share the same `rel_type: m.thread` / `event_id`
(the root).  The homeserver aggregates them and returns them via:

```
GET /_matrix/client/v1/rooms/{roomId}/relations/{rootEventId}/m.thread
```

**nio support**: `AsyncClient.room_get_event_relations()` is an `async`
generator that paginates through `RoomEventRelationsResponse.events` for a
given `rel_type`.  `RelationshipType.thread` (value `"m.thread"`) is defined in
`nio.api`.  This is the correct API for fetching thread replies from the server.
Neither `RelationshipType` nor `RoomEventRelationsResponse` are currently in
`stubs/nio/__init__.pyi` — they must be added.

### Which mechanism to support first

**This plan implements MSC3440 threads only.**

Rationale:
- Server-side aggregation via `room_get_event_relations` gives us all thread
  messages in one (paginated) request, with no client-side chain reconstruction.
- Legacy reply chains require hop-by-hop event fetching
  (`room_get_event`, not yet in the stubs) and have no thread root concept —
  they form implicit trees, not named threads.  That is a separate plan.
- In practice, modern Matrix clients (Element, FluffyChat, Cinny) send
  explicit `m.thread` events; `m.in_reply_to` is used only for single replies.

### Layout

Current layout in `MainScreen.compose()`:

```
Horizontal(
    RoomList         #rooms-panel
    TabbedContent    #message-panel   (contains MessageView widgets)
    MemberList       #members-panel
)
```

The `ThreadPanel` inserts **between** `TabbedContent` and `MemberList`.  It
is hidden by default (`display: none`) and revealed when a thread is opened.
This matches the plan 0014 pattern for `LogPanel` (docked below, toggled via
`display`) and avoids restructuring the `Horizontal` layout.

Because `Horizontal` distributes width with `1fr`, adding a hidden widget has
no effect on the existing layout.  When visible, `ThreadPanel` gets a fixed
width (e.g. 40 columns), the same pattern as `#rooms-panel` and
`#members-panel`.

`MainScreen` gains a `thread_visible: reactive[bool]` watcher (matching
`rooms_visible` / `members_visible`).

### No new `ClientEvent` needed

Fetching thread messages is a **user-initiated synchronous query**, not a
push event from the server.  The flow is:
1. User opens context menu on a message.
2. "View thread" is in the menu (only if the message has `rel_type: m.thread`
   OR if it is a thread root with replies).
3. `MainScreen` calls `client.get_thread_messages(room_id, root_event_id)`.
4. Results are pushed into `ThreadPanel` directly.

No `ClientEvent` subclass is needed.  The thread panel refreshes only on
explicit user action (open) or when a `NewMessage` with `rel_type: m.thread`
arrives in the active thread (see section on live updates).

---

## Decisions

1. **"View thread" visibility**: Show the menu item only when `message.thread_root_id
   is not None` (i.e. the message itself has `rel_type: m.thread` set). Thread
   root detection (tracking which messages *have* replies pointing at them) is a
   follow-up plan.

2. **Pagination**: Fetch one page (up to `limit=50`). Show a `"Load more"`
   footer widget when `has_more is True`. Full auto-pagination is deferred.

3. **Thread composer**: Include a composer in this plan. `send_text` gains a
   `thread_root_event_id: str | None = None` parameter that adds
   `rel_type: m.thread` to the content when set.

4. **Live updates**: Yes — `handle_new_message` in `MainScreen` forwards
   matching thread messages to `ThreadPanel.append_message` when the panel is
   open.

5. **Graceful degradation**: `get_thread_messages` catches any HTTP error (404,
   M_UNRECOGNIZED, etc.), logs a warning, and returns `([], False)`. No
   exception surfaces to the UI.

---

## Architecture

### New stub additions — `stubs/nio/__init__.pyi`

Add `RelationshipType` enum and the response types needed for thread fetching.
`room_get_event_relations` is a generator so its return type in the stub must
use `AsyncIterator`.

```python
# stubs/nio/__init__.pyi — add after RoomMessagesResponse

from enum import Enum

class RelationshipType(str, Enum):
    replacement: str
    annotation: str
    thread: str
    reference: str

class RoomEventRelationsResponse(Response):
    room_id: str
    parent_event_id: str
    events: list[Event]
    prev_batch: str | None
    next_batch: str | None

class RoomEventRelationsError(ErrorResponse): ...
```

Extend `AsyncClient` stub:

```python
# In AsyncClient stub body
def room_get_event_relations(
    self,
    room_id: str,
    event_id: str,
    rel_type: RelationshipType | None = ...,
    event_type: str | None = ...,
    limit: int | None = ...,
) -> AsyncIterator[Event]: ...
```

`AsyncIterator` requires `from collections.abc import AsyncIterator` at the
top of the stubs file.

### New `Message` field — `thread_root_id: str | None = None`

Add to `matrix/models.py` after `reply_to_event_id`.  This field records the
thread root for messages that are thread replies.  It is populated from:
- `event.source["content"]["m.relates_to"]["event_id"]` when
  `rel_type == "m.thread"` (live sync callback and backfill).
- The `thread_root_event_id` parameter passed to
  `get_thread_messages` (server-fetched thread messages).

The field has a default of `None` so all existing construction sites are
unchanged.

Because `Message` is `frozen=True, slots=True`, the new field goes last (after
`reply_to_event_id`) in the declaration order.

### Cache changes — `MessageCache`

No new columns.  `thread_root_id` is **not** persisted in the cache schema for
this plan; thread messages are always fetched fresh from the server on panel
open.  This avoids a schema migration while keeping correctness: the
homeserver is the source of truth for the thread aggregate.

A follow-up plan may add a `thread_root_id` column and cache thread pages.

`MessageCache.get_room` is unchanged.

### New `MatrixClient` method — `get_thread_messages`

```python
async def get_thread_messages(
    self,
    room_id: str,
    root_event_id: str,
    limit: int = 50,
) -> tuple[list[Message], str | None]:
    """Fetch messages in a thread rooted at root_event_id.

    Uses GET /_matrix/client/v1/rooms/{room_id}/relations/{root_event_id}/m.thread
    via nio's room_get_event_relations() generator.

    Returns (messages_chronological, next_batch).
    next_batch is non-None when more pages exist (not fetched in this call).

    On server error (404, M_UNRECOGNIZED, etc.) returns ([], None) and logs
    a warning — graceful degradation for servers without MSC3440 support.
    """
```

Implementation notes:
- Call `self._client.room_get_event_relations(room_id, root_event_id, rel_type=RelationshipType.thread, limit=limit)`.
- `room_get_event_relations` is an `AsyncIterator[Event]`.  Collect up to
  `limit` events from it, then call `aclose()` to cancel any pending pagination.
- Parse each `Event` identically to the `messages()` backfill path: handle
  `RoomMessageText`, `RoomMessageMedia`, `MegolmEvent`.  Set `thread_root_id=root_event_id`.
- The generator yields events newest-first (default direction `back`); reverse
  to chronological.
- `next_batch` is not directly accessible from the generator protocol; to
  expose pagination, use a single direct call to the internal
  `_send(RoomEventRelationsResponse, ...)` path.  However, that is private.
  **Alternative**: call `room_get_event_relations` with `limit=limit+1`, yield
  at most `limit` events, and set `has_more = True` if the generator yielded
  `limit+1` events.  This is the clean approach without touching private APIs.
  Return `"has_more"` as a `bool` instead of a `next_batch` token.

Revise the signature accordingly:

```python
async def get_thread_messages(
    self,
    room_id: str,
    root_event_id: str,
    limit: int = 50,
) -> tuple[list[Message], bool]:
    """Returns (messages_chronological, has_more)."""
```

Extend `ClientEvent` union: no change — thread fetching is query-only.

Extend `_MessageViewClient` protocol: no change — `ThreadPanel` has its own
protocol.

### New `_ThreadPanelClient` protocol — `tui/widgets/thread_panel.py`

```python
class _ThreadPanelClient(Protocol):
    async def get_thread_messages(
        self, room_id: str, root_event_id: str, limit: int = 50
    ) -> tuple[list[Message], bool]: ...

    async def send_text(
        self, room_id: str, body: str, reply_to_event_id: str | None = None
    ) -> str: ...

    def me(self) -> tuple[str, str]: ...
```

`send_text` is included because the composer in `ThreadPanel` sends replies
into the thread.  A separate `send_thread_reply` method is not needed for the
MVP: the caller sets `reply_to_event_id` to the last event in the thread and
the content will naturally carry `m.in_reply_to`.  Full `m.thread` `rel_type`
attachment (so the message appears in the thread aggregate on the server) is
addressed in OPEN QUESTION 3 — if confirmed, `send_text` must be extended with
a `thread_root_event_id: str | None = None` parameter that adds
`rel_type: m.thread` to the content.

### New `ThreadPanel` widget — `tui/widgets/thread_panel.py`

```python
class ThreadPanel(Widget):
    """Scrollable panel showing the messages in one Matrix thread."""

    class CloseRequested(TextualMessage):
        """Posted when the user presses Escape or clicks the close button."""

    class ThreadReply(TextualMessage):
        """Posted when the user sends a reply from the thread composer."""
        def __init__(self, room_id: str, body: str, reply_to_event_id: str) -> None: ...
```

Layout (from `compose()`):
```
Vertical(
    Horizontal(
        Static("Thread", id="thread-title"),
        Button("✕", id="thread-close"),
        id="thread-header",
    ),
    VerticalScroll(id="thread-messages"),
    Static("", id="thread-reply-quote"),   # hidden unless replying
    TextArea(id="thread-composer"),
    id="thread-panel-inner",
)
```

Key methods:
- `load_thread(room_id: str, root_event_id: str) -> None` — `on_mount` entry
  point, called by `MainScreen` after mounting.  Runs a worker.
- `_do_load(room_id, root_event_id)` — async worker; calls
  `client.get_thread_messages`; populates `#thread-messages` with `MessageRow`
  widgets (reusing the same class from `message_view.py`).
- `append_message(msg: Message) -> None` — called by `MainScreen` when a live
  `NewMessage` with `thread_root_id == self._root_event_id` arrives.
- `close()` — posts `CloseRequested`.
- `action_close_thread()` — bound to Escape, calls `close()`.
- `on_button_pressed(event)` — handles `#thread-close` click → `close()`.

State:
- `_room_id: str`
- `_root_event_id: str`
- `_has_more: bool`
- `_event_ids_rendered: set[str]` — deduplication guard matching
  `MessageView._rendered_event_ids`.

### Layout changes — `MainScreen`

`compose()` becomes:

```python
def compose(self) -> ComposeResult:
    yield Header()
    with Horizontal(id="main-layout"):
        yield RoomList(id="rooms-panel")
        with TabbedContent(id="message-panel"):
            pass
        yield ThreadPanel(self._client, id="thread-panel")   # NEW, hidden initially
        yield MemberList(self._client, id="members-panel")
    yield LogPanel(self._log_file, id="log-panel")
    yield Footer()
```

New reactive:

```python
thread_visible: reactive[bool] = reactive(False)
```

Watcher:

```python
def watch_thread_visible(self, visible: bool) -> None:
    self.query_one("#thread-panel").display = visible
```

New methods:

```python
def open_thread(self, room_id: str, root_event_id: str) -> None:
    """Show the thread panel and load the given thread."""
    panel = self.query_one(ThreadPanel)
    panel.load_thread(room_id, root_event_id)
    self.thread_visible = True

def close_thread(self) -> None:
    self.thread_visible = False
```

Handler for `ThreadPanel.CloseRequested`:

```python
def on_thread_panel_close_requested(self, _: ThreadPanel.CloseRequested) -> None:
    self.close_thread()
```

### Context menu — "View thread" entry

In `MainScreen.on_message_view_show_context_menu`, the context menu is built
from `event.items` that came from `MessageView`.  `MessageView` builds these
items in `_build_context_menu_items(message)`.

Add a "View thread" item when the message has a `thread_root_id` set OR when
it `reply_to_event_id` is set and `rel_type` was `m.thread` (i.e. the message
is itself a thread reply).

Since `Message` now has `thread_root_id`, the condition is simply:

```python
if message.thread_root_id is not None:
    items.append(MenuItem("View thread", lambda: self._open_thread(message.thread_root_id)))
```

`_open_thread` posts a new `MessageView.OpenThread` Textual message that
`MainScreen` handles:

```python
class OpenThread(TextualMessage):
    def __init__(self, room_id: str, root_event_id: str) -> None:
        super().__init__()
        self.room_id = room_id
        self.root_event_id = root_event_id
```

```python
# MainScreen
def on_message_view_open_thread(self, event: MessageView.OpenThread) -> None:
    self.open_thread(event.room_id, event.root_event_id)
```

### `client.py` — parse `thread_root_id` in callbacks and backfill

In `_on_room_message` (live sync callback):

```python
rel = event.source.get("content", {}).get("m.relates_to", {})
thread_root: str | None = None
if rel.get("rel_type") == "m.thread":
    thread_root = rel.get("event_id") or None
message = Message(
    ...,
    reply_to_event_id=reply_to,
    thread_root_id=thread_root,
)
```

Same addition in the `messages()` backfill loop for `RoomMessageText` events.

In the `NewMessage` event: `NewMessage` already carries the full `Message`
dataclass.  `MainScreen` checks if the active `ThreadPanel` cares about this
message:

```python
# MainScreen.handle_new_message (extend existing)
if (
    self.thread_visible
    and msg.thread_root_id is not None
):
    panel = self.query_one(ThreadPanel)
    if msg.thread_root_id == panel._root_event_id and msg.room_id == panel._room_id:
        panel.append_message(msg)
```

### `send_text` extension for thread replies (conditional on OPEN QUESTION 3)

If OPEN QUESTION 3 is confirmed (composer included), extend `send_text`:

```python
async def send_text(
    self,
    room_id: str,
    body: str,
    reply_to_event_id: str | None = None,
    thread_root_event_id: str | None = None,
) -> str:
```

When `thread_root_event_id` is set, the content becomes:

```python
content["m.relates_to"] = {
    "rel_type": "m.thread",
    "event_id": thread_root_event_id,
    "m.in_reply_to": {"event_id": reply_to_event_id or thread_root_event_id},
    "is_falling_back": reply_to_event_id is None,
}
```

`FakeMatrixClient.send_text` gains the same parameter; the spy tuple becomes
`(room_id, body, reply_to_event_id, thread_root_event_id)`.

### Command palette entry — `tui/commands.py`

```python
("Open thread", self.cmd_open_thread, "View the thread for the focused message (if any)"),
```

`cmd_open_thread`:
- Gets the active room's `MessageView`.
- Gets the currently-focused `MessageRow` via `view.query_one(MessageRow)` (or
  `view.focused_message`).
- If `row.message.thread_root_id` is not None, calls
  `screen.open_thread(row.message.room_id, row.message.thread_root_id)`.
- Otherwise notifies the user: "Focused message is not part of a thread."

### CSS — `tui/styles/app.tcss`

```css
/* Thread panel — plan 0023 */
#thread-panel {
    width: 40;
    border: solid $accent;
    border-title-color: $text;
    display: none;
}

#thread-header {
    height: 1;
    background: $surface;
}

#thread-title {
    width: 1fr;
    text-style: bold;
}

#thread-close {
    width: 3;
    min-width: 3;
}

#thread-messages {
    height: 1fr;
}

#thread-composer {
    height: 3;
    border-top: solid $primary;
}
```

The `display: none` default means the CSS rule `watch_thread_visible` will
set `display = True/False` on the widget, matching the `#log-panel` pattern.

---

## Tier-1 tests — `tests/matrix/test_thread.py` (new file)

All Tier-1 tests use `restore_client()` from `matrix.helpers` and
`aioresponses` via `stub_get`.  No Textual involved.

### Fixtures needed — `tests/fixtures/nio/synthetic/`

**`thread_relations_page1.json`** — a `RoomEventRelationsResponse` shape:

```json
{
  "chunk": [
    {
      "type": "m.room.message",
      "event_id": "$reply1:example.com",
      "sender": "@bob:example.com",
      "origin_server_ts": 1700000002000,
      "content": {
        "msgtype": "m.text",
        "body": "First reply",
        "m.relates_to": {
          "rel_type": "m.thread",
          "event_id": "$root:example.com"
        }
      }
    },
    {
      "type": "m.room.message",
      "event_id": "$reply2:example.com",
      "sender": "@carol:example.com",
      "origin_server_ts": 1700000004000,
      "content": {
        "msgtype": "m.text",
        "body": "Second reply",
        "m.relates_to": {
          "rel_type": "m.thread",
          "event_id": "$root:example.com"
        }
      }
    }
  ],
  "next_batch": null
}
```

**`sync_with_thread_reply.json`** — a sync response whose timeline includes a
`m.room.message` with `rel_type: m.thread` and `event_id: "$root:example.com"`.

---

### Test cases

**`test_get_thread_messages_returns_chronological_messages`**
- `restore_client()` → `client` with room `!r:s`.
- `stub_get(m, "/_matrix/client/v1/rooms/!r%3As/relations/%24root%3Aexample.com/m.thread", load_fixture("thread_relations_page1.json"))`.
- `messages, has_more = await client.get_thread_messages("!r:s", "$root:example.com")`.
- Assert `len(messages) == 2`, `messages[0].event_id == "$reply1:example.com"`,
  `messages[1].event_id == "$reply2:example.com"` (chronological: oldest first).
- Assert `messages[0].body == "First reply"`.
- Assert `messages[0].thread_root_id == "$root:example.com"`.
- Assert `has_more is False`.

**`test_get_thread_messages_sets_thread_root_id`**
- Same fixture; assert every returned `Message` has
  `thread_root_id == "$root:example.com"`.

**`test_get_thread_messages_server_error_returns_empty`**
- Stub the endpoint to return HTTP 404.
- Assert `(messages, has_more) == ([], False)` (graceful degradation).
- Assert no exception raised.

**`test_get_thread_messages_not_logged_in_raises`**
- Do not call `restore_client`; build a raw `MatrixClient` without logging in.
- Assert `await client.get_thread_messages(...)` raises `NotLoggedInError`.

**`test_backfill_thread_reply_sets_thread_root_id`**
- In `messages()` backfill, a `RoomMessageText` event whose `source` has
  `m.relates_to.rel_type == "m.thread"` and `event_id == "$root:example.com"`.
- Stub `room_messages` to return a fixture containing such an event.
- `msgs = await client.messages("!r:s")`.
- Assert the returned `Message` has `thread_root_id == "$root:example.com"`.

**`test_live_sync_thread_reply_sets_thread_root_id`**
- Use `aioresponses` + `stub_sync(m, load_fixture("sync_with_thread_reply.json"))`.
- Subscribe and collect `NewMessage` events.
- Assert the emitted `NewMessage.message.thread_root_id == "$root:example.com"`.

**`test_get_thread_messages_has_more_when_limit_exceeded`**
- Stub the endpoint to return `limit+1` events (server returns more than
  requested — simulated by adding `"next_batch": "t123"` to the fixture).
- `messages, has_more = await client.get_thread_messages("!r:s", "$root", limit=2)`.
- Assert `has_more is True`.

---

## Tier-2 tests — `tests/tui/test_thread_panel.py` (new file)

All Tier-2 tests use `FakeMatrixClient` with thread data scripted via a new
`thread_messages` dict.

### `FakeMatrixClient` additions

```python
# thread_messages: (room_id, root_event_id) -> (list[Message], has_more)
self.thread_messages: dict[tuple[str, str], tuple[list[Message], bool]] = {}

async def get_thread_messages(
    self, room_id: str, root_event_id: str, limit: int = 50
) -> tuple[list[Message], bool]:
    self._check_fail("get_thread_messages")
    await self._maybe_block("get_thread_messages")
    return self.thread_messages.get((room_id, root_event_id), ([], False))
```

`reset_spies()` does NOT clear `thread_messages` (scripted state).

If OPEN QUESTION 3 is confirmed, `send_text` in `FakeMatrixClient` gains
`thread_root_event_id: str | None = None`; the spy tuple becomes a 4-tuple.

### `ThreadHostApp`

```python
class ThreadHostApp(App[None]):
    def __init__(self, client: FakeMatrixClient) -> None:
        super().__init__()
        self._client = client

    def compose(self) -> ComposeResult:
        yield ThreadPanel(self._client, id="thread-panel")

    def on_mount(self) -> None:
        pass  # tests call panel.load_thread() directly
```

### Test cases

**`test_thread_panel_shows_messages_after_load`**
- `fake.thread_messages = {("!r:s", "$root"): ([msg1, msg2], False)}`.
- Mount `ThreadHostApp`.
- `await panel.load_thread("!r:s", "$root")`.
- `await pilot.pause()`.
- Assert `#thread-messages` contains two `MessageRow` widgets.
- Assert the first row renders `msg1.body`.

**`test_thread_panel_empty_thread_shows_no_rows`**
- `fake.thread_messages = {("!r:s", "$root"): ([], False)}`.
- After load, assert `len(panel.query(MessageRow)) == 0`.

**`test_thread_panel_close_posts_close_requested`**
- Mount; load a thread; `pilot.press("escape")`.
- `await pilot.pause()`.
- Assert `ThreadPanel.CloseRequested` was posted (use a message watcher or
  inspect that the panel's `_close_requested` was called — pattern from
  `LogPanel.CloseRequested`).

**`test_thread_panel_close_button_posts_close_requested`**
- Mount; load; `await pilot.click("#thread-close")`.
- Assert `CloseRequested` posted.

**`test_thread_panel_append_message_adds_row`**
- Load a thread with 1 message; `panel.append_message(new_msg)`.
- `await pilot.pause()`.
- Assert two `MessageRow` widgets present; second row body matches `new_msg.body`.

**`test_thread_panel_deduplicates_appended_messages`**
- Load a thread with `[msg1]`; call `panel.append_message(msg1)` (same event_id).
- Assert still only 1 `MessageRow`.

**`test_thread_panel_has_more_notice_shown`**
- `fake.thread_messages = {("!r:s", "$root"): ([msg1], True)}`.
- After load, assert some widget contains text indicating more messages exist
  (e.g. a Static with `"Load more"`).

**`test_thread_panel_has_more_notice_hidden_when_complete`**
- `has_more=False` → no "Load more" widget visible.

**`test_main_screen_open_thread_shows_panel`**
- Full `MainScreen` via `run_test` with `FakeMatrixClient`.
- Set `fake.thread_messages = {("!r:s", "$root"): ([msg1], False)}`.
- Call `screen.open_thread("!r:s", "$root")`.
- `await pilot.pause()`.
- Assert `#thread-panel` has `display == True`.
- Assert `#thread-panel` contains `MessageRow`.

**`test_main_screen_close_thread_hides_panel`**
- Open thread; then `screen.close_thread()`.
- Assert `#thread-panel` has `display == False`.

**`test_context_menu_view_thread_appears_for_thread_reply`**
- A `MessageRow` with `message.thread_root_id = "$root"`.
- Right-click on the row → context menu.
- Assert menu contains "View thread" item.

**`test_context_menu_view_thread_absent_for_plain_message`**
- A `MessageRow` with `message.thread_root_id = None`.
- Right-click → assert "View thread" is NOT in menu.

**`test_command_palette_open_thread`**
- Mount with a `MessageRow` focused whose message has `thread_root_id`.
- Open palette (`ctrl+p`), type `"Open thread"`, press `Enter`.
- Assert `#thread-panel` becomes visible.

**`test_live_new_message_appends_to_open_thread`**
- Open thread for root `"$root"`.
- `await fake.emit(NewMessage(message=thread_reply))` where `thread_reply.thread_root_id == "$root"`.
- `await pilot.pause()`.
- Assert the new row appears in `#thread-messages`.

**`test_live_new_message_in_other_thread_ignored`**
- Open thread for root `"$root"`.
- Emit `NewMessage` with `thread_root_id == "$other_root"`.
- Assert panel row count unchanged.

---

## Implementation steps

1. **Stub additions** — add `RelationshipType`, `RoomEventRelationsResponse`,
   `RoomEventRelationsError` to `stubs/nio/__init__.pyi`.  Add `AsyncIterator`
   import.  Add `room_get_event_relations` to `AsyncClient` stub.

2. **`Message.thread_root_id`** — add `thread_root_id: str | None = None`
   to `matrix/models.py`.

3. **`MatrixClient.get_thread_messages`** — implement in `matrix/client.py`.
   Parse `RoomMessageText` / `RoomMessageMedia` / `MegolmEvent` events from
   the iterator.  Return `(messages_chronological, has_more)`.

4. **Parse `thread_root_id` in `_on_room_message` and `messages()` backfill** —
   extend both paths to extract `rel_type: m.thread` → `thread_root_id`.

5. **Tier-1 tests** — write `tests/matrix/test_thread.py` and create fixtures.
   All should fail at this point.

6. **Run Tier-1** — iterate until green.

7. **`FakeMatrixClient` additions** — add `thread_messages` dict and
   `get_thread_messages` method to `tests/fakes.py`.  If OPEN QUESTION 3
   confirmed, extend `send_text` spy tuple.

8. **`ThreadPanel` widget** — create `src/telemente/tui/widgets/thread_panel.py`
   with `_ThreadPanelClient` protocol, `ThreadPanel` class,
   `load_thread`, `_do_load`, `append_message`, `close` methods, `CloseRequested`
   and `ThreadReply` messages.

9. **`MainScreen` layout changes** — add `ThreadPanel` to `compose()`, add
   `thread_visible` reactive and watcher, add `open_thread`, `close_thread`
   methods, add `on_thread_panel_close_requested` handler, add
   `on_message_view_open_thread` handler, extend `handle_new_message` to
   forward to the panel.

10. **`MessageView` context menu** — add `MessageView.OpenThread` Textual
    message, add "View thread" item to `_build_context_menu_items` when
    `message.thread_root_id is not None`.

11. **CSS** — add `#thread-panel` and child rules to `tui/styles/app.tcss`.

12. **Command palette** — add `"Open thread"` entry and `cmd_open_thread`
    to `tui/commands.py`.

13. **Tier-2 tests** — write `tests/tui/test_thread_panel.py`.  All should
    fail at this point.

14. **Run Tier-2** — iterate until green.

15. **Full feedback loop** — `uv run ruff check .`, `uv run ruff format .`,
    `uv run mypy`, `pyright src/`, `uv run pytest`.

---

## Files to create / modify

| File | Action | Changes |
|---|---|---|
| `stubs/nio/__init__.pyi` | Modify | Add `RelationshipType` enum; add `RoomEventRelationsResponse`, `RoomEventRelationsError`; add `room_get_event_relations` to `AsyncClient`; add `AsyncIterator` import |
| `src/telemente/matrix/models.py` | Modify | Add `thread_root_id: str \| None = None` to `Message` |
| `src/telemente/matrix/client.py` | Modify | Add `get_thread_messages()` method; parse `thread_root_id` in `_on_room_message` and `messages()` backfill; optionally extend `send_text` for thread-aware replies |
| `src/telemente/tui/widgets/thread_panel.py` | Create | `_ThreadPanelClient` protocol; `ThreadPanel` widget with `load_thread`, `_do_load`, `append_message`, `close`; `CloseRequested` and `ThreadReply` messages |
| `src/telemente/tui/screens/main.py` | Modify | Add `ThreadPanel` to `compose()`; add `thread_visible` reactive; add `open_thread`, `close_thread`, `on_thread_panel_close_requested`, `on_message_view_open_thread`; extend `handle_new_message` |
| `src/telemente/tui/widgets/message_view.py` | Modify | Add `MessageView.OpenThread` message; add "View thread" to context menu items; extend `_MessageViewClient` if `send_text` gains `thread_root_event_id` |
| `src/telemente/tui/commands.py` | Modify | Add `"Open thread"` entry; add `cmd_open_thread` |
| `src/telemente/tui/styles/app.tcss` | Modify | Add `#thread-panel`, `#thread-header`, `#thread-title`, `#thread-close`, `#thread-messages`, `#thread-composer` CSS rules |
| `tests/fakes.py` | Modify | Add `thread_messages` dict; add `get_thread_messages` method; optionally extend `send_text` spy tuple |
| `tests/matrix/test_thread.py` | Create | Seven Tier-1 tests for `get_thread_messages` and `thread_root_id` parsing |
| `tests/fixtures/nio/synthetic/thread_relations_page1.json` | Create | Synthetic `RoomEventRelationsResponse` fixture with two thread reply events |
| `tests/fixtures/nio/synthetic/sync_with_thread_reply.json` | Create | Sync fixture containing a timeline event with `rel_type: m.thread` |
| `tests/tui/test_thread_panel.py` | Create | Fourteen Tier-2 tests for `ThreadPanel`, `MainScreen` integration, context menu, command palette, and live updates |

---

## Done-when checklist

- [ ] `stubs/nio/__init__.pyi` defines `RelationshipType` with `.thread` member;
  defines `RoomEventRelationsResponse` with `.events: list[Event]`,
  `.next_batch: str | None`; `AsyncClient` stub has `room_get_event_relations`.
- [ ] `Message.thread_root_id: str | None = None` compiles under `mypy --strict`
  and `pyright src/` with no `Any` leaks; all existing `Message(...)` construction
  sites are unchanged.
- [ ] `MatrixClient.get_thread_messages(room_id, root_event_id)` returns a
  `(list[Message], bool)` tuple.
- [ ] Thread messages from `get_thread_messages` are in chronological order
  (oldest first).
- [ ] `get_thread_messages` sets `thread_root_id` on every returned `Message`.
- [ ] `get_thread_messages` returns `([], False)` gracefully on HTTP error.
- [ ] `get_thread_messages` raises `NotLoggedInError` when not logged in.
- [ ] `_on_room_message` callback sets `thread_root_id` when `rel_type == "m.thread"`.
- [ ] `messages()` backfill sets `thread_root_id` for thread reply events.
- [ ] All seven Tier-1 tests in `tests/matrix/test_thread.py` are green.
- [ ] `FakeMatrixClient.get_thread_messages` reads from `thread_messages` dict.
- [ ] `ThreadPanel` widget exists; `load_thread` populates `#thread-messages` with
  `MessageRow` widgets.
- [ ] `ThreadPanel.CloseRequested` is posted on Escape and on `#thread-close` click.
- [ ] `ThreadPanel.append_message` adds a row and deduplicates by event_id.
- [ ] `#thread-panel` has `display: none` by default.
- [ ] `MainScreen.open_thread` sets `thread_visible = True` and calls
  `panel.load_thread`.
- [ ] `MainScreen.close_thread` sets `thread_visible = False`.
- [ ] `on_thread_panel_close_requested` calls `close_thread`.
- [ ] `handle_new_message` forwards matching thread messages to `ThreadPanel.append_message`.
- [ ] Context menu shows "View thread" when `message.thread_root_id is not None`.
- [ ] Context menu does NOT show "View thread" for plain messages.
- [ ] `"Open thread"` appears in the command palette.
- [ ] `cmd_open_thread` works for the focused message row.
- [ ] All fourteen Tier-2 tests in `tests/tui/test_thread_panel.py` are green.
- [ ] `uv run ruff check .` clean.
- [ ] `uv run ruff format .` clean.
- [ ] `uv run mypy` clean.
- [ ] `pyright src/` clean.
- [ ] `uv run pytest` all green.
