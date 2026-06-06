# Plan 0026 — TUI test suite refactor: behavioural assertions and structural hardening

## Goal

The TUI test suite passes today but ships broken features. The root cause is
structural: tests overwhelmingly assert internal widget state (DOM existence,
attribute values, CSS display flags) after calling methods directly on widgets,
rather than asserting observable user behaviour driven through the Textual event
system. This plan diagnoses every blind spot found in the current suite and
specifies the concrete changes that close them.

## Dependencies

- All prior plans (the suite being refactored is the one built up by 0004–0025).
- No new runtime dependencies; `pytest-textual-snapshot` is the only new dev
  dependency, and it is optional (Layer 2 snapshot tests can be deferred).

---

## Diagnosis: specific gaps found in the existing test suite

### Gap 1 — `run_test()` called without `size=` in the overwhelming majority of tests

**Affected files:** every file in `tests/tui/` except `test_context_menu.py`
(tests 6–9), `test_thread_panel.py`, `test_message_search.py`, and parts of
`test_tab_context_menu.py`.

**Evidence:**
- `test_main_screen.py`: all 12 tests call `app.run_test()` with no `size=`.
- `test_room_list.py`: all 29 tests call `app.run_test()` with no `size=`.
- `test_message_view.py`: all 22 tests call `app.run_test()` with no `size=`.
- `test_sync_integration.py`: all 12 tests call `app.run_test()` with no `size=`.
- `test_member_list.py`, `test_login_screen.py`, `test_commands.py`: all tests,
  no `size=`.

**Why it matters:** at the default zero/stub size, Textual does not compute a
real layout. Panel widths are reported as 0. The three-panel collapse tests in
`test_main_screen.py` test 4 assert `message.region.width >= baseline_width`
where `baseline_width` is 0, so the assertion is trivially true even if the
layout is broken. A panel that would render at 0 px in a real terminal never
triggers the layout bug.

**Required change:** pass `size=(120, 40)` to every `run_test()` call in
`tests/tui/`. This is the minimum terminal size at which the three-panel layout
renders non-zero column widths. Tests that explicitly test collapse can use a
smaller size if they assert on the collapsed state only.

---

### Gap 2 — `@pytest.mark.asyncio` on individual tests despite `asyncio_mode = "auto"`

**Affected files:** `test_main_screen.py` (all 12 tests), `test_room_list.py`
(all 29 tests), `test_message_view.py` (all 22 tests), `test_sync_integration.py`
(all 12 tests), `test_member_list.py`, `test_login_screen.py`, `test_commands.py`,
`test_log_panel.py`, `test_login_sso.py`.

**Evidence:** every test in those files starts with `@pytest.mark.asyncio`.
`tests/matrix/` tests, `test_context_menu.py` tests 6–9, `test_thread_panel.py`,
and `test_message_search.py` are correctly written without the decorator.

**Why it matters:** the decorator is a no-op when `asyncio_mode = "auto"` is set
in `pyproject.toml`, so it does not cause failures. However it signals that the
tests were written before the project adopted the auto mode, and the same files
tend to have the other older-pattern problems (no `size=`, raw `pilot.pause()`
chains, no `message_hook`). The decorator itself is harmless but is a reliable
indicator of technical debt in the surrounding test.

**Required change:** strip `@pytest.mark.asyncio` from all async test functions
in `tests/tui/`. This is a mechanical cleanup; no behavioural change.

---

### Gap 3 — Fixed `pilot.pause()` chains instead of `wait_for_workers`

**Affected files:** `test_main_screen.py`, `test_sync_integration.py`,
`test_commands.py`, `test_room_context_menu.py`, `test_tab_context_menu.py`.

**Evidence:**
- `test_main_screen.py::test_selecting_room_opens_tab` (lines 211–215): four
  sequential `await pilot.pause()` calls.
- `test_sync_integration.py::test_rooms_appear_after_session_restore` (lines
  423–426): three sequential `await pilot.pause()` calls.
