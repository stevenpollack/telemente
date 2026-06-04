# Plan 0012 — Migrate RoomList from ListView to OptionList

## Context

Fixes 1–4 (plans 0009 performance addendum) reduced redundant `RoomsChanged`
emits, added debounced search, and made unread updates surgical.  The remaining
bottleneck is `RoomList._refresh_list`: every filter or sort change tears down
the entire `ListView` DOM and rebuilds it from scratch.  Textual's `OptionList`
widget is designed for exactly this use case and offers `replace_option` for
in-place mutation, eliminating the full rebuild for common operations.

## Goals

1. Replace `ListView` / `ListItem` (`_RoomItem`) with `OptionList` / `Option`.
2. In-place option mutation for unread-count changes (`replace_option`) — no
   full rebuild on every `NewMessage`.
3. Keep the public API of `RoomList` unchanged:
   `set_rooms`, `set_active_room`, `apply_filter`, `set_sort_mode`,
   `update_unread`, `all_rooms`, `visible_rooms`, `RoomSelected`.
4. Retain all existing tests (no test removals, only additions).

## Invariants to preserve

- Search filter (debounced 150 ms) still works via `apply_filter`.
- Active room highlighted with `-highlight` CSS class (or `OptionList`
  equivalent — `highlighted` pseudo-class on the focused option).
- Unread badge and encryption lock rendered in the option label (markup).
- `set_rooms` full rebuild happens only when the room list identity changes
  (fingerprinted at the `MatrixClient` level — already done in Fix 1).
- `update_unread` patches a single option without rebuilding the list.

## Design

### Data model

Replace `_RoomItem(ListItem)` with a plain helper:

```python
def _option_id(room_id: str) -> str:
    safe = _INVALID_ID_CHARS.sub("-", room_id)
    return f"opt-room-{safe}"
```

Each `OptionList.Option` carries `id=_option_id(room_id)` and
`prompt=_render_name(room)` (rich markup string).

### `_refresh_list` — full rebuild path

Used only by `set_rooms` (and indirectly `apply_filter` / `set_sort_mode`):

```python
def _refresh_list(self) -> None:
    ol = self.query_one("#room-list-view", OptionList)
    ol.clear_options()
    for room in self._visible_rooms:
        ol.add_option(Option(_render_name(room), id=_option_id(room.room_id)))
    self._apply_active_highlight()
    self._sync_empty_state()
```

### `update_unread` — surgical path

```python
def update_unread(self, room_id: str, count: int) -> None:
    # patch _all_rooms and _visible_rooms (same as today)
    ...
    # replace just the affected option
    ol = self.query_one("#room-list-view", OptionList)
    oid = _option_id(room_id)
    try:
        updated = next(r for r in self._all_rooms if r.room_id == room_id)
        ol.replace_option_prompt(oid, _render_name(updated))
    except OptionDoesNotExist:
        pass  # room not currently visible (filtered out)
```

### Selection

`OptionList.OptionSelected` replaces `ListView.Selected`:

```python
def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
    room_id = _option_id_to_room_id(event.option.id)
    self.post_message(RoomList.RoomSelected(room_id))
```

`_option_id_to_room_id` is the inverse of `_option_id`:

```python
_OPT_PREFIX = "opt-room-"

def _option_id_to_room_id(oid: str | None) -> str:
    # Reverse the substitution: "-" could be original or substituted.
    # Store a side-map room_id -> option_id at set_rooms time to avoid
    # ambiguity.
```

Because `-` in a room ID is substituted to `-` (identity), the mapping is
not always invertible purely from the ID string.  Use a `dict[str, str]`
side-table `_opt_to_room: dict[str, str]` populated in `_refresh_list`.

### Active highlight

`OptionList` does not expose a `-highlight` class per option.  Use the
`highlighted` reactive on `OptionList` to scroll-to and visually select the
active room:

```python
def _apply_active_highlight(self) -> None:
    ol = self.query_one("#room-list-view", OptionList)
    if self._active_room_id is None:
        return
    oid = _option_id(self._active_room_id)
    try:
        idx = ol.get_option_index(oid)
        ol.highlighted = idx
    except OptionDoesNotExist:
        pass
```

## Test cases

All existing tests must remain green.  Add:

### Test A — `set_rooms` populates OptionList

`room_list.set_rooms([r1, r2])` → `OptionList` has exactly 2 options with the
correct IDs.

### Test B — `update_unread` calls `replace_option_prompt`, not `clear_options`

Spy on `OptionList.clear_options`: call `update_unread`; assert
`clear_options` was NOT called and the option's prompt contains the new count.

### Test C — `RoomSelected` message carries correct room_id

Click / select an option; assert `RoomSelected.room_id` matches the original
room_id (including rooms whose IDs contain characters substituted in
`_option_id`).

### Test D — filter hides options without full widget replacement

After `apply_filter("rand")`, `OptionList` contains only the matching option.
After `apply_filter("")`, both options are restored.

### Test E — active highlight set after `_refresh_list`

After `set_active_room(room_id)` + `set_rooms(...)`, the option at
`_option_id(room_id)` is highlighted (`ol.highlighted == expected_index`).

## Migration steps

1. **Write failing tests A–E** (all red).
2. Add `OptionList`, `Option`, `OptionDoesNotExist` imports; remove `ListView`,
   `ListItem` imports.
3. Replace `_RoomItem` class with `_render_name` and `_option_id` free
   functions plus `_opt_to_room` side-table.
4. Swap `ListView` widget for `OptionList` in `compose`.
5. Rewrite `_refresh_list`, `update_unread`, `_apply_active_highlight`,
   `on_list_view_selected` → `on_option_list_option_selected`.
6. Run full suite — all green.
7. Run `ruff check`, `ruff format`, `mypy --strict`.
8. Commit.

## Out of scope

- Keyboard navigation beyond what `OptionList` provides by default.
- Virtual scrolling (Textual handles this internally for `OptionList`).
- E2EE or other feature work.
