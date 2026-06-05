# Plan 0021 — Context menu and emoji picker bug fixes

## Goal

Fix seven post-landing regressions in plan 0020: context menu item actions not
firing, missing mute indicator, emoji picker reaction not sending, emoji picker
flicker on hover, delete with no confirmation, tab right-click doing nothing,
and context menu for room list items being too wide and overflowing the screen.

## Dependencies

Plan 0020 must be merged before implementing any of these fixes.

---

## Bug 1 — Context menu item clicks don't fire actions

### Root cause

`src/telemente/tui/widgets/context_menu.py`, lines 153–176.

`ContextMenu.on_click` fires when the user clicks a menu item `Static`. It
correctly calls `event.stop()` to prevent the click from bubbling to
`MainScreen.on_click` (which would dismiss the menu before the action runs).
However, `_dismiss()` is called **before** `entry.action()` on lines 146–147
and 172–173:

```python
self._dismiss()
entry.action()
```

`_dismiss()` calls `self.post_message(self.Dismissed())` then `self.remove()`.
`widget.remove()` in Textual returns an `AwaitRemove` object (not awaited
here); the DOM removal is scheduled but deferred. The `entry.action()` call
then runs synchronously. Because the ContextMenu is still in the DOM (removal
hasn't happened yet) and the closures all capture `MessageView` (not
`ContextMenu`), this works in unit tests driven by `pilot.click(static)`.

The real failure mode is a **missing test that exercises the full path**: right-
click MessageRow → ContextMenu appears → left-click item → action fires. The
existing tests bypass the right-click `on_mouse_down` dispatch and post
`ContextMenuRequest` directly, so they never exercise `ContextMenu.on_click`
in the context of an action callback that calls `self.post_message(...)` or
`self.app.push_screen(...)`. In particular, `test_reply_item_posts_reply_request`
and `test_react_item_opens_emoji_picker` do call `pilot.click(item)` on the
static, but the action closures are built by `on_message_row_context_menu_request`
using `self = MessageView`. When the ContextMenu is mounted as a child of
`MainScreen` (not `MessageView`), the bubbled `MessageRow.ReplyRequest` message
posted from inside `_reply()` must bubble from `MessageView` upward through
`MainScreen` — not from inside `ContextMenu`. The current tests assert only
that the reply indicator is visible, but they don't go through `MainScreen` at
all in the `_simulate_right_click` + `pilot.click` path.

The concrete ordering hazard: `_dismiss()` fires `post_message(Dismissed())`
which — because message posting is synchronous — runs `MainScreen.on_context_menu_dismissed`
BEFORE `entry.action()` executes when the message pump drains. If
`_active_context_menu` is cleared before the action fires there is no loss of
data, but if in a future refactor the dismiss handler does more cleanup the
ordering is fragile.

### Proposed fix

Swap the call order in `_activate_focused` and `on_click`: call
`entry.action()` first, then `self._dismiss()`. This ensures the action fires
while the menu is still nominally alive, and the dismiss happens after.

```python
# context_menu.py  _activate_focused
entry.action()   # fire first
self._dismiss()  # then remove

# context_menu.py  on_click
entry.action()
self._dismiss()
```

Add an end-to-end test that goes through the full mouse-down → ContextMenu
mount → click-item path using `MainScreen` as the host.

### Test cases needed

**`tests/tui/test_context_menu.py`**

`test_click_item_fires_action` — Mount `ContextMenu` in `MenuHostApp`, call
`pilot.click` on the first `Static`, assert the callback was called and the
menu was removed.

**`tests/tui/test_message_context_menu.py`**

`test_reply_via_context_menu_end_to_end` — Use `HostApp` with `MainScreen`,
open a room, right-click a `MessageRow` by posting `ContextMenuRequest`
(existing shortcut is fine), then call `pilot.click` on the "Reply" Static and
assert the reply indicator is shown. (This test already exists and passes; the
new test should also assert no Python exception was raised, i.e., the worker
completes successfully.)