- `test_sync_integration.py::test_restore_session_rebuilds_client_for_different_homeserver`
  (lines 574–577): three sequential `await pilot.pause()` calls.
- `test_commands.py::test_cmd_logout_triggers_app_logout` (lines 347–349): two
  `await pilot.pause()` calls with a comment "Worker needs time to complete".
- `test_tab_context_menu.py`: uses `await asyncio.sleep(0.15)` as a wall-clock
  pause.
- `test_room_context_menu.py`: uses `await asyncio.sleep(0.05)` inside
  `_right_click_room()`.

**Why it matters:** fixed pause chains are flaky. On a loaded CI machine the
worker might not finish in one `pause()` cycle; on a fast developer machine they
may hide that a worker is running at all. The `wait_for_workers` helper already
exists in `tests/conftest.py` and is used correctly in `test_thread_panel.py`
and `test_message_search.py`, but those files import it and use it; the older
files do not.

`asyncio.sleep()` is worse: it introduces a wall-clock dependency that will
either be too short (flaky) or too long (slow). It is also semantically wrong:
the test should wait for the app to reach a stable state, not for a fixed number
of milliseconds.

**Required change:** replace all fixed `await pilot.pause(); await pilot.pause()`
chains and all `await asyncio.sleep(N)` calls in TUI tests with
`await wait_for_workers(app)` from `conftest.py`. The import is already available
in files that use it; add it to the older files.

---

### Gap 4 — Tests assert internal state, not user-observable behaviour

This is the most impactful gap. Current tests assert *that something exists in
the DOM* rather than *that the user can see/interact with it*. There are three
sub-patterns.

#### 4a — DOM existence without content verification

**Examples:**
- `test_main_screen.py::test_three_panels_present`: asserts `rooms.display is True`
  for three widgets. Does not verify that the panels have non-zero size, that
  they contain their expected child widgets, or that a keyboard shortcut reaches
  them.
- `test_sync_integration.py::test_new_message_appends_to_active_room` (lines
  169–173): asserts `len(rows) == 1` (one `MessageRow` in the DOM). Does not
  verify the message body is visible, that the row is scrolled into view, or that
  the sender/timestamp metadata rendered correctly.
- `test_member_list.py::test_load_room_renders_members` (lines 67–77): asserts
  `ml.member_count == 3` and that "Alice", "Bob", "Carol" appear in raw
  `label.render()` output. Does not verify the rendered text is actually in a
  visible `Label` widget (could be hidden by a parent container), or that the
  sort order is preserved.

#### 4b — Method calls bypass the event/message system

**Examples:**
- `test_message_view.py::test_load_room_renders_messages_in_order` (lines 94–95):
  calls `await view.load_room("!r:s")` directly on the widget. In a real session
  `load_room` is called by `MainScreen._open_or_focus_room` in response to a
  `RoomList.RoomSelected` message. The test bypasses the entire message-routing
  path and does not verify that the Textual message dispatch triggers `load_room`
  at all.
- `test_member_list.py::test_load_room_renders_members` (line 69): calls
  `ml.load_room("!room:s")` directly. Same issue.
- `test_commands.py` tests 4–9: call `provider.cmd_sort_alpha()` etc. directly
  on the `TelementeCommands` instance. These verify the callback logic but not
  that the command palette actually invokes the callback when the user selects it.
- `test_message_search.py::_load_messages` helper (lines 62–70): directly
  mutates `view._rendered_event_ids`, `view._msgs_by_id`, and calls
  `timeline.mount(MessageRow(msg))`, bypassing `load_room` entirely. This tests
  code paths that never execute in production.
- `test_thread_panel.py::test_command_palette_open_thread` (line 479):
  calls `screen.open_thread(...)` directly after a comment "Invoke
  cmd_open_thread directly (palette interaction is flaky in tests)".

#### 4c — No `message_hook` for cross-widget message assertion

