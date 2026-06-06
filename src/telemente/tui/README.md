# `src/telemente/tui/` — Textual UI layer

This package contains the entire Textual application: the root `App` subclass,
all screens, all widgets, the command palette provider, the shared colour
utility, and the CSS file. It never imports `nio` and never touches application
config or credential storage directly — those come in via constructor injection.

## Purpose

Translate `ClientEvent` objects (arriving from `MatrixClient`) into DOM
mutations, and user gestures into `MatrixClient` calls. The UI is fully
testable without a network by injecting `FakeMatrixClient` (see
`tests/fakes.py`).

## Key design decisions

**`TelementeApp` owns the single `MatrixClient` instance.** It creates the
client, subscribes to events, starts sync as a worker, and passes the client
down to screens and widgets by constructor injection. Widgets never reach up to
`self.app` to fetch the client.

**`ClientEvent` → Textual message bridge.** `MatrixClient.subscribe` fires on
the asyncio loop with a plain Python callback. `TelementeApp._on_client_event`
wraps each `ClientEvent` in a private `_Client*` Textual message and calls
`post_message`. Handlers (`on__client_rooms_changed`, etc.) then forward to
the active `MainScreen`. This decoupling means the client emits synchronously
while Textual's message queue serialises delivery — no race conditions.

**Structural protocols at each injection point.** `LoginScreen`, `MainScreen`,
`MessageView`, `MemberList`, and `ThreadPanel` each define a private
`_*Client` protocol listing only the methods they actually call. This keeps the
compile-time surface narrow and makes fakes easy to write.

**Command palette is the canonical feature index.** `TelementeCommands` in
`commands.py` lists every user-facing action. Keybindings are shortcuts to
palette commands; they never bypass the palette. Adding a feature without a
corresponding `DiscoveryHit` violates invariant 6 in `AGENTS.md`.

**CSS lives in `styles/app.tcss`.** Widget IDs use `#kebab-case`. No inline
styles for anything that might change. `DEFAULT_CSS` on widget classes is
acceptable for layout that is intrinsic to the widget and would never need
overriding.

## File map

| File | Role |
|------|------|
| `app.py` | `TelementeApp` — owns `MatrixClient`, bridges `ClientEvent` to Textual messages, handles login/logout lifecycle |
| `commands.py` | `TelementeCommands` — command palette provider; every user-facing action lives here |
| `colors.py` | `sender_color(user_id)` — deterministic per-sender colour from a 12-colour palette |
| `screens/` | Full-screen views (see `screens/README.md`) |
| `widgets/` | Reusable sub-screen components (see `widgets/README.md`) |
| `styles/app.tcss` | Application-wide CSS |
| `__init__.py` | Package docstring only |

## Patterns used

**Workers for async protocol calls.** All `await client.*` calls happen inside
`run_worker(coro, exclusive=...)`. Room-selection workers use `exclusive=True`
to serialise concurrent loads; send/react/edit workers use `exclusive=False`
because they are independent.

**Reactive attributes for panel visibility.** `MainScreen` uses `reactive()`
for `rooms_visible`, `members_visible`, `log_visible`, and `thread_visible`.
The corresponding `watch_*` methods toggle `display` on the relevant widget.
This is the idiomatic Textual pattern for binary show/hide.

**Textual messages for cross-widget communication.** Widgets post
`WidgetClass.SomeMessage` instances; parent screens handle them with
`on_widget_class_some_message`. This keeps widgets unaware of their containers.

## What lives elsewhere

- Matrix protocol and `ClientEvent` definitions → `matrix/client.py`
- `RoomSummary`, `Message`, `Member` model types → `matrix/models.py`
- Session persistence and credential store → `telemente/config.py`
- Test fakes → `tests/fakes.py`
- TUI tests → `tests/tui/`