---

## Bug 2 — Muting a room shows no bell icon

### Root cause

`src/telemente/matrix/client.py`, `set_room_tag` (line 620) and
`remove_room_tag` (line 653).

Both methods make a raw HTTP PUT/DELETE to the homeserver but do NOT:
1. Update the in-memory nio `MatrixRoom.tags` dict.
2. Emit a `RoomsChanged` event after a successful call.

Contrast with `leave_room` (line 608–618) which explicitly calls
`await self._emit(RoomsChanged(rooms=self.rooms()))` after removing the room
locally. `set_room_tag` / `remove_room_tag` have no equivalent call. The UI
therefore never receives a `RoomsChanged` event with the new tag, so
`RoomItem._render_name()` never sees `"m.mute"` in `room.tags` and never
appends `🔕`. The indicator only appears after the next sync cycle from nio
re-populates `room.tags` (which can take 30+ seconds with long-poll).

The secondary issue: nio's `MatrixRoom.tags` is a live dict managed by the
sync machinery. We cannot write to it directly without risking corruption. The
cleanest fix is to maintain a separate `_pending_tags` overlay in
`MatrixClient` that is consulted by `rooms()` and `_rooms_fast()`, OR to emit
`RoomsChanged` after each successful tag operation (the simpler fix).

### Proposed fix

After a successful `set_room_tag` call, emit `RoomsChanged` with the
current room list **after** also updating a `_tag_overrides` dict that
patches the tag set returned by `rooms()` / `_rooms_fast()` until the next
sync brings the canonical state.

Specifically:

1. Add `self._tag_overrides: dict[str, set[str]]` to `MatrixClient.__init__`.
   Key: room_id. Value: set of tags currently applied but not yet confirmed
   by a sync response.

2. In `set_room_tag`, after the HTTP call succeeds, add tag to
   `_tag_overrides[room_id]` and call `await self._emit(RoomsChanged(rooms=self.rooms()))`.

3. In `remove_room_tag`, after HTTP success, remove tag from
   `_tag_overrides[room_id]` and emit `RoomsChanged`.

4. In `rooms()` and `_rooms_fast()`, merge `_tag_overrides.get(room_id, set())`
   into the tags dict before building the `RoomSummary`.