Only `test_thread_panel.py` (tests 9, 10, 14) and `test_message_search.py`
(tests 2–6) use `message_hook=messages.append` in `run_test()`. No tests in
`test_main_screen.py`, `test_sync_integration.py`, `test_commands.py`,
`test_login_screen.py`, `test_room_list.py`, or `test_message_view.py` use it.

This means there is zero assertion coverage for whether the right Textual
messages are posted when a user action occurs. For example:
- `test_room_list.py::test_selecting_posts_roomselected` (line 244): asserts
  `len(app.selected_room_ids) == 1` by intercepting the message in the host
  app's `on_room_list_room_selected` handler. This works, but only because the
  host app has a bespoke handler. It does not confirm the message type or its
  fields via `message_hook`.
- No test in `test_main_screen.py` confirms that pressing `ctrl+b` posts any
  Textual message at all — the test only reads the display flag after the fact.

**Required changes for Gap 4:**

1. Add `message_hook=messages.append` to integration-level tests in
   `test_sync_integration.py`, `test_main_screen.py`, and `test_commands.py`
   where cross-widget message flow is the core of what is being tested.
2. In widget-level tests (`test_room_list.py`, `test_message_view.py`,
   `test_member_list.py`), replace direct method calls (`view.load_room()`,
   `ml.load_room()`) with the message-driven path where feasible: post the
   appropriate triggering message and assert that the widget reacted.
3. Supplement DOM-existence assertions with content assertions: after asserting
   a widget has `display is True`, also assert that a sentinel string from the
   expected content is present in `str(widget.render())` or in the rendered
   text of a meaningful child.

---

### Gap 5 — Focus state never asserted before keybinding tests

**Affected tests:** every test that presses a key without first asserting focus.

**Examples:**
- `test_main_screen.py::test_toggle_rooms_hides_and_shows` (line 89): presses
  `ctrl+b` without asserting which widget has focus. If a search `Input` has
  focus and consumes the event, the keybinding silently fails but the test still
  passes (the display flag stays `True`, which matches the pre-toggle state).
- `test_message_view.py::test_G_key_scrolls_to_bottom` (lines 602–608): focuses
  `view` via `view.focus()` then asserts scroll position. Does not assert
  `app.focused.__class__.__name__ == "MessageView"` before pressing `G`, so if
  focus lands on a child widget that consumes `G`, the scroll never happens.
- `test_message_view.py::test_react_binding_sends_reaction` (lines 495–518):
  calls `row.focus()` and then `await pilot.press("e")`. Does not assert focus
  before the press, so an intermediate `await pilot.pause()` that shifts focus
  would silently break the test.
- `test_room_list.py::test_selecting_posts_roomselected` (lines 238–244):
  focuses `OptionList` and presses `down`/`enter`. Does not assert focus is on
  the `OptionList` before sending keys.

**Why it matters:** this is explicitly documented in `docs/ux-testing-strategy.md`
as a known blind spot. The classic failure mode is "key binding silently eaten by
a focused Input". Tests that do not assert focus before pressing keys cannot
detect this class of regression.

**Required change:** add `assert app.focused.__class__.__name__ == "ExpectedClass"`
immediately before any `await pilot.press(key)` call in tests where focus is
required for the keybinding to reach the target widget.

---

### Gap 6 — Panel layout not exercised at integration level

**Affected file:** `test_main_screen.py`.

Test 4 (`test_center_always_visible`) computes a `baseline_width` of 0 (because
`run_test()` has no `size=`) and then asserts `message.region.width >= 0` after
collapsing both side panels — a tautology. This test currently provides zero
coverage for the actual layout.

Test 5 (`test_focus_search_binding`) presses `ctrl+k` and asserts
`app.focused.id == "room-search"`, but does so without checking that
`ctrl+k` was dispatched through the correct widget tree (at size zero, the
dispatch tree may not match production).

**Required change:** add `size=(120, 40)` to all `test_main_screen.py` tests and
replace the `baseline_width` computation in test 4 with a concrete width assertion
(e.g. `assert message.region.width >= 40` after both panels collapse).

