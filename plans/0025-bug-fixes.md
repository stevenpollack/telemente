# Plan 0025 — Five post-0024 bug fixes

## Goal

Fix five user-visible regressions:

1. Leaving a room does not refresh the room list or close its tab.
2. The room-list right-click context menu renders far too wide.
3. Right-clicking a message tab produces no context menu.
4. Redacting your own last message removes the row entirely instead of leaving a
   `🗑️ Message deleted` tombstone in place.
5. Emoji with a skin-tone modifier cannot be picked or sent (no variants exist
   in the picker, and the base emoji has no fallback path).

Each bug is independent; they share no code changes except both Bug 2 and Bug 3
touch the context-menu surface. TDD applies: write the listed failing test(s)
first, then implement.

## Dependencies

Assumes the codebase at the state left by plans 0020–0024 (context menus,
tombstone spec 0022, thread panel 0023, in-room search 0024). No new runtime
dependencies.

---

## Bug 1 — Leave room does not refresh the list or close the tab

### Root cause

`FakeMatrixClient.leave_room` (tests/fakes.py) mutates `rooms_data` but never
emits a `RoomsChanged` event, unlike the real `MatrixClient.leave_room`
(matrix/client.py) which calls `await self._emit(RoomsChanged(rooms=self.rooms()))`.
The UI's only refresh/tab-close path is `MainScreen.handle_rooms_changed`, which
fires solely in response to a `RoomsChanged` event. `_do_leave` in
`MainScreen` (screens/main.py) awaits `leave_room` and notifies but performs no
direct list refresh or tab close — it relies entirely on the emitted
`RoomsChanged`. With the real client the emission happens, but the production
symptom indicates the UI is not reliably driven by it; the missing fake emission
also means no Tier-2 test ever catches a regression here.

The minimal, robust fix has two parts:

- Make `_do_leave` self-sufficient so the UI updates regardless of whether a
  subsequent `RoomsChanged` arrives: on success, explicitly close the tab for
  the left room and remove it from the room list locally.
- Make `FakeMatrixClient.leave_room` emit `RoomsChanged` so the event-driven
  path is also exercised in tests (parity with the real client and with the fake
  `set_room_tag`/`remove_room_tag`).

### Minimal fix

- In `MainScreen._do_leave` (screens/main.py), after a successful
  `await self._client.leave_room(room_id)`:
  - `await self.close_tab(room_id)` (already exists; no-op if not open).
  - Re-set the room list from the client's current rooms, e.g.
    `self.query_one(RoomList).set_rooms(self._client.rooms())`, OR drop the left
    room from `RoomList.all_rooms` directly. Prefer driving from
    `self._client.rooms()` since the real client already excludes left rooms via
    `_left_rooms`, and the fake removes it from `rooms_data`.
  - Keep the existing success notify.
- In `tests/fakes.py::FakeMatrixClient.leave_room`, after removing the room from
  `rooms_data`, emit `RoomsChanged(rooms=list(self.rooms_data))` (import locally
  as the tag methods already do). This keeps the fake faithful to the real
  client contract.

Keep `handle_rooms_changed`'s existing `departed` tab-closing logic intact; the
direct close in `_do_leave` and the event-driven close are idempotent
(`close_tab` is a no-op when the room is absent).

### Test cases needed (TDD)

In `tests/tui/test_room_context_menu.py` (HostApp there already wires
`RoomsChanged` → `handle_rooms_changed`):

- `test_leave_refreshes_room_list` — two rooms; open both tabs; confirm leave on
  one; assert the left room is gone from `RoomList.all_rooms`/`visible_rooms`
  and the OptionList no longer has its option.
- `test_leave_closes_tab` — open the room's tab (`on_room_list_room_selected`),
  confirm leave, assert `room_id not in screen.open_tabs` and the `TabPane` is
  removed from `TabbedContent`.
- Extend/confirm `test_leave_confirmed_calls_leave_room` still passes.

Also add a Tier-1/fake-contract assertion (in `tests/tui/` or a fakes unit test)
that `FakeMatrixClient.leave_room` emits exactly one `RoomsChanged` whose room
list excludes the left room.

### Gotchas

- `_do_leave` runs in a worker; tests must `await asyncio.sleep(...)` /
  `pilot.pause()` for it to complete (existing leave tests already do this after
  `dismiss(True)`).
