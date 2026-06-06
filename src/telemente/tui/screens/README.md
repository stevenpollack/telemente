# `src/telemente/tui/screens/` — full-screen views

Screens are the top-level composable units in a Textual application. Each
screen in this directory occupies the full terminal and is pushed onto the
`App.screen_stack` via `app.push_screen()`.

## Purpose

Implement distinct interaction modes: unauthenticated login flow (`LoginScreen`)
and the authenticated three-panel main interface (`MainScreen`). A modal emoji
picker (`EmojiPickerScreen`) is technically a `ModalScreen` but lives here
because it is an application-level concern (reactions), not a reusable widget.

## Key design decisions

**`LoginScreen` takes a `client_factory`, not a client.** The homeserver URL is
not known until the user types it. A factory `(homeserver: str) -> _LoginClient`
is injected at construction time; the actual client is built lazily after flow
detection resolves the homeserver. The `_LoginClient` protocol lists only the
four methods the screen needs, keeping the seam narrow.

**Login state machine.** Flow detection, password login, and SSO login are
three separate `@work(exclusive=True)` workers. The `exclusive=True` flag
means a second attempt cancels the first — no double-submit. UI controls are
disabled while a worker is running and re-enabled on success or failure via
`_on_login_success` / `_on_login_failure`.

**SSO races loopback server against manual paste.** `asyncio.wait(...,
return_when=FIRST_COMPLETED)` races a future from `SsoCallbackServer` against
a `_manual_token_future` that the "Confirm token" button resolves. Whichever
resolves first wins. This handles environments where the browser is on a
different machine (SSH tunnels) without a separate code path.

**`LoginScreen` posts `LoggedIn` and exits.** The screen never persists the
session or navigates — those are `TelementeApp` responsibilities. The `session`
object is carried in the message. This keeps the screen stateless with respect
to application-level concerns.

**`MainScreen` uses a `_MainClient` protocol** rather than depending on the
concrete `MatrixClient`. The protocol names exactly the methods `MainScreen`
calls, so `FakeMatrixClient` satisfies it for tests.

**Tab management is LRU with a cap of 8.** `open_tabs` is an `OrderedDict`
(room_id → display_name); `move_to_end` promotes on re-selection, and
`next(iter(...))` evicts the oldest when the cap is exceeded. Eviction and
addition are serialised inside one exclusive worker so `remove_pane` always
completes before `add_pane`.

**Context menus are mounted as floating widgets.** `MainScreen._show_context_menu`
mounts a `ContextMenu` widget at clamped screen coordinates on a dedicated
`LAYERS = ("context-menu",)` layer. Only one menu exists at a time;
`_dismiss_context_menu` is called on any outside click (`on_click`) and when
the menu posts `Dismissed`. Coordinates are clamped to prevent overflow off
the terminal edge.

**Right-click on a `Tab` is caught by `on_mouse_down`.** Textual's `Tab` widget
consumes `Click` events before they bubble. `on_mouse_down` fires first and
inspects `event.button == 3`; it then resolves the widget at those coordinates
via `get_widget_at` (necessary because `event.widget` can be `None` in the xterm
parser path) and walks the parent chain to find a `Tab`.

## File map

| File | Role |
|------|------|
| `login.py` | `LoginScreen` — homeserver flow detection, password login, SSO (loopback + manual paste) |
| `main.py` | `MainScreen` — three-panel layout, LRU tab manager, client-event routing, context menus |
| `emoji_picker.py` | `EmojiPickerScreen` — searchable emoji grid with skin-tone modifier support; dismisses with selected emoji |
| `__init__.py` | Package docstring only |

## Patterns used

- `@work(exclusive=True)` for login workers — serialises attempts, cancels stale ones.
- `reactive()` booleans for panel visibility (`rooms_visible`, `members_visible`,
  `log_visible`, `thread_visible`) with `watch_*` methods that toggle `.display`.
- `ModalScreen[str]` for `EmojiPickerScreen` — `dismiss(value)` returns the
  emoji to the calling screen's callback.
- `ModalScreen[bool]` for `ConfirmScreen` (in `widgets/`) — same pattern for
  destructive actions.
- `OrderedDict` for LRU tab ordering.
- `asyncio.wait(FIRST_COMPLETED)` for the SSO race.

## What lives elsewhere

- The `ConfirmScreen` modal → `tui/widgets/confirm_screen.py` (extracted to
  avoid circular imports between `main.py` and `commands.py`)
- `ContextMenu` widget → `tui/widgets/context_menu.py`
- Command palette → `tui/commands.py`
- All panel widgets (RoomList, MessageView, MemberList, etc.) → `tui/widgets/`
