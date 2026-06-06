# Plan 0032a — Virtualize message timeline

## Goal

Replace the current `VerticalScroll` + eagerly-mounted `MessageRow(Widget)`
architecture with a custom `MessageTimeline(ScrollView)` that uses Textual's
Line API. The new widget renders only the rows in the viewport; the full message
list lives in a plain Python list. No new user-visible features — same visual
output, same keyboard navigation, same context menu, just a rendering
architecture change that makes plan 0032b (pagination) cheap at any message
count.

---

## Dependencies

- Plans 0001–0031 complete.
- No new runtime dependencies.

---

## Architecture

### Why a custom `ScrollView`, not `OptionList`

`OptionList` has no `insert_at_index` or prepend operation — every paginated
load would require `clear_options()` + rebuild, causing a full-screen flash.
Messages are append-only (live), prepend-able (pagination), mutated in-place
(edits/reactions), and individually removable (redactions). `OptionList`'s
immutable-chunk model cannot express those four operations without a rebuild.
A bespoke `ScrollView` subclass gives full control over all four with roughly
200 lines.

### `MessageTimeline(ScrollView)` — data model

```python
@dataclass(frozen=True, slots=True)
class _RenderedLine:
    """Maps a global line index to a (message_index, local_line_offset) pair."""
    msg_index: int
    local_offset: int  # 0 = header/body line 0, 1 = body line 1, etc.
```

Internal state:
- `_messages: list[Message]` — chronological order, oldest first.
- `_heights: list[int]` — pre-computed line count per message (1+ lines).
- `_line_offsets: list[int]` — `_line_offsets[i]` = first global line of
  message `i`. Rebuilt in O(n) after any structural change.
- `_highlighted: int | None` — index of the keyboard-focused message.
- `_total_lines: int` — sum of all heights; drives `virtual_size`.

### Height calculation

A message occupies N lines:

```
reply_quote_line  : 1 if msg.reply_to_event_id else 0
header_body_lines : max(1, count of '\n' in formatted header+body) + 1
                    (header is always one line; body wraps at widget width)
reaction_line     : 1 if msg.reactions else 0
media_line        : 1 if msg.media_url else 0
```

`_compute_height(msg, width)` produces this integer. Width is `self.size.width`
(available after first layout). When the terminal is resized, all heights are
recomputed and `_line_offsets` is rebuilt.

### `render_line(y)` — Line API entry point

```python
def render_line(self, y: int) -> Strip:
    """Called by Textual for each visible row during repaint."""
    global_line = y + int(self.scroll_y)
    if global_line >= self._total_lines or not self._messages:
        return Strip.blank(self.size.width)
    msg_index = bisect.bisect_right(self._line_offsets, global_line) - 1
    local_offset = global_line - self._line_offsets[msg_index]
    return self._render_message_line(msg_index, local_offset)
```

`_render_message_line(msg_index, local_offset)` builds the Rich `Strip` for
that specific line of that message using Rich `Text` and `Segment` objects
(same formatting logic as the current `MessageRow.compose`).

`virtual_size` is overridden to return `Size(self.size.width, self._total_lines)`.

### Public API

```python
class MessageTimeline(ScrollView):
    def append(self, msg: Message) -> None:
        """Append a new message (live or local echo). O(1)."""

    def prepend_batch(self, msgs: list[Message]) -> None:
        """Prepend older messages at the top. Restores scroll position
        so the user's viewport does not jump. O(len(msgs) + n)."""

    def update_message(self, event_id: str, msg: Message) -> None:
        """Replace a message in-place (edit, reaction, name patch). O(log n)."""

    def remove_message(self, event_id: str) -> None:
        """Remove a message by event_id (redaction). O(n)."""

    def clear(self) -> None:
        """Remove all messages. O(1)."""

    def scroll_to_bottom(self) -> None:
        """Scroll to the last message without animation."""

    def scroll_to_event(self, event_id: str) -> None:
        """Scroll the highlighted message into view. Used by search."""

    @property
    def newest_event_id(self) -> str | None:
        """event_id of the most recently appended message, or None."""

    @property
    def message_count(self) -> int:
        """Number of messages currently in the timeline."""

    def highlighted_message(self) -> Message | None:
        """The currently keyboard-focused message, or None."""
```

### Keyboard navigation