---

### Gap 7 — Private attribute access in tests

**Examples:**
- `test_main_screen.py::test_rooms_changed_reloads_active_room_after_sync`
  (lines 549, 571): assigns to `fake.members` and `fake.messages` with
  `# type: ignore[method-assign]`. This patches instance methods on the fake,
  which is fragile and bypasses the normal FakeMatrixClient scripting API.
- `test_message_search.py::_load_messages` helper (lines 67–69): directly writes
  to `view._rendered_event_ids` and `view._msgs_by_id`.
- `test_message_search.py` and `test_thread_panel.py`: set
  `view._current_room_id` directly.
- `test_sync_integration.py::test_on_client_event_saves_rooms_to_cache` (lines
  523, 531): accesses `app._cached_user_id` and `app._room_cache` directly.
- `test_context_menu.py::test_context_menu_uses_context_menu_layer` (line 299):
  calls `screen._show_context_menu(...)` directly.

**Why it matters:** these accesses couple tests to implementation details that
can change without breaking the public contract. They also mean the test never
exercises the public path that a user's action would take, so a regression in
that path is invisible.

**Required change:**
1. Replace private attribute writes with the appropriate FakeMatrixClient
   scripting API (e.g. use `fake.members_data` + standard patching via
   `fail_next`/`block_op` instead of `fake.members = patched_members`).
2. Replace `view._current_room_id = room_id` with the proper room-selection
   message path.
3. Replace `app._cached_user_id = ...` with the public interface if one exists;
   if none exists, document that the test is a whitebox exception and explain why
   in a comment.
4. In `test_message_search.py`, replace the `_load_messages` helper with a call
   to `view.load_room(room_id)` after populating `fake.messages_data`.

---

### Gap 8 — No coverage for focus restoration after modal dismissal

**Context:** `ConfirmScreen` is pushed as a modal by `test_message_view.py`
test 19 (`test_delete_binding_removes_row`). After the modal is dismissed,
focus returns to some widget. No test asserts which widget receives focus or
that subsequent keystrokes still reach the correct widget after the modal closes.

**Also:** the emoji picker (`EmojiPickerScreen`) is pushed modally. No test
asserts that focus returns to `ComposerArea` after the picker dismisses.

**Required change:** add focus-restoration assertions to tests that involve
modal push/pop. After `await pilot.click(yes_btn)` and `await wait_for_workers(app)`,
assert `app.focused.__class__.__name__` is the expected post-modal target.

---

### Gap 9 — No visual regression (snapshot) coverage

No test in the suite uses `App.export_screenshot()` or `pytest-textual-snapshot`.
Layout regressions in `app.tcss` — wrong panel widths, missing borders, colour
changes, broken `ContextMenu` sizing — are completely invisible to the test suite.

The `test_context_menu.py::test_context_menu_menu_item_not_using_1fr` test (lines
232–256) inspects `item_static.styles.width.unit`, which is a reasonable proxy but
not a full layout assertion.

**Required change:** add a small suite of SVG snapshot tests using
`pytest-textual-snapshot` (see Layer 2 in `docs/ux-testing-strategy.md`).
Baseline snapshots should be committed for at minimum:
- The main screen at rest (three panels visible, no tab open).
- The main screen with one room tab open.
- A `ContextMenu` with items + separator.
- The `LoginScreen`.

---

### Gap 10 — No end-to-end test for the full login-to-main-screen flow

`test_sync_integration.py::test_rooms_appear_after_session_restore` tests session
restore, but there is no test that drives the full user journey: open the app,
see the login screen, type credentials, submit, verify the main screen appears
with a populated room list. The closest is `test_login_screen.py` which only
tests the `LoginScreen` in isolation and records the `LoggedIn` message.

The wiring in `TelementeApp._on_login_screen_logged_in` that calls
`start_sync_and_subscribe` and pushes `MainScreen` is never integration-tested.