- Don't double-refresh in a way that flickers: `set_rooms` already rebuilds; the
  event-driven `handle_rooms_changed` will run once more. Acceptable — both are
  cheap and idempotent. If flicker is a concern, gate the direct refresh behind
  "tab/list still shows the room".
- Verify the real `MatrixClient.leave_room` path is unchanged; this plan does not
  modify it.

---

## Bug 2 — Room-list context menu is far too wide

### Root cause

The `MenuSeparator` in the room context menu renders as a Textual `Rule`
(context_menu.py `compose`). `Rule` sets `self.expand = True` and its
`Rule.-horizontal` default CSS sets `width: 1fr`
(.venv/.../textual/widgets/_rule.py). The app.tcss `ContextMenu Rule` block only
overrides `height` and `color`, not `width`. Because `ContextMenu` is
`width: auto`, a `1fr`/`expand` child forces the menu to expand to the full
available width.

Empirically verified: a ContextMenu with two items and no separator measures 20
cells wide; the same menu with a `MenuSeparator` between the items measures 116
cells in a 120-wide terminal. The room menu is the only context menu that
includes a `MenuSeparator`, which is exactly why only the room-list menu is too
wide (tab and message menus are unaffected).

### Minimal fix

In `src/telemente/tui/styles/app.tcss`, constrain the Rule width inside the
ContextMenu. In the existing `ContextMenu Rule` block add `width: auto;` (or
`width: 100%;`). `width: auto` lets the menu size to its widest menu-item; the
separator then matches that width instead of forcing expansion. Also set the
`Rule` margin to 0 (the default `margin: 1 0` adds vertical gaps inside the
menu); optional but tidier.

No Python change is required. (If `width: auto` still misbehaves because `Rule`
forces `expand=True` regardless of CSS, fall back to replacing the
`MenuSeparator → Rule` rendering with a 1-cell `Static` divider in
`context_menu.py::compose`; prefer the CSS-only fix first.)

### Test cases needed (TDD)

In `tests/tui/test_context_menu.py`:

- `test_context_menu_with_separator_fits_content` — mount a `ContextMenu` with
  `[MenuItem("Favourite"), MenuSeparator(), MenuItem("Leave room")]` in an
  80- or 120-wide terminal; `await pilot.pause()` twice; assert
  `menu.size.width <= 30` (must not balloon to terminal width). This test fails
  today (width ~116) and passes after the fix.

Keep the existing `test_context_menu_menu_item_not_using_1fr`.

### Gotchas

- Two `pilot.pause()` calls are needed for the layout to settle before reading
  `menu.size.width` (a single pause can read a stale size).
- `Rule.expand = True` is set in `__init__`; confirm the CSS width override
  actually wins. The empirical test above is the source of truth — if it stays
  red with `width: auto`, switch to the Static-divider fallback.

---

## Bug 3 — Right-clicking a tab does nothing

### Root cause

`MainScreen.on_mouse_down` (screens/main.py) decides whether a right-click
landed on a tab by reading `event.widget` and walking `widget.parent` looking
for a `Tab`. But a raw `MouseDown` is constructed by the xterm parser with
`widget=None` (.venv/.../textual/_xterm_parser.py line 130), and neither
`Screen._forward_event` nor `Screen._on_mouse_event` sets `.widget` on a
`MouseDown` before forwarding it. The event is delivered to the widget under the
cursor via `widget._forward_event(...)` (screen.py line 1940) and then bubbles
up the DOM to `MainScreen`, but with `event.widget is None` the
`while widget is not None` loop body never executes, so `_show_tab_context_menu`
is never called.

This is why `MessageRow.on_mouse_down` works (the row is the forwarded target
and posts `self.post_message(...)` without relying on `event.widget`) while the
tab path does not. Existing `tests/tui/test_tab_context_menu.py` masks the bug:
its tests either call `screen._show_tab_context_menu(tab, ...)` directly or use
`pilot.click(tab, button=3)` wrapped in a try/except-skip plus a zero-region
skip, so the real `on_mouse_down` dispatch is never asserted.

### Minimal fix

Stop relying on `event.widget` in `MainScreen.on_mouse_down`. Resolve the widget
under the cursor from screen coordinates instead. Two viable approaches, prefer
the first:

- Use `self.get_widget_at(event.screen_x, event.screen_y)` (App/Screen API) to
  get the target widget, then walk its ancestry for a `Tab` exactly as today.
  Guard for `NoWidget`. This keeps all logic in `MainScreen`.