`MessageTimeline` handles `Up`/`Down`/`Home`/`End` to move `_highlighted`.
`Enter` and the action keys (`e`, `r`, `E`, `d`) act on `highlighted_message()`.
The highlighted message is rendered with a `$accent` background on its first
line.

### Context menu via mouse click

`on_mouse_down(event)`: resolve `event.y + int(self.scroll_y)` to a message
index using `bisect.bisect_right(_line_offsets, ...)`. For right-click (`button
== 3`), post `MessageTimeline.ContextMenuRequest(message, screen_x, screen_y)`.

### Rich hyperlinks

`_linkify` produces `[link=url]text[/link]` markup (already in current
`message_view.py`). In `render_line`, pass the resulting `Rich.Text` through
`Text.render(console)` to obtain `Segment` objects that carry OSC 8 metadata.

### Messages (Textual)

```python
class MessageTimeline(ScrollView):
    class ReactRequest(TextualMessage): ...
    class ReplyRequest(TextualMessage): ...
    class EditRequest(TextualMessage): ...
    class DeleteRequest(TextualMessage): ...
    class ContextMenuRequest(TextualMessage): ...
```

Same payload types as the current `MessageRow.*` messages. `MessageView`
handles them identically — only the source widget changes.

### Integration into `MessageView`

`MessageView` replaces the `VerticalScroll` container with `MessageTimeline`:

```python
def compose(self) -> ComposeResult:
    yield MessageTimeline(id="message-timeline")
    ...
```

All call sites that currently do `timeline.mount(MessageRow(...))` or
`self.query(MessageRow)` are updated to use `MessageTimeline`'s public API.

### Date separators

Date separators are rendered as synthetic "messages" at the correct height
position. A private `_SeparatorEntry` sentinel (not a `Message`) is interleaved
into `_messages` during `_rebuild_line_offsets`. `render_line` detects
`_SeparatorEntry` and draws the date text.

Alternatively — simpler — date separators are computed on the fly inside
`_render_message_line` by comparing `msg.timestamp.date()` with the previous
message's date. No sentinel needed; the line count for the first message of
each date is incremented by 1 for the separator line.

The plan uses the on-the-fly approach (no sentinels, no parallel list).

---

## Files changed

- `src/telemente/tui/widgets/message_view.py` — introduce `MessageTimeline`;
  update `MessageView` to use it; remove `MessageRow`'s DOM-mounting logic
  (keep the message/action classes as Textual messages or migrate inline).
- `src/telemente/tui/styles/app.tcss` — adjust selectors that target
  `MessageRow` to target `MessageTimeline` or its rendered content.
- `tests/tui/` — update tests that use `app.query(MessageRow)` to use the
  new `MessageTimeline` inspection API.

---

## Implementation steps

1. Add `MessageTimeline(ScrollView)` to `message_view.py` with empty `render_line`, `append`, `prepend_batch`, `update_message`, `remove_message`, `clear`, `scroll_to_bottom`, `scroll_to_event`.
2. Implement `_compute_height`, `_rebuild_line_offsets`, `virtual_size`.
3. Implement `render_line` → `_render_message_line` with Rich `Text`/`Segment`.
4. Implement keyboard navigation (`Up`/`Down`/`Home`/`End`, action keys).
5. Implement mouse click → context menu resolution.
6. Implement `prepend_batch` with scroll-position restoration.
7. Swap `VerticalScroll` + `MessageRow` out of `MessageView`; wire all call sites.
8. Update CSS selectors.
9. Update TUI tests.
10. Write tests before steps 1–9.

---

## Tests

### `tests/tui/test_message_timeline.py` — unit tests for `MessageTimeline`

