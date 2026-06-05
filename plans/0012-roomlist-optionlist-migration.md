# Plan 0012 — Migrate RoomList from ListView to OptionList

## Context

The remaining flicker in the room list panel has two causes:

1. `_rebuild()` defers the DOM update with `call_after_refresh(_refresh_list)`,
   so room-list changes repaint in a separate tick from member/message patches
   fired in the same `handle_rooms_changed` call.
2. `ListView.clear()` + remount in the slow path mounts N widgets individually
   before Textual can coalesce them, even with `app.batch_update()`.

Textual's `OptionList` widget offers synchronous `clear_options()` /
`add_option()` / `replace_option_prompt()`, so all mutations in
`handle_rooms_changed` happen in the same tick and Textual collapses them into
one repaint.  The `call_after_refresh` deferred pattern goes away entirely.

## Current state (as of this writing)

`RoomList` already has:
- A working fast path in `_refresh_list`: when room order and membership are
  unchanged it calls `RoomItem.update_room()` in-place (no remount).
- `app.batch_update()` wrapping the slow-path rebuild.
- `update_unread()` patching a single item surgically.

The fast/slow-path tests added in the previous commit assert `RoomItem` instance
identity — they will need to be **replaced** (not just updated) because
`OptionList` has no per-option widget identity to compare.

The context-menu feature (`RoomItem.ContextMenuRequest` → `RoomList.RoomContextMenu`)
uses `on_mouse_down` on `RoomItem` and bubbles through `on_room_item_context_menu_request`.
`OptionList` renders options internally and does not expose per-option widgets,
so the right-click path must be reimplemented on the `OptionList` container
itself using `on_mouse_down`, mapping screen coordinates back to the hovered
option via `OptionList.get_option_at_line` or by tracking `OptionList.highlighted`
on hover.

## Goals

1. Replace `ListView` / `RoomItem(ListItem)` with `OptionList` / `Option`.
2. Eliminate `call_after_refresh` — `_refresh_list` becomes synchronous.
3. `update_unread` uses `replace_option_prompt` (no `clear_options`).
4. Keep the public API of `RoomList` unchanged:
   `set_rooms`, `set_active_room`, `apply_filter`, `set_sort_mode`,
   `update_unread`, `all_rooms`, `visible_rooms`, `RoomSelected`,
   `RoomContextMenu`.
5. All existing tests remain green except the two instance-identity tests
   (see §Tests below).

## Invariants to preserve

- Search filter (debounced 150 ms) still works via `apply_filter`.
- Active room highlighted — use `ol.highlighted = idx` (integer index).
- Unread badge, encryption lock, favourite star, low-priority arrow, mute bell
  rendered in the option prompt (Rich markup string).
- Right-click on a room item still emits `RoomList.RoomContextMenu` carrying the
  correct `RoomSummary`, `screen_x`, `screen_y`.
- `update_unread` patches a single option without rebuilding the list.
- `set_rooms` full rebuild only when room list identity changes.

## Design

### Imports to add / remove

```python
# Add
from textual.widgets import OptionList
from textual.widgets.option_list import Option, OptionDoesNotExist

# Remove
from textual.widgets import ListItem, ListView
```

Remove the `RoomItem` class entirely.  The `RoomList.RoomContextMenu` message
class and `RoomList.RoomSelected` message class are **unchanged**.

### ID helpers

```python
import re
_INVALID_ID_CHARS = re.compile(r"[^a-zA-Z0-9_-]")

def _option_id(room_id: str) -> str:
    return "opt-room-" + _INVALID_ID_CHARS.sub("-", room_id)
```

Because `-` in a room ID maps to `-` (identity), the substitution is not always
invertible from the string alone.  Use a side-table:

```python
# instance attribute on RoomList
self._opt_to_room: dict[str, str] = {}   # option_id -> room_id
```

Populated in `_refresh_list` alongside each `add_option` call.

