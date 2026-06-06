# TUI Test Layer

Tests in `tests/tui/` drive Textual widgets and screens with a headless
`Pilot` using `FakeMatrixClient` injected via the DI seam. There is no real
network, no matrix-nio, no threads. All async work runs on the single Textual
event loop.

---

## Golden rules

1. **`size=(120, 40)` on every `run_test()` call.** At default/zero size
   Textual does not compute a real layout. Panel widths are reported as 0,
   collapse tests are tautologies, and layout bugs are invisible. 120 × 40 is
   the minimum size at which the three-panel main layout renders non-zero column
   widths for all three panels.

2. **`await wait_for_workers(app)` instead of pause chains.** Import it from
   `conftest`:
   ```python
   from conftest import wait_for_workers
   await wait_for_workers(app)
   ```
   Never use two or more sequential `await pilot.pause()` calls to wait for a
   worker. Never use `await asyncio.sleep(N)`.

3. **Assert focus before pressing keys.** If a keybinding requires a specific
   widget to have focus, assert it before calling `pilot.press()`:
   ```python
   assert app.focused.__class__.__name__ == "ComposerArea"
   await pilot.press("enter")
   ```
   Without this assertion a focused `Input` widget can silently consume the key,
   the test still passes, and the real user's keybinding is broken.

4. **No `@pytest.mark.asyncio`.** `asyncio_mode = "auto"` is configured in
   `pyproject.toml`. The decorator is a no-op and a sign the test was written
   before auto mode was adopted — strip it.

5. **No private attribute access.** Do not write `widget._foo = ...` or read
   `app._bar` in tests. Use the public API. When no public API exists, document
   the whitebox exception with an inline comment explaining why.

6. **Drive through messages, not method calls.** The production path is:
   user action → Textual message → handler → widget update. Tests should follow
   that path:
   ```python
   # Wrong: calls the method directly, bypassing message routing
   await view.load_room("!r:s")

   # Right: post the triggering message and let the routing do its job
   room_list.post_message(RoomList.RoomSelected("!r:s"))
   await wait_for_workers(app)
   ```
   Direct method calls test the callback in isolation but never catch wiring
   regressions.

7. **Use `message_hook` for cross-widget message assertions.** When you need to
   assert that the right Textual message was posted (not just that the widget
   reached a certain state), capture the message stream:
   ```python
   messages: list[TextualMessage] = []
   async with app.run_test(size=(120, 40), message_hook=messages.append) as pilot:
       ...
       posted = [m for m in messages if isinstance(m, RoomList.RoomSelected)]
       assert len(posted) == 1
       assert posted[0].room_id == "!a:h"
   ```

---

## Host app pattern

Every test file that tests an isolated widget defines a minimal `HostApp` that
mounts only the widget under test. Tests that need the full three-panel layout
use `TelementeApp` or `MainScreen` directly.

```python
class HostApp(App[None]):
    def __init__(self, client: FakeMatrixClient) -> None:
        super().__init__()
        self._client = client

    def compose(self) -> ComposeResult:
        yield MyWidget(self._client, id="my-widget")
```

Do not mount the full `MainScreen` when you are testing a single widget — the
extra widgets slow the test and can interfere with focus.

---

## FakeMatrixClient scripting

`FakeMatrixClient` (`tests/fakes.py`) mirrors the full public surface of
`MatrixClient`. Key capabilities:

| Mechanism | How to use |
|---|---|
| Pre-load data | `fake.messages_data["!r:s"] = [...]`, `fake.rooms_data = [...]` |
| Script failures | `fake.fail_next("send_text")`, `fake.always_fail("messages")` |
| Block an op | `fake.block_op("send_text")` / `fake.unblock_op("send_text")` |
| Script `me()` | `fake.set_me("@alice:h", "Alice")` |
| Emit events | `await fake.emit(NewMessage(message=msg))` |
| Read spies | `fake.sent_messages`, `fake.sent_reactions`, `fake.left_rooms` |
| Reset spies | `fake.reset_spies()` |