5. In `_on_sync`, clear the `_tag_overrides` entry for any room that now has
   tags populated by nio (so the override doesn't persist past the next sync).

Alternative (simpler, lower risk): skip the override dict and just emit
`RoomsChanged` after the HTTP call. Accept that between the HTTP call and the
next sync, `rooms()` reads the stale nio tags (so the icon would not appear
immediately). This is still better than the current behaviour where the icon
NEVER appears until the next poll cycle. The simpler fix is preferred for this
bug fix plan.

**Preferred fix (simple):** add `await self._emit(RoomsChanged(rooms=self.rooms()))` at the end of `set_room_tag` and `remove_room_tag`, AND maintain `_tag_overrides` so that `rooms()` returns the correct tags immediately.

### Test cases needed

**`tests/tui/test_room_context_menu.py`**

`test_mute_shows_bell_icon_after_toggle` — right-click room → click Mute →
`await pilot.pause()` → assert the `RoomItem` label contains `🔕`.

`test_unmute_removes_bell_icon` — set up room with `tags={"m.mute": None}` →
right-click → click Unmute → assert the label no longer contains `🔕`.

These require `FakeMatrixClient` to emit a `RoomsChanged` event after
`set_room_tag` / `remove_room_tag`. Add this behaviour to `tests/fakes.py`:
after recording the tag change, rebuild `rooms_data` to reflect the new tags
and call `await self.emit(RoomsChanged(...))`.

**`tests/matrix/test_tag_operations.py`** (new Tier-1 file)

`test_set_room_tag_emits_rooms_changed` — use `aioresponses` cassette or
synthetic fixture; call `client.set_room_tag(room_id, "m.mute")`; assert a
`RoomsChanged` event is emitted to subscribers and the returned `RoomSummary`
for that room includes `"m.mute"` in its tags.

`test_remove_room_tag_emits_rooms_changed` — symmetric.

---

## Bug 3 — Emoji picker: clicking an emoji does nothing

### Root cause

`src/telemente/tui/widgets/message_view.py`, `_open_emoji_picker_for`
(lines 531–541) and `_handle_emoji_value` (lines 543–554).

The `_on_picked` callback is defined as:

```python
def _on_picked(emoji: str | None) -> None:
    if emoji:
        self._handle_emoji_value(emoji)
```

`_handle_emoji_value` reads `self._react_target_event_id` which was set
immediately before `push_screen`:

```python
self._react_target_event_id = event_id
self.app.push_screen(EmojiPickerScreen(), _on_picked)
```

This is correct when `_open_emoji_picker_for` is called directly (e.g.
from the keyboard `action_react` or the command palette). **The bug only
manifests when called from the context menu closure**:

```python
def _react() -> None:
    self._open_emoji_picker_for(msg.event_id)
```

This closure is created in `on_message_row_context_menu_request` and stored
inside a `MenuItem`. `MenuItem.action` is typed `Callable[[], object]`. The
`_react` function returns `None` implicitly.

The root cause is **Bug 1 again**: if the menu item click doesn't fire (see
Bug 1), `_react()` is never called, so `_open_emoji_picker_for` is never
called, so the picker never appears and no reaction is sent. The
`test_react_item_opens_emoji_picker` test passes because it calls
`pilot.click(react_items[0])` which correctly dispatches the Click directly
to the `Static` — but the test does NOT verify that `send_reaction` was
eventually called (it only checks that `EmojiPickerScreen` is the active
screen). Bug 3 is therefore a **downstream consequence of Bug 1** for the
React action specifically.

There is additionally a secondary issue: `test_emoji_picker_integration_sends_reaction`
(in `tests/tui/test_emoji_picker.py`) calls `view._open_emoji_picker_for`
directly (not via context menu) and does assert `fake.sent_reactions`. This
test passes. So the emoji picker machinery itself is correct.

### Proposed fix

Fix Bug 1 first (swap action/dismiss order). Then add a test that exercises
the full path: right-click → React → emoji picker appears → click emoji →
assert `fake.sent_reactions` is populated.

### Test cases needed

**`tests/tui/test_message_context_menu.py`**

`test_react_via_context_menu_sends_reaction` — open room with a message,
post `ContextMenuRequest` to get the menu, click the React `Static`,
`await pilot.pause()` (picker appears), click the first emoji button,
`await pilot.pause()`, assert `fake.sent_reactions == [(room_id, event_id, emoji)]`.

---

## Bug 4 — Emoji picker flickers / re-renders on mouse movement

### Root cause

`src/telemente/tui/screens/emoji_picker.py`, `_populate_grid` (lines 192–196)
and the `tooltip=name` argument on line 196.

Two separate causes combine:

**Cause A — Full grid remount on every keystroke.**
`_populate_grid` calls `grid.remove_children()` then mounts N new `Button`
widgets. This destroys and recreates the entire DOM subtree on every
`Input.Changed` event. Textual schedules a full layout pass after each
`mount()` call, so even a single keystroke causes N+1 DOM mutations (one
`remove_children` + N mounts). When the search field is empty (the default),
this also runs on `on_mount`, which is unavoidable for initial population, but
the full-remount pattern on every filter change is the defect.

**Cause B — Tooltip DOM mutations on hover.**
Each `Button` is created with `tooltip=name`. Textual 0.8.x manages tooltips
by mounting a `Tooltip` widget into the screen on hover and removing it when
the mouse moves away. These DOM mutations (add/remove `Tooltip`) trigger a
layout recalculation for the containing screen, which forces the emoji grid to
repaint. With ~100 buttons, moving the mouse across the grid fires one
mount+remove cycle per button boundary, causing continuous flicker.

