# 0005 — Main Screen Layout

## Goal

The three-panel main screen: left room list (collapsible), center message view,
right member list (collapsible). Establishes layout, collapse bindings, focus
order, header/footer — the shell that 0006/0007/0008 fill in.

## Dependencies

- 0003 (`MatrixClient` / `FakeMatrixClient`).
- 0004 (navigation into this screen after login).

## Files to create / modify

- `src/telemente/tui/screens/main.py` — new (`MainScreen`).
- `src/telemente/tui/styles/app.tcss` — add layout rules.
- `tests/tui/test_main_screen.py` — new.
- (0006/0007/0008 provide the real `RoomList`/`MessageView`/`MemberList`; this
  plan may mount lightweight placeholders behind the same widget ids/classes so
  layout/collapse can be built and tested independently, then swapped.)

## Public interface

```python
# src/telemente/tui/screens/main.py
class MainScreen(Screen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        ("ctrl+b", "toggle_rooms", "Rooms"),
        ("ctrl+r", "toggle_members", "Members"),
        ("ctrl+k", "focus_search", "Search rooms"),
    ]
    def __init__(self, client: "MatrixClient") -> None: ...
    def action_toggle_rooms(self) -> None: ...
    def action_toggle_members(self) -> None: ...
```

## Behavior / layout

- `compose`: `Header()`, a `Horizontal` containing:
  - left: `RoomList` (id `#rooms-panel`), width e.g. `30` cols.
  - center: `MessageView` (id `#message-panel`), `width: 1fr` (always visible).
  - right: `MemberList` (id `#members-panel`), width e.g. `24` cols.
  - `Footer()`.
- **Collapse**: toggling sets the side panel's `display` (or a `.collapsed`
  class) off/on. Center keeps `1fr` and naturally fills the freed space. Persist
  collapse state on the screen instance (reactive bools `rooms_visible`,
  `members_visible`). Default: both visible.
- **Focus order**: search input → room list → composer/message view → member
  list. `ctrl+k` focuses the room-list search (0006).
- Wire selection flow placeholder: when `RoomList` posts `RoomSelected`
  (0006), `MainScreen` tells `MessageView` and `MemberList` to load that room
  (full event routing is finalized in 0009).
- TCSS: define widths, borders/titles per panel, and a `.collapsed { display:
  none; }` rule.

## Test cases (write first)

`tests/tui/test_main_screen.py` (host app pushes `MainScreen(FakeMatrixClient())`):

1. `test_three_panels_present` — after mount, `query_one("#rooms-panel")`,
   `#message-panel`, `#members-panel` all exist and are displayed.
2. `test_toggle_rooms_hides_and_shows` — `pilot.press("ctrl+b")`; assert rooms
   panel not displayed; press again → displayed. Center always displayed.
3. `test_toggle_members_hides_and_shows` — same for `ctrl+r`.
4. `test_center_always_visible` — collapse both sides; `#message-panel` still
   displayed and occupies remaining width (`region.width` grew vs. baseline).
5. `test_focus_search_binding` — `pilot.press("ctrl+k")`; assert the room search
   input has focus (`app.focused.id == "room-search"`).

## Mocking strategy

- Inject `FakeMatrixClient`. No network. Use `await pilot.pause()` after key
  presses so reactive/CSS updates apply before asserting `widget.display` /
  `widget.region`.

## Done-when

- [ ] All 5 tests pass.
- [ ] Both side panels collapse/expand via bindings; center never disappears.
- [ ] Footer shows the binding hints; focus order is sensible.
- [ ] `mypy --strict` + `ruff` clean.
