# Plan 0014 — Log viewer panel

## Goal

A collapsible bottom panel that tails `telemente.log` in real time.  Toggleable
via command palette and keyboard shortcut; closeable with ESC or a close button.

## Files

| File | Change |
|------|--------|
| `src/telemente/tui/widgets/log_panel.py` | New widget |
| `src/telemente/tui/screens/main.py` | Compose LogPanel, add reactive + action |
| `src/telemente/tui/commands.py` | Add "Toggle log viewer" command |
| `src/telemente/tui/styles/app.tcss` | Style the panel |
| `tests/tui/test_log_panel.py` | New test module |

## Public interface

```python
class LogPanel(Widget):
    class CloseRequested(Message): ...

    def __init__(self, log_file: Path, *, max_lines: int = 500, ...) -> None: ...
```

`MainScreen` gains:
- `log_visible: reactive[bool]` (default `False`)
- `action_toggle_log() -> None`
- Binding `ctrl+backslash` → `toggle_log` / "Log"

## Behaviour

- `LogPanel` is always mounted in `MainScreen` (`display = False` by default).
- On mount the worker opens the log file, seeks to the last 16 KB, emits those
  lines into `RichLog`, then polls for new lines every 250 ms.
- If the log file does not yet exist the worker exits cleanly; no crash.
- ESC while the panel is focused (or anywhere on `MainScreen` when the panel is
  visible) posts `LogPanel.CloseRequested`, which sets `log_visible = False`.
- The close button (top-left of the panel header) does the same.
- Command palette exposes "Toggle log viewer".

## Test cases

1. `test_log_panel_hidden_by_default` — panel `display` is `False` on mount.
2. `test_action_toggle_log_shows_panel` — after one toggle, `display` is `True`.
3. `test_action_toggle_log_hides_panel` — two toggles → back to `False`.
4. `test_close_button_posts_close_requested` — pressing ✕ fires
   `LogPanel.CloseRequested` and hides the panel.
5. `test_esc_closes_panel` — ESC key on focused panel hides it.
6. `test_panel_reads_existing_log` — pre-written file content appears in `RichLog`.
7. `test_panel_tails_new_lines` — lines written after mount appear in `RichLog`.
8. `test_missing_log_file_no_crash` — panel mounts cleanly when file absent.
9. `test_command_palette_has_toggle` — "Toggle log viewer" is discoverable.

## Mocking strategy

`LogPanel` tests use an isolated `App` wrapper and a `tmp_path` log file.
`MainScreen` toggle tests use `FakeMatrixClient` from `tests/fakes.py`.

## Done-when

- All 9 tests pass.
- `ruff`, `mypy --strict`, `pytest` all green.
- Command palette entry exists for toggle.
- Panel hidden by default; ESC and ✕ both close it.