The combination: the initial `_populate_grid` already creates 100 buttons;
then every hover fires a tooltip mount/remove; if the user is also typing in
the search field, each keystroke causes a full grid rebuild.

### Proposed fix

**Fix Cause B (required for the flicker report):** Remove `tooltip=name` from
the `Button` constructor. The emoji codepoint is visible in the button label;
a tooltip is not necessary. Replace it with an accessible `name` attribute if
needed:

```python
grid.mount(Button(codepoint, name=name))
```

**Fix Cause A (desirable for performance):** Replace the destroy-and-recreate
approach in `_populate_grid` with a diff-based update:

```python
def _populate_grid(self, emoji_list: list[tuple[str, str]]) -> None:
    grid = self.query_one("#emoji-grid", Grid)
    existing = list(grid.query(Button))
    # Reuse existing buttons where possible; add/remove only the delta.
    for i, (codepoint, _name) in enumerate(emoji_list):
        if i < len(existing):
            existing[i].label = codepoint
        else:
            grid.mount(Button(codepoint))
    for excess in existing[len(emoji_list):]:
        excess.remove()
```

This reuses existing `Button` widgets (just updating their label) instead of
destroying and remounting them, making filter changes O(delta) rather than
O(N).

### Test cases needed

**`tests/tui/test_emoji_picker.py`**

`test_emoji_picker_no_tooltip_on_buttons` — assert that buttons in the grid
have no `tooltip` attribute set (i.e. `button.tooltip is None` or
`button.tooltip == ""`).

`test_emoji_picker_filter_does_not_remount_all_buttons` — populate grid,
get the set of button widget ids, type a search query, assert that at least
one button id is the same (i.e. the button was reused, not destroyed and
recreated). This confirms the diff-based update works.

---

## Bug 5 — Delete message fires without confirmation

### Root cause

`src/telemente/tui/widgets/message_view.py`, `on_message_row_delete_request`
(lines 467–473), and `MessageView.on_message_row_context_menu_request`
(lines 475–498), specifically the `_delete` closure at line 489–490.

`on_message_row_delete_request` calls `run_worker(self._do_redact_and_remove(...))` immediately, with no confirmation step. Compare with `MainScreen._confirm_leave_room` which pushes `ConfirmScreen` and only proceeds on `confirmed == True`.

The keyboard action `action_delete` on `MessageRow` (line 246) also fires
`DeleteRequest` directly, so the keyboard path also lacks confirmation.

The context menu `_delete` closure:

```python
def _delete() -> None:
    self.post_message(MessageRow.DeleteRequest(msg))
```

posts `DeleteRequest` to `MessageView`, which calls `run_worker` immediately.

### Proposed fix

Push `ConfirmScreen` from `MessageView.on_message_row_delete_request` and only
proceed with `_do_redact_and_remove` if the user confirms. `MessageView`
already imports `self.app` (via the `TelementeApp` cast); `ConfirmScreen` is
already available at `telemente.tui.widgets.confirm_screen`.

```python
def on_message_row_delete_request(self, event: MessageRow.DeleteRequest) -> None:
    msg = event.message
    room_id = self._current_room_id
    if not room_id:
        return
    from telemente.tui.widgets.confirm_screen import ConfirmScreen

    def _on_confirmed(confirmed: bool | None) -> None:
        if confirmed:
            self.run_worker(
                self._do_redact_and_remove(room_id, msg), exclusive=False
            )

    self.app.push_screen(ConfirmScreen(f"Delete message?"), _on_confirmed)
```

The `MessageRow.action_delete` binding (keyboard `d`) posts `DeleteRequest` to
the same handler, so it will also gain confirmation for free.

### Test cases needed

**`tests/tui/test_message_context_menu.py`**

`test_delete_shows_confirm_dialog` — right-click own message → click Delete →
`await pilot.pause()` → assert `isinstance(app.screen, ConfirmScreen)`.

`test_delete_confirmed_calls_redact` — get to `ConfirmScreen`, dismiss with
`True` → `await asyncio.sleep(0.1)` → assert
`fake.redacted_messages == [(room_id, event_id)]`.