**Required change:** add one test in `test_sync_integration.py` (or a new
`test_full_flow.py`) that:
1. Starts `TelementeApp` with no saved session.
2. Verifies `LoginScreen` is the initial screen.
3. Fills credentials and submits.
4. Verifies `MainScreen` is pushed.
5. Emits `RoomsChanged` via the fake and asserts rooms appear.

---

## Files to create / modify

| File | Action |
|---|---|
| `tests/conftest.py` | Add `size` fixture; expose `wait_for_workers` as a fixture (not just a bare function) so pytest can inject it |
| `tests/tui/test_main_screen.py` | Add `size=(120, 40)` to all `run_test()` calls; strip `@pytest.mark.asyncio`; add focus assertions before keybinding presses; add `message_hook` to integration tests; fix test 4 layout width assertion |
| `tests/tui/test_room_list.py` | Add `size=(120, 40)` to all `run_test()` calls; strip `@pytest.mark.asyncio`; add focus assertions before key presses in tests 6, 23, 24, C |
| `tests/tui/test_message_view.py` | Add `size=(120, 40)` to all `run_test()` calls; strip `@pytest.mark.asyncio`; add focus assertions before key presses; add `message_hook` to tests 13–20; add focus-restoration assertions after modal dismissal; replace `_rendered_text` helper with content from `str(widget.renderable)` where practical |
| `tests/tui/test_sync_integration.py` | Add `size=(120, 40)` to all `run_test()` calls; strip `@pytest.mark.asyncio`; replace triple `await pilot.pause()` chains with `await wait_for_workers(app)`; add `message_hook` to tests 1–7; add test for full login-to-main flow |
| `tests/tui/test_commands.py` | Add `size=(120, 40)`; strip `@pytest.mark.asyncio`; replace double `await pilot.pause()` with `await wait_for_workers(app)` |
| `tests/tui/test_member_list.py` | Add `size=(120, 40)`; strip `@pytest.mark.asyncio` |
| `tests/tui/test_login_screen.py` | Add `size=(120, 40)`; strip `@pytest.mark.asyncio`; add focus assertions before key presses in test 4 |
| `tests/tui/test_log_panel.py` | Add `size=(120, 40)`; strip `@pytest.mark.asyncio` |
| `tests/tui/test_login_sso.py` | Add `size=(120, 40)`; strip `@pytest.mark.asyncio` |
| `tests/tui/test_room_context_menu.py` | Replace `await asyncio.sleep(0.05)` in `_right_click_room` with `await wait_for_workers(app)` |
| `tests/tui/test_tab_context_menu.py` | Replace `await asyncio.sleep(0.15)` with `await wait_for_workers(app)` |
| `tests/tui/test_message_search.py` | Replace `_load_messages` private-attribute helper with `view.load_room(room_id)` after populating `fake.messages_data` |
| `tests/tui/test_message_context_menu.py` | Add `size=(120, 40)` |
| `tests/tui/snapshots/` | New directory; add committed SVG baselines after first `--snapshot-update` run |
| `tests/tui/test_snapshots.py` | New file; snapshot tests for main screen at rest, one tab open, ContextMenu, LoginScreen |
| `tests/README.md` | New file; top-level test documentation |
| `tests/tui/README.md` | New file; TUI-specific test documentation |

---

## Public interface changes

None to `MatrixClient` or `FakeMatrixClient`. The `wait_for_workers` function in
`tests/conftest.py` is promoted from bare async function to a pytest fixture so
tests can inject it by name, matching the Harlequin pattern:

```python
# tests/conftest.py  (addition only)
import pytest
from collections.abc import AsyncIterator
from typing import Any
from textual.app import App

@pytest.fixture
async def wait_for_workers_fixture() -> AsyncIterator[Any]:
    """Inject wait_for_workers as a fixture. Usage: async def test_foo(wait_for_workers):"""
    # The existing bare function is kept for direct imports.
    yield wait_for_workers
```

Alternatively keep it as a direct import; either approach is acceptable. The
bare function in `conftest.py` is already importable — the only required change
is that test files that currently use `await asyncio.sleep()` or chains of
`pilot.pause()` import and call it instead.