- Alternatively, read the option/tab identity from `event.style.meta` the way
  `RoomList.on_mouse_down` does — but tabs do not embed an option index in
  meta, so `get_widget_at` is the correct mechanism here.

After resolving the `Tab`, call the existing `event.stop()` +
`_show_tab_context_menu(tab, event.screen_x, event.screen_y)`. The rest of
`_show_tab_context_menu` (pane-id stripping, `Close tab` item) is correct and
unchanged.

Optionally clamp the menu's `screen_y` to a minimum of 2 so it does not render
under the tab bar (plan 0021 Bug 6 noted this); `_show_context_menu` already
clamps to the screen bounds, so this is cosmetic.

### Test cases needed (TDD)

In `tests/tui/test_tab_context_menu.py`:

- `test_tab_right_click_dispatch_shows_menu` — open a room tab in a terminal
  sized so the tab bar is visible (e.g. `run_test(size=(120, 40))`); construct a
  `MouseDown` with `button=3` at the tab's `region` screen coordinates and feed
  it through the real dispatch (post it to the screen / use
  `screen.on_mouse_down(event)` with a `MouseDown` whose `screen_x/screen_y`
  point at the tab, and `widget=None` to reproduce production); assert a
  `ContextMenu` appears. This test fails today (widget is None → no menu) and
  passes after switching to `get_widget_at`.
- Keep the existing direct-call tests as regression coverage; remove or tighten
  the `pilot.click(button=3)` skip guards so the dispatch path is actually
  asserted where the tab region is non-zero.

### Gotchas

- The new test must construct the `MouseDown` with `widget=None` to faithfully
  reproduce production; passing a real widget would hide the bug.
- `get_widget_at` raises `NoWidget` when the coordinate is outside any widget —
  wrap in try/except and return early.
- Tab widget IDs are prefixed by `ContentTab` (`--content-tab-…`);
  `_show_tab_context_menu` already strips that prefix — do not duplicate that
  logic in the dispatch.
- In `run_test`, tab regions can be zero-sized if the layout has not painted;
  pause until `tab.region.width > 0` or size the terminal generously.

---

## Bug 4 — Redacted message disappears instead of showing a tombstone

### Root cause

Plan 0022 specified that a redaction should replace the row body in-place with
`🗑️ Message deleted` and add the `-redacted` CSS class, via a new
`MessageView.handle_redaction(event)` method. That method was never implemented.
Instead, `MainScreen.handle_redaction` (screens/main.py) calls
`view.remove_message(event.event_id)`, which deletes the row from the DOM. The
`MessageRow.-redacted` CSS rule exists in app.tcss but is applied nowhere, and
`MessageView` has no `handle_redaction`. The local optimistic path
`MessageView._do_redact_and_remove` also calls `row.remove()`, so the user's own
just-deleted message vanishes before/instead of becoming a tombstone.

### Minimal fix

Implement the plan-0022 in-place tombstone and route to it:

- Add `MessageView.handle_redaction(event: MessageRedacted)` (message_view.py):
  if `event.room_id != self._current_room_id` return; otherwise find the
  `MessageRow` whose `message.event_id == event.event_id`, call
  `row.update_body("🗑️ Message deleted")`, `row.add_class("-redacted")`, and
  `self._msgs_by_id.pop(event.event_id, None)`. Do NOT clear
  `_rendered_event_ids` (keeps dedup so a sync echo can't re-append the
  original). If no row matches, log at debug and return. Import `MessageRedacted`
  from `telemente.matrix.client`.
- Change `MainScreen.handle_redaction` to call `view.handle_redaction(event)`
  instead of `view.remove_message(...)`.
- Change the local own-message path `MessageView._do_redact_and_remove` so that,
  on successful redact, it replaces the row in place (same `update_body` +
  `add_class("-redacted")` + `_msgs_by_id.pop`) rather than `row.remove()`.
  Rename if helpful (e.g. `_do_redact_and_tombstone`) but keep the existing
  call sites. Keep `_rendered_event_ids` populated so the redaction echo from
  sync is deduped.
- Verify the `MessageRow.-redacted` rule matches the actual widget class name
  `MessageRow` (it does) — no CSS change needed beyond confirming it stays.

Note the tombstone glyph: client.py backfill uses `\U0001f5d1️ Message deleted`
(🗑️). Use the identical string everywhere so dedup and assertions match.

### Test cases needed (TDD)

In `tests/tui/test_message_view.py`:

- `test_redaction_event_replaces_body_with_tombstone` — load a room with one
  message body `"hello"`; emit `MessageRedacted(room_id, event_id, redacted_by)`
  through a host app that routes to `view.handle_redaction`; assert
  `"🗑️ Message deleted"` is in the rendered text and `"hello"` is not, and the
  row count is unchanged (row not removed).
- `test_redaction_event_adds_redacted_css_class` — same setup; assert
  `"-redacted" in row.classes`.
- `test_redaction_for_other_room_is_ignored` — emit for a different room_id;
  assert body unchanged.
- `test_redaction_unknown_event_id_is_silent` — emit for an unknown event_id;
  assert no exception and original row intact.

Update the existing `test_delete_binding_removes_row` (and the analogous
`test_delete_confirmed_calls_redact` in `tests/tui/test_message_context_menu.py`
if it asserts removal): the own-message delete path now tombstones in place
rather than removing the row. The assertion `"delete me" not in rendered` should
become `"🗑️ Message deleted" in rendered` and the row should still exist; the
`redacted_messages` spy assertions stay.

Consider adding `FakeMatrixClient.auto_emit_redactions` (plan 0022 §4.4) so the
echo path can be tested, but it is not strictly required for the in-place
rendering tests.

### Gotchas

- `update_body` already exists and calls `_refresh_body_static`; reuse it.
- The `🗑️` glyph includes a U+FE0F variation selector; keep the exact byte
  sequence consistent between client backfill, MessageView, and test assertions
  or string `in` checks will silently fail.
- Existing test 19 currently encodes the WRONG (removal) behavior; it must be
  updated as part of this fix, not left green.
- `remove_message` may still be used elsewhere (e.g. genuine server-side removal
  semantics) — leave the method present but stop calling it from the redaction
  handler.

---

## Bug 5 — Emoji with skin-tone modifier not displayed / not selectable

### Root cause

The renderer is not at fault: a skin-tone sequence such as `🫶🏻` (U+1FAF6
U+1F3FB) survives Rich markup parsing intact and `rich.cells.cell_len` measures
it correctly (width 2), so a `Static`/reaction chip would display it. The real
cause is the data source: the emoji picker
(`src/telemente/tui/screens/emoji_picker.py`) uses a hardcoded curated
`REACTION_EMOJI` list of base emoji only — there are no skin-tone variants and no
mechanism to apply a modifier, and several bases (e.g. 🫶 heart hands) are absent
entirely. A user therefore has no path to pick a skin-toned emoji, and there is
no base-emoji fallback because the base itself may not be in the list. (No
`emoji`/grapheme library is installed; the list is the only source.)

### Minimal fix

Add a skin-tone capability to the picker rather than exploding the static list
into every base×5 combination:

- Add a skin-tone selector row to `EmojiPickerScreen` — five swatch buttons for
  the Fitzpatrick modifiers (U+1F3FB–U+1F3FF) plus a "default/none" option.
  Track the selected modifier in screen state (default: none).
- When an emoji button is pressed, if the emoji supports skin tone and a
  modifier is selected, append the modifier codepoint to the base before
  `self.dismiss(emoji)`. Maintain a small set/predicate of which curated bases
  are skin-tone-capable (hands, gestures, people), since not all emoji accept a
  modifier (faces with `_FE0F`, hearts, objects do not).
- Ensure the chosen base emoji is always returned even if no modifier applies —
  i.e. the base is the fallback (fixes the "not even the base shown" symptom for
  any newly-added bases). Add the missing common bases (heart hands 🫶, etc.) to
  `REACTION_EMOJI`.
- Confirm `MessageRow` reaction-chip rendering (`f"{emoji} {len(senders)}"` in a
  `Static`) handles the multi-codepoint string — it does; no renderer change
  needed. Add a rendering assertion test anyway to lock it in.

This keeps the data set small (bases + one modifier selector) and avoids a
combinatorial list, satisfying YAGNI/DRY.

### Test cases needed (TDD)

In `tests/tui/test_emoji_picker.py`:

- `test_skin_tone_selector_present` — open the picker; assert the skin-tone
  swatch controls exist.
- `test_skin_tone_applied_to_capable_emoji` — select a light-skin swatch, press a
  hand/gesture emoji, assert `dismiss` returns base+`\U0001F3FB` (the modifier
  codepoint is present in the result).
- `test_skin_tone_ignored_for_incapable_emoji` — select a swatch, press a face or
  heart, assert the result is the unmodified base (no stray modifier appended).