`test_delete_cancelled_does_not_redact` — get to `ConfirmScreen`, dismiss
with `False` → assert `fake.redacted_messages == []`.

`test_keyboard_delete_shows_confirm_dialog` — focus a `MessageRow`, press `d`,
assert `ConfirmScreen` is pushed (keyboard path also guarded).

---

## Bug 6 — Right-clicking a tab does nothing

### Root cause

`src/telemente/tui/screens/main.py`, `on_mouse_down` (lines 225–235) and
`_show_tab_context_menu` (lines 237–262).

The handler is registered on `MainScreen` (a `Screen`). When the user right-
clicks on a `ContentTab` (the actual widget rendered by `TabbedContent`),
Textual's `Screen._on_mouse_event` finds the `ContentTab` via `get_widget_at`
and forwards the `MouseDown` directly to it via `widget._forward_event`. The
event then bubbles: `ContentTab` → `ContentTabs` → `TabbedContent` →
`Horizontal#main-layout` → `MainScreen`. At `MainScreen`, `on_mouse_down`
fires and the while-loop finds `ContentTab` (which is an `isinstance(widget, Tab)`
hit). `_show_tab_context_menu` is called.

The code path is logically correct, but **there is no test that exercises it
end-to-end via mouse events**. All existing tests in `test_tab_context_menu.py`
call `screen._show_tab_context_menu(tab, 5, 5)` directly, bypassing
`on_mouse_down`. The comment in the test file explains: "pilot.click with
button=3 raises OutOfBounds for off-screen tabs."

The practical failure is caused by **the test suite not detecting a
regression** introduced when `pilot.click(tab, button=3)` was avoided. Textual
0.8.7 raises `OutOfBounds` when `pilot.click` targets a widget whose region is
outside the test terminal's viewport. Tabs are typically in the top 2 rows of
the screen; if the test terminal is sized normally, the tabs ARE visible. The
`OutOfBounds` guard in the comment is therefore overly cautious and causes the
`on_mouse_down` path to be untested.

A secondary candidate for actual failure: Textual 0.8.7 does not dispatch
`MouseDown` events to the Screen's user-defined `on_mouse_down` when the
Screen's `_on_mouse_event` path handles the event first. Specifically,
`screen._on_mouse_event` runs for EVERY mouse event (it is the entry point
from the terminal input loop). For `MouseDown`, it calls
`widget._forward_event(event._apply_offset(...))` which creates a NEW event
object and posts it to the ContentTab. This new object then bubbles. But the
ORIGINAL `MouseDown` that triggered `_on_mouse_event` on the Screen is NOT
bubbled upward — only the forwarded copy is. `MainScreen.on_mouse_down` fires
for the forwarded copy when it bubbles back up through the DOM tree.

If in practice no context menu appears on tab right-click, the most likely
cause is that `_show_tab_context_menu` calls `self.mount(menu)` which mounts
into the Screen, but the ContextMenu's `on_mount` calls
`self.absolute_offset = Offset(screen_x, screen_y)` — if the tab bar is near
the top of the screen, `screen_y` may be 0 or 1, which places the menu at the
very top. This is visually correct but can appear to do nothing if the user
does not notice the menu rendered at the top of the tab bar area.

### Proposed fix

1. Add a proper end-to-end test that uses `pilot.click(tab, button=3)` rather
   than calling `_show_tab_context_menu` directly. Resize the pilot terminal
   to ensure the tab bar is in the visible region.

2. In `_show_tab_context_menu`, clamp `screen_y` so the menu does not appear
   at row 0 or 1 (which would be hidden under the tab bar). Add a minimum
   `screen_y` of 2.

3. If the real failure is that `on_mouse_down` never fires for tabs: add an
   alternative intercept. `TabbedContent` or `ContentTabs` may need an
   `on_mouse_down` that re-posts the event to `MainScreen` or posts a custom
   message. The safest fix is to add `on_mouse_down` directly on `ContentTabs`
   (or `ContentTab`) to post a `TabRightClicked` message that `MainScreen`
   handles — this avoids relying on the Screen-level bubble path.