**Do not monkey-patch `fake.messages` or `fake.members`.** Use `fail_next`,
`always_fail`, or `messages_data` to control what the fake returns.

---

## Emitting client events

The typical pattern for testing the UI's reaction to a Matrix event:

```python
await fake.emit(RoomsChanged(rooms=[...]))
await wait_for_workers(app)
# assert widget state
```

`await fake.emit(...)` calls all registered handlers synchronously (they are
async coroutines awaited in turn). `wait_for_workers` drains any workers those
handlers launched.

---

## Focus patterns

Focus is the most common source of "tests pass, UX broken" failures. Adopt
these patterns:

```python
# Assert focused widget class before a keybinding test
assert app.focused.__class__.__name__ == "OptionList"
await pilot.press("enter")

# After a modal is pushed and dismissed, assert focus restoration
await pilot.click(yes_btn)
await wait_for_workers(app)
assert app.focused.__class__.__name__ == "MessageView"

# Focus assertion by ID (when class name is ambiguous)
assert app.focused is not None
assert app.focused.id == "room-search"
```

---

## Typical test skeleton

```python
async def test_selecting_room_loads_messages() -> None:
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.messages_data["!r:s"] = [_msg("$e1", "hello")]
    fake.members_data["!r:s"] = []

    messages: list[TextualMessage] = []
    async with MainHostApp(fake).run_test(size=(120, 40), message_hook=messages.append) as pilot:
        await wait_for_workers(app)
        screen = app.screen
        assert isinstance(screen, MainScreen)

        # Assert initial focus
        assert app.focused.__class__.__name__ == "OptionList"

        # Drive through messages
        room_list = screen.query_one(RoomList)
        room_list.post_message(RoomList.RoomSelected("!r:s"))
        await wait_for_workers(app)

        # Assert observable outcome
        view = screen.message_view_for("!r:s")
        assert view is not None
        assert view.current_room_id == "!r:s"

        # Assert message routing (not just final state)
        selected = [m for m in messages if isinstance(m, RoomList.RoomSelected)]
        assert len(selected) == 1 and selected[0].room_id == "!r:s"
```

---

## What not to test in this tier

- **The MatrixClient internals.** Matrix protocol behaviour belongs in
  `tests/matrix/`. TUI tests assert only on widget/screen state.
- **Visual appearance.** CSS correctness, pixel-level layout, colour — these
  require snapshot tests (`pytest-textual-snapshot`). See plan 0026 for that
  layer.
- **Real network.** Never use `aioresponses` or touch a real homeserver in
  `tests/tui/`.

---

## File inventory

| File | What it covers |
|---|---|
| `test_main_screen.py` | Three-panel layout, tab management, unread, panel toggle keybindings |
| `test_sync_integration.py` | Full `TelementeApp` wired with fake; `RoomsChanged`, `NewMessage`, `MembersChanged` events; session restore |
| `test_room_list.py` | `RoomList` widget: set_rooms, filter, sort, unread badge, selection, right-click |
| `test_message_view.py` | `MessageView` + `ComposerArea`: load, send, reply, edit, redact, reactions, scrolling, typing indicator |
| `test_member_list.py` | `MemberList` widget: load, sort, count |
| `test_login_screen.py` | `LoginScreen`: password login, SSO flow, error states |
| `test_login_sso.py` | SSO redirect and token exchange |
| `test_commands.py` | Command palette: discover, search, callbacks |
| `test_context_menu.py` | `ContextMenu` widget: rendering, dismiss, layer positioning |
| `test_room_context_menu.py` | Room list right-click actions: favourite, leave, mute |
| `test_tab_context_menu.py` | Tab right-click actions: close tab |
| `test_message_context_menu.py` | Message right-click actions |
| `test_thread_panel.py` | `ThreadPanel`: load, live append, open from command, close |
| `test_message_search.py` | In-room search: open, highlight, navigate, close |
| `test_log_panel.py` | Log viewer panel |
| `test_emoji_picker.py` | Emoji picker screen |