---

## Behavior

### New test cases to add (write-first TDD)

#### `tests/tui/test_main_screen.py`

- `test_three_panels_have_nonzero_width` — run at `size=(120, 40)`;
  assert all three panel regions have `width > 0` and `height > 0`.
- `test_ctrl_b_toggles_rooms_focus_follows` — assert focus is on a non-input
  widget before pressing `ctrl+b`; press it; assert the rooms panel is hidden;
  press again; assert visible.
- `test_panel_collapse_expands_center` — at `size=(120, 40)`, record
  `message.region.width` before and after `ctrl+b`; assert the center panel
  is measurably wider after collapse (not just non-zero).
- `test_room_selection_message_flows_through_app` — use `message_hook`; select
  a room; assert `RoomList.RoomSelected` appears in the captured messages.

#### `tests/tui/test_sync_integration.py`

- `test_full_login_to_main_flow` — start with no session; verify `LoginScreen`;
  submit credentials; verify `MainScreen` pushed; emit `RoomsChanged`; assert
  rooms appear.
- `test_message_hook_captures_rooms_changed` — use `message_hook`; emit
  `RoomsChanged`; assert the exact message appears in the hook's list.

#### `tests/tui/test_message_view.py`

- `test_focus_on_composer_before_typing` — assert
  `app.focused.__class__.__name__ == "ComposerArea"` before key presses in
  tests 3, 10.
- `test_focus_restored_to_message_view_after_delete_confirm` — after
  confirming redaction via `ConfirmScreen`, assert focus returns to a
  `MessageView` child, not `None`.
- `test_focus_restored_after_emoji_picker_dismiss` — open emoji picker; dismiss
  it; assert `app.focused.__class__.__name__ == "ComposerArea"`.

#### `tests/tui/test_snapshots.py` (new)

- `test_snapshot_main_screen_at_rest` — `run_test(size=(120, 40))`; pause;
  `assert snap_compare(...)`.
- `test_snapshot_main_screen_one_tab` — open one room; `snap_compare`.
- `test_snapshot_login_screen` — `snap_compare` the login screen.
- `test_snapshot_context_menu` — open a context menu; `snap_compare`.

---

## Mocking strategy

All TUI tests continue to inject `FakeMatrixClient` via the constructor DI
seam. No change to the mocking layer is needed. `pytest-textual-snapshot` is
the only new dev dependency; it is used only in `test_snapshots.py` and is
opt-in (tests can run without it if the package is not installed, by marking
them `skipif`).

---

## Done-when

- [ ] `@pytest.mark.asyncio` removed from all async tests in `tests/tui/`.
- [ ] `size=(120, 40)` passed to every `run_test()` call in `tests/tui/`.
- [ ] All `await asyncio.sleep(N)` calls in TUI tests replaced with
  `await wait_for_workers(app)`.
- [ ] All `await pilot.pause(); await pilot.pause()` chains (two or more)
  replaced with `await wait_for_workers(app)`.
- [ ] Focus assertions (`assert app.focused.__class__.__name__ == "..."`)
  added before every `await pilot.press(key)` in a test that requires a
  specific widget to have focus for the binding to reach it.
- [ ] `message_hook=messages.append` added to at minimum the tests listed in
  §Test cases (new tests for main_screen and sync_integration).
- [ ] `_load_messages` private-attribute helper in `test_message_search.py`
  replaced with public-path equivalent.
- [ ] Private attribute assignments in test files replaced with public API
  calls where a public API exists.
- [ ] Four new snapshot baselines committed to `tests/tui/snapshots/`.
- [ ] `test_full_login_to_main_flow` passes.
- [ ] `tests/README.md` and `tests/tui/README.md` created.
- [ ] `uv run ruff check .` clean.
- [ ] `uv run mypy` clean.
- [ ] `pyright src/` clean.
- [ ] `uv run pytest` all green (no regressions from refactor).