Preferred approach: add the end-to-end test first; if it passes, the test gap
was the only problem. If the test fails, implement a `TabRightClicked` custom
message posted from `ContentTab.on_mouse_down`.

### Test cases needed

**`tests/tui/test_tab_context_menu.py`**

`test_tab_right_click_via_mouse_event` — open a room tab, call
`pilot.click(tab, button=3)` after ensuring the tab is in the visible region
(use `pilot.resize(80, 24)` or equivalent), `await pilot.pause()`, assert
`isinstance(app.screen.query_one(ContextMenu), ContextMenu)`.

---

## Bug 7 — Room list context menu is too wide and overflows vertically

### Root cause

**Width:** `src/telemente/tui/styles/app.tcss`, lines 188–192:

```css
ContextMenu .menu-item {
    height: 1;
    padding: 0 1;
    width: 1fr;
}
```

`width: 1fr` on a child inside a container with `width: auto` (the
ContextMenu) is undefined behaviour in Textual's CSS engine. In practice,
Textual resolves this by expanding the `1fr` child to the full available width
of the parent, which is itself sized auto from its children — a circular
dependency. The result is that the ContextMenu expands to a default large
width (often the full terminal width or the parent container width) rather
than shrinking to fit the longest label.

The fix: change `ContextMenu .menu-item { width: 1fr; }` to `width: 100%`.
With `width: 100%`, the Static fills the ContextMenu's width (which is still
`auto`, determined by the widest item), without the circular expansion
problem. Alternatively, set `width: auto` on the menu item and let each
Static be as wide as its text content.

**Vertical overflow:** `src/telemente/tui/screens/main.py`,
`_show_context_menu` (lines 203–208) and `context_menu.py`, `on_mount` (line 85):

```python
def on_mount(self) -> None:
    self.absolute_offset = Offset(self._screen_x, self._screen_y)
```

The menu is positioned at the exact `screen_y` of the right-click. If the
click is near the bottom of the screen and the menu has several items, the
menu extends below the terminal boundary. Textual does not automatically clip
or reposition overflow menus.

The fix: in `_show_context_menu`, clamp the Y position:

```python
def _show_context_menu(self, items, screen_x, screen_y):
    self._dismiss_context_menu()
    # Clamp so the menu doesn't extend below the screen.
    menu_height_estimate = len([i for i in items if not isinstance(i, MenuSeparator)]) + 2
    max_y = self.size.height - menu_height_estimate
    clamped_y = min(screen_y, max(0, max_y))
    clamped_x = min(screen_x, max(0, self.size.width - 26))
    menu = ContextMenu(items, clamped_x, clamped_y)
    self._active_context_menu = menu
    self.mount(menu)
```

The `+2` accounts for the border. The `26` accounts for `min-width: 24` plus
2 for the border. The horizontal clamp prevents the menu from disappearing off
the right edge.

### Test cases needed

**`tests/tui/test_context_menu.py`**

`test_context_menu_width_fits_content` — Mount a ContextMenu with items of
known short labels; assert `menu.size.width <= 30` (i.e. it does not expand
to terminal width).

**`tests/tui/test_room_context_menu.py`**

`test_context_menu_does_not_overflow_screen` — right-click a room at
coordinates near the bottom of the screen; assert that
`menu.absolute_offset.y + menu.size.height <= app.screen.size.height`.

---

## Files to modify

