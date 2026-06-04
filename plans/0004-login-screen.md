# 0004 — Login Screen

## Goal

A Textual screen that collects homeserver / username / password, logs in via
`MatrixClient`, persists the session, and transitions to the main screen. Shows
inline validation, a loading state, and login errors.

## Dependencies

- 0003 (`MatrixClient`, `LoginError`, `tests/fakes.py::FakeMatrixClient`).
- 0002 (`CredentialStore`, `Settings`) — to prefill homeserver and save session.

## Files to create / modify

- `src/telemente/tui/screens/login.py` — new (`LoginScreen`).
- `src/telemente/tui/app.py` — modify: app holds a `MatrixClient` +
  `CredentialStore`; on start, if a saved session exists → restore + go to main,
  else push `LoginScreen`. (Main screen wiring lands in 0005; for now pushing a
  placeholder is fine and noted.)
- `tests/tui/test_login_screen.py` — new.

## Public interface

```python
# src/telemente/tui/screens/login.py
from textual.screen import Screen
from textual.message import Message as TextualMessage

class LoginScreen(Screen[None]):
    class LoggedIn(TextualMessage):
        def __init__(self, session: "Session") -> None: ...

    def __init__(self, client: "MatrixClient", *, default_homeserver: str) -> None: ...
```

The screen posts a `LoginScreen.LoggedIn` message (or calls an injected
callback) on success; the app handles persistence + navigation. Keep the screen
ignorant of `CredentialStore` (single responsibility, easier to test).

## Behavior / layout

- Widgets (give every interactive widget a stable `id`):
  - `Input(id="homeserver", value=default_homeserver)`
  - `Input(id="username", placeholder="@user:server or user")`
  - `Input(id="password", password=True, id="password")`
  - `Button("Log in", id="submit", variant="primary")`
  - `Static(id="error")` (hidden unless there's an error)
  - a loading indicator (e.g. `LoadingIndicator`, shown while awaiting login)
- Submit triggers on the button **and** on `Input.Submitted` (Enter in any
  field).
- **Validation**: homeserver, username, password all non-empty; otherwise show
  "All fields are required." and do not call the network.
- On submit: disable the form, show loading; `await client.login(user, pw)`
  (homeserver is set when constructing the client — if the user changed it,
  rebuild/point the client at the new homeserver; simplest: the app constructs
  `MatrixClient(homeserver)` lazily after reading the field — document this
  choice in code). On success post `LoggedIn(session)`. On `LoginError` show the
  message, re-enable the form, keep focus.
- Use Textual `@work(exclusive=True)` worker (or `run_worker`) for the await so
  the UI stays responsive; never block `on_button_pressed`.

## Test cases (write first)

`tests/tui/test_login_screen.py` (drive via a host `App` + `run_test`):

1. `test_successful_login_posts_loggedin` — inject a `FakeMatrixClient`
   configured to return a `Session`; set inputs; press submit; `await
   pilot.pause()`; assert a `LoggedIn` message was posted (capture via a tiny
   host app that records it) and the session matches.
2. `test_failed_login_shows_error` — `FakeMatrixClient.login` raises
   `LoginError("bad creds")`; submit; assert `#error` is visible with that text
   and the screen is still `LoginScreen` (no navigation).
3. `test_empty_fields_blocks_network` — leave password empty; submit; assert the
   error shows "All fields are required." and `FakeMatrixClient.login` was
   **not** called (spy/flag on the fake).
4. `test_enter_in_password_submits` — fill fields; `pilot.press("enter")` while
   password focused; same success path as test 1.
5. `test_loading_state` — make the fake's `login` await an event you control;
   after submit assert the form is disabled / loading indicator present; release
   → success.

## Mocking strategy

- No network: inject `FakeMatrixClient` (from `tests/fakes.py`). Add to the fake
  a `login` that can be scripted to succeed (return Session), fail (raise), or
  block (await an `asyncio.Event`) and a `login_called` flag.
- Use a minimal host `App` in the test that pushes `LoginScreen` and records
  posted `LoggedIn` messages via an `on_login_screen_logged_in` handler.
- Interact with `pilot.press`, set `Input.value` directly, `await pilot.pause()`
  between actions for messages/workers to settle.

## Done-when

- [ ] All 5 tests pass; no real network.
- [ ] Enter and the button both submit; validation blocks empty submits.
- [ ] Errors render inline; success posts `LoggedIn(session)`.
- [ ] App restores a saved session on launch (skips login) — covered by a small
      app-level test or noted for 0005 integration.
- [ ] `mypy --strict` + `ruff` clean.
