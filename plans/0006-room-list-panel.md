# 0006 — Room List Panel (searchable)

## Goal

The left panel: a filterable, selectable list of rooms with a search box at the
top. Live-filters by name as the user types, sorts by recent activity, shows
unread indicators, and emits a `RoomSelected` message when a room is chosen.

## Dependencies

- 0003 (`RoomSummary`, `MatrixClient`/`FakeMatrixClient`).
- 0005 (mounts this widget as the left panel).

## Files to create / modify

- `src/telemente/tui/widgets/room_list.py` — new (`RoomList`).
- `src/telemente/tui/styles/app.tcss` — room-list styling.
- `tests/tui/test_room_list.py` — new.

## Public interface

```python
# src/telemente/tui/widgets/room_list.py
class RoomList(Widget):
    class RoomSelected(TextualMessage):
        def __init__(self, room_id: str) -> None: ...

    def set_rooms(self, rooms: list[RoomSummary]) -> None: ...
    def apply_filter(self, query: str) -> None: ...   # case-insensitive substring
    @property
    def visible_rooms(self) -> list[RoomSummary]: ...  # current filtered+sorted view
```

## Behavior / layout

- `compose`: an `Input(id="room-search", placeholder="Search rooms…")` above a
  scrollable selectable list. Use a `ListView` of `ListItem`s (or an
  `OptionList`) — choose `ListView` for easy custom item widgets.
- **Data**: `set_rooms()` stores the full list; rebuilds the visible view by
  applying the current filter then sorting by `last_activity` desc (rooms with
  `None` last_activity sort last, by name). Each item shows display name and, if
  `unread_count > 0`, a badge (e.g. `(3)`); mark encrypted rooms with a lock
  glyph.
- **Filter**: `on_input_changed` for `#room-search` calls `apply_filter(value)`;
  case-insensitive substring over `display_name`. Empty query → all rooms.
  No match → an empty-state row ("No rooms match").
- **Selection**: `ListView.Selected` (Enter/click) → post `RoomSelected(room_id)`
  for the highlighted room.
- Keep current selection stable across filter changes when possible.

## Test cases (write first)

`tests/tui/test_room_list.py` (host app mounts `RoomList`; feed `RoomSummary`s):

1. `test_set_rooms_renders_all` — set 3 rooms → 3 items; `visible_rooms` has 3.
2. `test_filter_substring_case_insensitive` — rooms "General", "Random",
   "Dev"; type "ra" → only "Random"; `visible_rooms == ["Random"]`.
3. `test_empty_filter_restores_all` — filter then clear → all rooms again.
4. `test_no_match_shows_empty_state` — filter "zzz" → empty-state present, zero
   room items.
5. `test_sorted_by_recent_activity` — rooms with different `last_activity` →
   `visible_rooms` ordered newest first; `None` last.
6. `test_selecting_posts_roomselected` — highlight 2nd room, `pilot.press
   ("enter")`; assert a `RoomSelected` with that `room_id` was posted (recorded
   by host app).
7. `test_unread_badge_rendered` — a room with `unread_count=3` shows "(3)" /
   the badge in its rendered item.

## Mocking strategy

- No network. Build `RoomSummary` fixtures directly. Host app records
  `RoomList.RoomSelected` via `on_room_list_room_selected`. `await pilot.pause()`
  after typing so `Input.Changed` → filter applies before asserting.

## Done-when

- [ ] All 7 tests pass.
- [ ] Live filtering, recent-activity sort, unread/encrypted markers work.
- [ ] Selecting a room posts `RoomSelected(room_id)`.
- [ ] `mypy --strict` + `ruff` clean.