| File | Changes |
|---|---|
| `src/telemente/tui/widgets/context_menu.py` | Swap `_dismiss()` / `entry.action()` order in `_activate_focused` and `on_click` |
| `src/telemente/matrix/client.py` | `set_room_tag` and `remove_room_tag`: add `_tag_overrides` dict, merge into `rooms()` / `_rooms_fast()`, emit `RoomsChanged` on success |
| `src/telemente/tui/widgets/message_view.py` | `on_message_row_delete_request`: push `ConfirmScreen` before redacting |
| `src/telemente/tui/screens/emoji_picker.py` | Remove `tooltip=name` from Button; replace full-remount `_populate_grid` with diff-based update |
| `src/telemente/tui/screens/main.py` | `_show_context_menu`: clamp `screen_x` / `screen_y` to prevent overflow; if tab right-click needs a code fix, add `TabRightClicked` message from a `ContentTabs` subclass |
| `src/telemente/tui/styles/app.tcss` | Change `ContextMenu .menu-item { width: 1fr; }` to `width: 100%` |
| `tests/fakes.py` | `FakeMatrixClient.set_room_tag` / `remove_room_tag`: after recording the call, rebuild `rooms_data` tags and `await self.emit(RoomsChanged(...))` |
| `tests/tui/test_context_menu.py` | Add `test_click_item_fires_action`, `test_context_menu_width_fits_content` |
| `tests/tui/test_message_context_menu.py` | Add `test_react_via_context_menu_sends_reaction`, `test_delete_shows_confirm_dialog`, `test_delete_confirmed_calls_redact`, `test_delete_cancelled_does_not_redact`, `test_keyboard_delete_shows_confirm_dialog` |
| `tests/tui/test_room_context_menu.py` | Add `test_mute_shows_bell_icon_after_toggle`, `test_unmute_removes_bell_icon`, `test_context_menu_does_not_overflow_screen` |
| `tests/tui/test_tab_context_menu.py` | Add `test_tab_right_click_via_mouse_event` |
| `tests/tui/test_emoji_picker.py` | Add `test_emoji_picker_no_tooltip_on_buttons`, `test_emoji_picker_filter_does_not_remount_all_buttons` |
| `tests/matrix/test_tag_operations.py` | New Tier-1 file: `test_set_room_tag_emits_rooms_changed`, `test_remove_room_tag_emits_rooms_changed` |

---

## Done-when checklist

- [ ] `ContextMenu`: action fires before dismiss in both `_activate_focused` and `on_click`.
- [ ] `test_click_item_fires_action` is green.
- [ ] `set_room_tag` / `remove_room_tag` in `MatrixClient` emit `RoomsChanged` after success, with tag overrides reflected in `rooms()`.
- [ ] `FakeMatrixClient.set_room_tag` / `remove_room_tag` emit `RoomsChanged` to subscribers.
- [ ] `test_mute_shows_bell_icon_after_toggle` is green; `test_unmute_removes_bell_icon` is green.
- [ ] `test_set_room_tag_emits_rooms_changed` (Tier-1) is green.
- [ ] `test_react_via_context_menu_sends_reaction` is green (confirms Bug 3 is fixed as a consequence of Bug 1 fix).
- [ ] Emoji buttons have no `tooltip`; grid uses diff-based update.
- [ ] `test_emoji_picker_no_tooltip_on_buttons` is green.
- [ ] `test_emoji_picker_filter_does_not_remount_all_buttons` is green.
- [ ] `MessageView.on_message_row_delete_request` pushes `ConfirmScreen`.
- [ ] `test_delete_shows_confirm_dialog`, `test_delete_confirmed_calls_redact`, `test_delete_cancelled_does_not_redact` are green.
- [ ] `test_keyboard_delete_shows_confirm_dialog` is green.
- [ ] `test_tab_right_click_via_mouse_event` is green (and if it fails, `TabRightClicked` message is implemented and tested).
- [ ] `ContextMenu .menu-item` uses `width: 100%` in `app.tcss`.
- [ ] `_show_context_menu` clamps x/y to prevent overflow.
- [ ] `test_context_menu_width_fits_content` is green.
- [ ] `test_context_menu_does_not_overflow_screen` is green.
- [ ] `uv run ruff check .` clean.
- [ ] `uv run mypy` clean.
- [ ] `pyright src/` clean.
- [ ] `uv run pytest` all green.