```python
async def test_append_increases_message_count() -> None:
    """append() adds one message and message_count increases by 1."""

async def test_append_updates_newest_event_id() -> None:
    """After append(), newest_event_id equals the appended message's event_id."""

async def test_prepend_batch_places_messages_above_existing() -> None:
    """prepend_batch() inserts messages so that older messages appear before
    existing ones; message ordering is chronological (oldest first)."""

async def test_prepend_batch_restores_scroll_position() -> None:
    """After prepend_batch(), the scroll_y is adjusted so that the message
    that was at the top of the viewport before the prepend is still at the
    top after it."""

async def test_update_message_replaces_body() -> None:
    """update_message() with a new body causes the next render_line call for
    that message to return a Strip containing the new body text."""

async def test_update_message_unknown_event_id_is_noop() -> None:
    """update_message() with an unknown event_id does not raise and does not
    change message_count."""

async def test_remove_message_decreases_message_count() -> None:
    """remove_message() decreases message_count by 1."""

async def test_remove_message_unknown_event_id_is_noop() -> None:
    """remove_message() with an unknown event_id does not raise."""

async def test_clear_resets_state() -> None:
    """clear() sets message_count to 0 and newest_event_id to None."""

async def test_render_line_returns_blank_strip_when_empty() -> None:
    """render_line(y) returns a blank Strip when no messages are loaded."""

async def test_render_line_returns_strip_for_valid_line() -> None:
    """render_line(0) returns a non-blank Strip after one message is appended."""

async def test_render_line_includes_sender_display_name() -> None:
    """The Strip for line 0 of a message contains the sender's display name."""

async def test_render_line_includes_body_text() -> None:
    """The Strip for the body line of a message contains the message body."""

async def test_render_line_date_separator_for_new_day() -> None:
    """When two messages are on different dates, a date separator line appears
    between them in the rendered output."""

async def test_render_line_reply_quote_shown() -> None:
    """A message with reply_to_event_id renders a reply-quote line before the
    header line, incrementing the message height by 1."""

async def test_render_line_reaction_chips_shown() -> None:
    """A message with reactions renders a reaction-chips line at the bottom."""

async def test_height_recalculated_on_resize() -> None:
    """After a terminal resize, _compute_height is recalculated so that long
    body lines wrap at the new width."""

async def test_keyboard_up_down_moves_highlight() -> None:
    """Pressing Down moves the highlighted index from None to 0, then to 1.
    Pressing Up moves it back to 0."""

async def test_keyboard_highlight_wraps_at_bounds() -> None:
    """Pressing Up at the top does not go below 0; pressing Down at the bottom
    does not exceed the last index."""

async def test_context_menu_request_on_right_click() -> None:
    """A right-click (button=3) at a y-coordinate that maps to a message posts
    MessageTimeline.ContextMenuRequest with the correct message."""

async def test_react_request_key() -> None:
    """Pressing 'e' when a message is highlighted posts ReactRequest."""

async def test_reply_request_key() -> None:
    """Pressing 'r' when a message is highlighted posts ReplyRequest."""

async def test_edit_request_key() -> None:
    """Pressing 'E' when a message is highlighted posts EditRequest."""

async def test_delete_request_key() -> None:
    """Pressing 'd' when a message is highlighted posts DeleteRequest."""
```

### Adapted existing TUI tests

All existing tests under `tests/tui/` that assert on `app.query(MessageRow)`
or `app.query_one(MessageRow)` must be updated to use one of:

- `timeline.message_count` — for count assertions.
- `timeline.newest_event_id` — for "last message" assertions.
- `timeline.highlighted_message()` — for focus assertions.
- `timeline._messages[i].body` — for content assertions (direct list access
  is acceptable in tests; no need for a dedicated getter).

The public surface of `MessageView` is unchanged. Tests that drive
`MessageView.load_room`, `MessageView.append_message`, `MessageView.clear`,
`MessageView.remove_message`, `MessageView.patch_sender_names` continue to
work without modification; only the DOM-inspection calls change.

---

## Done-when checklist

- [ ] `MessageTimeline(ScrollView)` exists in `message_view.py`.
- [ ] `MessageTimeline` passes all unit tests in `test_message_timeline.py`.
- [ ] `MessageView` uses `MessageTimeline` instead of `VerticalScroll` + `MessageRow.mount`.
- [ ] `MessageRow` widget class is removed (or kept only for its message subclasses, migrated inline to `MessageTimeline`).
- [ ] `_compute_height` and `_rebuild_line_offsets` are correct for single-line and multi-line bodies, reply quotes, reactions, and media.
- [ ] Date separators render between messages from different days.
- [ ] Keyboard navigation (Up/Down/Home/End, action keys) works.
- [ ] Right-click context menu resolves to the correct message.
- [ ] Rich OSC 8 hyperlinks render for bare URLs.
- [ ] All existing TUI tests that were adapted still pass.
- [ ] `uv run ruff check .` / `uv run ruff format .` clean.
- [ ] `uv run mypy` clean.
- [ ] `pyright src/` clean.
- [ ] `uv run pytest` all green.