- `test_default_no_modifier_returns_base` — with no swatch selected, pressing an
  emoji returns the bare base.

In `tests/tui/test_message_view.py` (renderer lock-in):

- `test_skin_toned_reaction_renders` — append/update a reaction whose key is a
  skin-toned sequence; assert the full sequence (base + modifier) is present in
  the rendered chips text.

### Gotchas

- Not all emoji accept Fitzpatrick modifiers; appending a modifier to an
  incapable base produces a broken/garbled glyph. The capability predicate must
  be conservative (whitelist hands/gestures/people bases).
- Some curated bases already carry a U+FE0F variation selector (e.g. ✌️). Appending
  a skin tone after FE0F is invalid; either strip FE0F before applying the
  modifier or exclude FE0F-bearing entries from the skin-tone-capable set.
- The reaction `key` sent to the server is the literal string; ensure the picker
  returns exactly what should be sent (`send_reaction` passes it through).
- Keep the diff-based `_populate_grid` (plan 0021 Bug 4) — do not regress to full
  remount when adding the swatch row.

---

## Files to modify / create

| File | Bug | Action |
|---|---|---|
| `src/telemente/tui/screens/main.py` | 1, 3 | `_do_leave`: close tab + refresh list on success. `on_mouse_down`: resolve tab via `get_widget_at` instead of `event.widget`. `handle_redaction`: call `view.handle_redaction`. |
| `tests/fakes.py` | 1, (4) | `leave_room`: emit `RoomsChanged`. (Optional) `auto_emit_redactions`. |
| `src/telemente/tui/styles/app.tcss` | 2 | `ContextMenu Rule`: add `width: auto;` (and `margin: 0;`). |
| `src/telemente/tui/widgets/context_menu.py` | 2 (fallback) | Only if CSS fix insufficient: render `MenuSeparator` as a 1-cell `Static` divider. |
| `src/telemente/tui/widgets/message_view.py` | 4 | Add `handle_redaction`; change `_do_redact_and_remove` to tombstone in place; import `MessageRedacted`. |
| `src/telemente/tui/screens/emoji_picker.py` | 5 | Add skin-tone swatch row + capability predicate; add missing bases; apply modifier on pick. |
| `tests/tui/test_room_context_menu.py` | 1 | `test_leave_refreshes_room_list`, `test_leave_closes_tab`. |
| `tests/tui/test_context_menu.py` | 2 | `test_context_menu_with_separator_fits_content`. |
| `tests/tui/test_tab_context_menu.py` | 3 | `test_tab_right_click_dispatch_shows_menu`; tighten skip guards. |
| `tests/tui/test_message_view.py` | 4, 5 | Tombstone tests; update test 19; `test_skin_toned_reaction_renders`. |
| `tests/tui/test_message_context_menu.py` | 4 | Update delete-confirmed assertion to tombstone. |
| `tests/tui/test_emoji_picker.py` | 5 | Skin-tone selector tests. |

## Command palette

These fixes change behavior of existing palette/menu features only (leave room,
react, delete); no new top-level feature is introduced, so no new
`DiscoveryHit` is required. If the skin-tone picker becomes a standalone
"Insert emoji" action, add a palette entry at that time.

## Done-when checklist

- [ ] Leaving a room removes it from the room list and closes its tab in the UI;
  `FakeMatrixClient.leave_room` emits `RoomsChanged`.
- [ ] `test_leave_refreshes_room_list` and `test_leave_closes_tab` green;
  existing leave tests still green.
- [ ] Room context menu with a separator measures `<= 30` cells wide;
  `test_context_menu_with_separator_fits_content` green.
- [ ] Right-clicking a tab via the real `on_mouse_down` dispatch (with
  `event.widget=None`) shows a ContextMenu;
  `test_tab_right_click_dispatch_shows_menu` green.
- [ ] Redacting a message leaves a `🗑️ Message deleted` tombstone row with the
  `-redacted` class; the row is not removed. New tombstone tests green; test 19
  and the context-menu delete test updated and green.
- [ ] Skin-tone modifier can be selected and is appended only to capable bases;
  base is always the fallback. Emoji-picker skin-tone tests green;
  `test_skin_toned_reaction_renders` green.
- [ ] `uv run ruff check .` / `uv run ruff format .` clean.
- [ ] `uv run mypy` clean.
- [ ] `pyright src/` clean.
- [ ] `uv run pytest` all green.
