# `src/telemente/tui/widgets/` — reusable UI components

All Textual `Widget` subclasses used inside `MainScreen` live here. Each widget
is responsible for a single panel or interaction surface. Widgets never import
`nio` and never read from `TelementeApp` directly — the client is injected by
the parent screen.

## Purpose

Implement the individual panels of the three-panel layout (room list, message
view, member list), plus auxiliary surfaces: thread panel, log viewer, floating
context menu, and confirmation modal. Each widget exposes a narrow public API
and communicates upward via `post_message`.

## Key design decisions

**Each widget defines its own `_*Client` protocol.** `MessageView`,
`MemberList`, and `ThreadPanel` each declare a structural protocol with only
the `MatrixClient` methods they call. This means a future refactor can change
what a widget needs without touching the full `MatrixClient` signature, and
fakes can be narrow.

**`RoomList` uses `OptionList` (not `ListView`).** Plan 0012 migrated from
`ListView` to `OptionList` because `OptionList.replace_option_prompt` enables
surgical single-option updates without clearing and rebuilding the list. The
`update_unread` method exploits this: it patches only the changed option in
place, which is critical for performance on accounts with many rooms.

**Search input is debounced 150 ms.** `RoomList.on_input_changed` cancels the
previous `set_timer` on each keystroke and creates a new one. The actual
`apply_filter` call happens only after 150 ms of silence. This prevents a full
`OptionList` rebuild on every keypress.

**`MessageRow` is a focusable widget with keybindings.** Each rendered message
is a `Widget(can_focus=True)` with bindings for `e` (react), `r` (reply),
`E` (edit), `d` (delete). This lets keyboard-driven users operate on messages
without a mouse. The bindings post inner `Message` subclasses (`ReactRequest`,
`ReplyRequest`, etc.) that bubble to `MessageView`.

**Reactions are applied optimistically.** When the user sends a reaction,
`MessageRow.update_reaction` updates the chips display immediately before the
network call completes. This makes the UI feel instant. The server will echo
the reaction back in the next sync; the deduplication in `append_message`
prevents double-rendering.

**Edit and send echo locally.** `_do_send` constructs a local `Message` with a
synthetic event_id and calls `append_message` after `send_text` returns. The
server's echo during the next sync is discarded by the `_rendered_event_ids`
set. Same pattern for edits: `_do_edit` calls `update_body` on the row
optimistically after the edit RPC succeeds.

**`EmojiPickerScreen` is opened via `call_next`.** `open_emoji_picker_for` uses
`self.call_next(self.app.push_screen, ...)` rather than a direct `push_screen`.
This defers the call to after the current message-pump turn, ensuring the
callback is registered with `MessageView` as the active pump, not a
`ContextMenu` that is being dismissed in the same turn.

**`ContextMenu` mounts directly into the screen at absolute coordinates.**
`on_mount` sets `self.absolute_offset` to the clamped screen position. It lives
on the screen's `"context-menu"` layer so it floats above all panels. Only one
menu exists at a time; `MainScreen._dismiss_context_menu` removes the old one
before mounting a new one.

**`LogPanel` defers tail start until `on_show`.** The tail worker only launches
the first time the panel becomes visible, avoiding a file handle sitting open
while the panel is hidden.

**`ThreadPanel` deduplicates by `_event_ids_rendered`.** Live messages from
`MainScreen.handle_new_message` are forwarded via `append_message` only if the
panel's `root_event_id` and `room_id` match. The set prevents the same event
appearing twice if it arrives in both the backfill and a live sync.

## File map

| File | Role |
|------|------|
| `room_list.py` | `RoomList` — searchable/filterable `OptionList` of rooms with unread badges and tag decorators |
| `message_view.py` | `MessageView` — scrollable message timeline, `MessageRow`, `ComposerArea`; handles send/reply/react/edit/redact/search |
| `member_list.py` | `MemberList` — right-panel member list sorted by power level then name |
| `thread_panel.py` | `ThreadPanel` — collapsible side panel for a single Matrix thread |
| `log_panel.py` | `LogPanel` — bottom panel tailing the telemente log file via `RichLog` |
| `context_menu.py` | `ContextMenu`, `MenuItem`, `MenuSeparator` — floating single-column menu |
| `confirm_screen.py` | `ConfirmScreen` — minimal Yes/No `ModalScreen[bool]` |
| `__init__.py` | Package docstring only |

## Reactive attributes

| Widget | Attribute | Effect |
|--------|-----------|--------|
| `MainScreen` | `rooms_visible` | Toggles `#rooms-panel` display |
| `MainScreen` | `members_visible` | Toggles `#members-panel` display |
| `MainScreen` | `log_visible` | Toggles `#log-panel` display |
| `MainScreen` | `thread_visible` | Toggles `#thread-panel` display |

`RoomList`, `MessageView`, `MemberList`, and `ThreadPanel` do not use
`reactive()` — their mutable state is managed manually via `_rebuild`,
`_refresh`, etc., because full re-render on every attribute write would be
too expensive.

## What lives elsewhere

- Sorting logic (`sort_rooms_by_recency`) → `matrix/sort.py`
- Sender colour assignment → `tui/colors.py`
- The `ClientEvent` types that feed widget updates → `matrix/client.py`
- `EmojiPickerScreen` (opened by `MessageView`) → `tui/screens/emoji_picker.py`
- Application-level routing of `ClientEvent` to widgets → `tui/app.py` and
  `tui/screens/main.py`
