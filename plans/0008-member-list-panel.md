# 0008 — Member List Panel

## Goal

The right panel: the member list for the selected room. Shows display names
(with a marker for elevated power levels), updates when the room changes and
when membership events arrive.

## Dependencies

- 0003 (`Member`, `MatrixClient.members` / `FakeMatrixClient`).
- 0005 (mounts this as the right panel).

## Files to create / modify

- `src/telemente/tui/widgets/member_list.py` — new (`MemberList`).
- `src/telemente/tui/styles/app.tcss` — member styling.
- `tests/tui/test_member_list.py` — new.

## Public interface

```python
# src/telemente/tui/widgets/member_list.py
class MemberList(Widget):
    def load_room(self, room_id: str) -> None: ...        # pull members from client
    def set_members(self, members: list[Member]) -> None: ...
    @property
    def member_count(self) -> int: ...
```

## Behavior / layout

- `compose`: a header showing the count ("Members — N") and a scrollable list
  (`ListView`/`OptionList`) of members.
- **load_room(room_id)**: `members = client.members(room_id)` (sync accessor),
  store `current_room_id`, render. Sort by power level desc, then display name.
- **Rendering**: display name; prefix/marker for power level (e.g. `~` admin ≥
  100, `+` moderator ≥ 50). Keep glyphs ASCII-friendly.
- **Updates**: a `MembersChanged` event for the current room (delivered by 0009)
  → `set_members(...)` re-renders. Events for other rooms are ignored.

## Test cases (write first)

`tests/tui/test_member_list.py` (host app mounts `MemberList(FakeMatrixClient)`):

1. `test_load_room_renders_members` — fake returns 3 members; `load_room`;
   `member_count == 3`; names rendered.
2. `test_sorted_by_power_then_name` — members with power 100/50/0 →
   admin first; ties broken by name.
3. `test_power_level_marker` — a power-100 member renders the admin marker.
4. `test_switching_rooms_updates_list` — load room A (2 members) then room B
   (4 members) → `member_count == 4`, B's members shown.
5. `test_set_members_updates_render` — after load, call `set_members` with a
   changed list (e.g. a join) → new member appears, count updates.

## Mocking strategy

- No network. `FakeMatrixClient.members(room_id)` returns scripted `Member`
  lists. `await pilot.pause()` after `load_room`/`set_members` before asserting
  rendered output. Membership updates are simulated by calling `set_members`
  directly (the 0009 plan covers routing real events here).

## Done-when

- [ ] All 5 tests pass.
- [ ] Members load per room, sorted with power markers; updates re-render;
      other-room events ignored.
- [ ] `mypy --strict` + `ruff` clean.