### `compose`

```python
def compose(self) -> ComposeResult:
    with Horizontal(id="search-bar"):
        yield Input(id="room-search", placeholder="Search rooms…")
        yield Button("✕", id="clear-search")
    yield Static("Syncing…", id="room-list--loading", classes="room-loading-state")
    yield OptionList(id="room-list-view")
    yield Static("No rooms match", id="room-list--empty-state", classes="room-empty-state")
```

### `_render_name` (free function, replaces `RoomItem._render_name`)

Identical logic to `RoomItem._render_name`; extract it as a module-level
function so both `_refresh_list` and `update_unread` can call it.

### `_refresh_list` — now synchronous, no `call_after_refresh`

```python
def _refresh_list(self) -> None:
    ol = self.query_one("#room-list-view", OptionList)
    self._opt_to_room.clear()
    ol.clear_options()
    for room in self._visible_rooms:
        oid = _option_id(room.room_id)
        self._opt_to_room[oid] = room.room_id
        ol.add_option(Option(_render_name(room), id=oid))
    self._apply_active_highlight()
    self._sync_empty_state()
```

Remove the `call_after_refresh(self._refresh_list)` call in `_rebuild`;
call `self._refresh_list()` directly instead.

### `update_unread`

```python
def update_unread(self, room_id: str, count: int) -> None:
    # patch _all_rooms and _visible_rooms (same logic as today)
    ...
    ol = self.query_one("#room-list-view", OptionList)
    oid = _option_id(room_id)
    updated = next((r for r in self._visible_rooms if r.room_id == room_id), None)
    if updated is not None:
        try:
            ol.replace_option_prompt(oid, _render_name(updated))
        except OptionDoesNotExist:
            pass  # room filtered out — not currently visible
```

### `_apply_active_highlight`

```python
def _apply_active_highlight(self) -> None:
    ol = self.query_one("#room-list-view", OptionList)
    if self._active_room_id is None:
        return
    oid = _option_id(self._active_room_id)
    try:
        ol.highlighted = ol.get_option_index(oid)
    except OptionDoesNotExist:
        pass
```

### Selection handler

```python
def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
    room_id = self._opt_to_room.get(event.option.id or "")
    if room_id:
        logger.info("Room selected: %s", room_id)
        self.post_message(RoomList.RoomSelected(room_id))
```

Remove `on_list_view_selected`.

### Context menu (right-click)

`OptionList` does not expose per-option widget handles, so handle `on_mouse_down`
on the `OptionList` container and use `OptionList.highlighted` (the index of the
currently-highlighted option, updated on hover) to identify the target room:

```python
def on_option_list_mouse_down(self, event: events.MouseDown) -> None:
    if event.button != 3:
        return
    event.stop()
    ol = self.query_one("#room-list-view", OptionList)
    idx = ol.highlighted
    if idx is None or idx >= len(self._visible_rooms):
        return
    room = self._visible_rooms[idx]
    self.post_message(
        RoomList.RoomContextMenu(room, event.screen_x, event.screen_y)
    )
```

Remove `on_room_item_context_menu_request` — it is no longer needed.

### CSS

Replace the `RoomItem`-specific CSS rules with `OptionList` equivalents.
`OptionList` handles its own hover and highlight styling; remove the
`RoomItem.-highlight` and `RoomItem:hover` rules.  Add any overrides needed
to match the existing visual appearance.

## Tests

### Tests that must be **replaced** (not just updated)

These two tests assert `RoomItem` instance identity and are invalidated by the
migration.  Delete them and substitute the tests below:

- `test_set_rooms_same_order_patches_items_in_place`
- `test_set_rooms_order_change_rebuilds_correctly`

**Replacement — Test F: `_refresh_list` is synchronous**

After `set_rooms(rooms)`, `visible_rooms` is updated *without* an extra
`pilot.pause()` to drain a deferred callback:

```python
room_list.set_rooms([r1, r2])
# No await pilot.pause() here
assert len(room_list.visible_rooms) == 2
```

**Replacement — Test G: OptionList has correct option count after set_rooms**

```python
ol = room_list.query_one(OptionList)
assert ol.option_count == 2
```

### Tests that need **mechanical updates** (ListView → OptionList)

Any test that imports `ListView` or `RoomItem`, or calls
`room_list.query_one(ListView)`, needs the import/query updated:

- `test_selecting_posts_roomselected` — replace `ListView` focus/keypress with
  `OptionList` focus/keypress; `"down"` + `"enter"` still work on `OptionList`.
- `test_set_active_room_highlights_matching_item` — assert
  `ol.highlighted == expected_index` instead of `-highlight` CSS class.
- `test_active_highlight_survives_set_rooms_rebuild` — same.
- `test_switch_active_room_moves_highlight` — same.
- `test_unread_badge_rendered` — query the option prompt string instead of a
  `Label` widget.
- `test_unread_room_name_is_bold` — same.
- `test_read_room_name_is_plain` — same.
- `test_favourite_tag_shows_star` — same.
- `test_lowpriority_tag_shows_arrow` — same.
- `test_mute_tag_shows_bell` — same.
- `test_update_unread_patches_label_in_place` — assert `ol.option_count`
  unchanged and prompt contains new count; no `RoomItem` identity check.
- `test_debounced_search_does_not_rebuild_per_keystroke` — no structural change
  needed; `visible_rooms` API is unchanged.

### New tests to add (A–E from original plan, updated)

**Test A — `set_rooms` populates OptionList with correct IDs**

```python
ol = room_list.query_one(OptionList)
assert ol.option_count == 2
assert ol.get_option_index(_option_id("!a:h")) == 0
```

**Test B — `update_unread` calls `replace_option_prompt`, not `clear_options`**

Monkeypatch `OptionList.clear_options` on the instance; call `update_unread`;
assert it was NOT called and the option prompt contains the new count.

**Test C — `RoomSelected` carries correct room_id for IDs with special chars**

Use a room_id containing `:` and `.` (e.g. `"!abc:example.com"`); select it;
assert `RoomSelected.room_id == "!abc:example.com"`.

**Test D — filter hides/restores options without full widget replacement**

After `apply_filter("rand")`, `ol.option_count == 1`.
After `apply_filter("")`, `ol.option_count == 2`.

**Test E — active highlight index set correctly after `_refresh_list`**

After `set_active_room("!b:h")` + `set_rooms([ra, rb])`:
`ol.highlighted == ol.get_option_index(_option_id("!b:h"))`.

## Migration steps

1. Write failing tests for A–E plus the two replacement tests F and G (all red).
2. Add `OptionList`, `Option`, `OptionDoesNotExist` imports; remove `ListView`,
   `ListItem` imports; remove `RoomItem` class.
3. Add `_render_name` and `_option_id` free functions; add `_opt_to_room: dict`
   instance attribute in `__init__`.
4. Swap `ListView` for `OptionList` in `compose`.
5. Rewrite `_refresh_list` (synchronous), `_rebuild` (remove
   `call_after_refresh`), `update_unread`, `_apply_active_highlight`,
   `on_list_view_selected` → `on_option_list_option_selected`,
   `on_room_item_context_menu_request` → `on_option_list_mouse_down`.
6. Update CSS.
7. Update the mechanically-affected existing tests; delete the two
   instance-identity tests.
8. Run full suite — all green.
9. `uv run ruff check .` · `uv run ruff format .` · `uv run mypy` · `pyright src/`.
10. Commit with message: `feat(tui): migrate RoomList from ListView to OptionList (plan 0012)`.

## Out of scope

- Keyboard navigation beyond what `OptionList` provides by default.
- Virtual scrolling (Textual handles this internally for `OptionList`).
- E2EE or other feature work.
