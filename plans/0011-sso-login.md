# 0011 — SSO Login (with dynamic flow detection)

## Goal

The current login path (plan 0004 + `MatrixClient.login`) only supports
`m.login.password`. Many homeservers require **SSO** (`m.login.sso`): the user
authenticates through an external identity provider in a browser, the homeserver
hands back a single-use `loginToken`, and the client exchanges it via
`m.login.token`. This plan adds SSO while **keeping password login**, by
**detecting the homeserver's supported flows** and presenting the right
controls. Because telemente is a TUI (often run over SSH), SSO must work both
via an automatic **loopback browser** flow and a **manual token-paste**
fallback.

It also fixes a latent bug in the existing password login (see "Bug fix" below).

## Dependencies

- 0003 (`MatrixClient`), 0004 (`LoginScreen`), 0002 (`Session`).
- Independent of 0005–0010 (UI/sync/e2ee unaffected; this only changes auth +
  the login screen). After login completes, everything downstream is identical.

## Decisions (locked with the user)

- **Homeserver**: user-entered; detect flows dynamically via `GET /login`.
- **SSO callback**: support **both** loopback-browser and manual-paste, with the
  manual path as automatic fallback when no browser is available (e.g. SSH).
- **Password**: keep it; show whichever flows the homeserver advertises.

## Background — the Matrix SSO flow

1. `GET {hs}/_matrix/client/v3/login` → lists flows. SSO is advertised as
   `{"type": "m.login.sso"}`, optionally with an `identity_providers` array
   (`[{id, name, icon?}]`). `m.login.token` is also advertised (needed for the
   exchange).
2. Client picks a loopback `redirectUrl` (e.g. `http://localhost:PORT/<nonce>`),
   opens the browser to
   `GET {hs}/_matrix/client/v3/login/sso/redirect[/{idp_id}]?redirectUrl=<enc>`.
3. After IdP auth, the homeserver redirects the browser to `redirectUrl` with a
   `?loginToken=<token>` query param.
4. Client exchanges: `POST /login` `{"type":"m.login.token","token":<token>,
   "device_id"/"initial_device_display_name":...}` → `access_token`, `device_id`,
   `user_id`.

`matrix-nio` supports the exchange: `AsyncClient.login(token=<token>,
device_name=...)` sends `m.login.token`. `AsyncClient.login_info()` returns
advertised flow types. IdP details (`identity_providers`) are **not** reliably
surfaced by nio, so parse the raw `GET /login` JSON ourselves.

## Files to create / modify

- `src/telemente/matrix/auth.py` — **new**: `LoginFlows`, `IdentityProvider`
  dataclasses; pure helpers (`build_sso_redirect_url`, `parse_login_flows`).
- `src/telemente/matrix/sso.py` — **new**: `SsoCallbackServer` (loopback
  aiohttp server that captures `loginToken`).
- `src/telemente/matrix/client.py` — **modify**: add `login_flows()`,
  `login_with_token()`, `sso_redirect_url()`; refactor a shared
  `_finalize_login(response)`; **fix** the password-login `user` bug.
- `src/telemente/tui/screens/login.py` — **modify**: detect flows; render
  password form and/or SSO button(s); drive the loopback + manual SSO flows.
- `tests/fakes.py` — **modify**: extend `FakeMatrixClient` with the new auth
  surface (scriptable flows / token-login).
- New tests: `tests/matrix/test_auth.py`, `tests/matrix/test_sso_server.py`,
  `tests/tui/test_login_sso.py`. Update `tests/tui/test_login_screen.py` if the
  screen's construction signature changes (see below).

## Public interface

```python
# src/telemente/matrix/auth.py
from dataclasses import dataclass, field

@dataclass(frozen=True, slots=True)
class IdentityProvider:
    id: str
    name: str
    icon: str | None = None

@dataclass(frozen=True, slots=True)
class LoginFlows:
    password: bool
    sso: bool
    token: bool                                   # m.login.token (needed for SSO exchange)
    identity_providers: list[IdentityProvider] = field(default_factory=list)

def parse_login_flows(payload: dict) -> LoginFlows: ...
def build_sso_redirect_url(
    homeserver: str, redirect_url: str, idp_id: str | None = None
) -> str: ...   # urlencodes redirect_url; appends /{idp_id} when given
```

```python
# src/telemente/matrix/sso.py
class SsoCallbackServer:
    """Loopback HTTP server that captures the SSO loginToken redirect.

    Binds 127.0.0.1 on an ephemeral port. The redirect path includes a random
    nonce; only a request to that exact path resolves the token (prevents a
    stray browser tab from completing the flow).
    """
    def __init__(self) -> None: ...
    async def start(self) -> str: ...                 # returns redirect_url incl. nonce path
    async def wait_for_token(self, timeout: float = 300.0) -> str: ...  # raises SsoTimeoutError
    async def stop(self) -> None: ...

class SsoError(Exception): ...
class SsoTimeoutError(SsoError): ...
```

```python
# src/telemente/matrix/client.py  (additions)
class MatrixClient:
    async def login_flows(self) -> LoginFlows: ...
    def sso_redirect_url(self, redirect_url: str, idp_id: str | None = None) -> str: ...
    async def login_with_token(self, token: str) -> Session: ...
    # existing login()/restore() refactored to share _finalize_login(response)
```

## Behavior

### MatrixClient
- **`login_flows()`**: `GET {homeserver}/_matrix/client/v3/login` (use the nio
  client's existing aiohttp session if convenient, else a plain `aiohttp` GET),
  `parse_login_flows(json)`. Raise `LoginError` on transport/HTTP error.
- **`sso_redirect_url(redirect_url, idp_id)`**: delegate to
  `build_sso_redirect_url(self._homeserver, redirect_url, idp_id)`.
- **`login_with_token(token)`**: `resp = await self._client.login(token=token,
  device_name=self._device_name)`; on `nio.LoginError` raise `LoginError`; else
  `_finalize_login(resp)` → `Session`.
- **`_finalize_login(resp)`**: the shared tail currently inlined in `login()`
  (build `Session`, set `_logged_in`, `_load_store()`, `_register_callbacks()`).
  Refactor `login()` and `login_with_token()` to both call it.
- **Bug fix (password login)**: `login()` currently calls
  `self._client.login(password, ...)` but never sets the user identifier, so nio
  has no `user` to authenticate. Set `self._client.user = user` before the call
  (nio's `login` uses `self.user`). Add a regression test asserting the user
  reaches nio.

### SsoCallbackServer
- `start()`: create `aiohttp.web.Application` with a route `GET /{nonce}` (nonce
  from `secrets.token_urlsafe`); `AppRunner` + `TCPSite("127.0.0.1", 0)`; read
  the bound port from the server sockets; return `http://localhost:{port}/{nonce}`.
- The handler reads `request.query.get("loginToken")`; if present, resolve an
  internal `asyncio.Future` and return a small HTML page ("Login complete — you
  can close this tab and return to telemente."); if absent, return 400.
- `wait_for_token(timeout)`: `await asyncio.wait_for(self._future, timeout)`;
  raise `SsoTimeoutError` on timeout.
- `stop()`: clean up runner/site (idempotent).
- **No external network** — strictly loopback.

### LoginScreen
- **Construction change**: the entered homeserver determines the flows and the
  client, so inject a **factory** rather than a prebuilt client:
  `LoginScreen(client_factory: Callable[[str], _LoginClient], *,
  default_homeserver, sso_server_factory: Callable[[], SsoCallbackServer] =
  SsoCallbackServer, open_browser: Callable[[str], bool] = webbrowser.open)`.
  The `sso_server_factory` and `open_browser` seams exist for tests. The app
  provides a real factory that builds `MatrixClient(homeserver, store_path=...,
  device_name=...)`. (Update `app.py` and `tests/tui/test_login_screen.py`
  accordingly; password tests pass a factory returning the `FakeMatrixClient`.)
- **Flow detection**: on mount and whenever the homeserver field changes
  (debounced / on submit of the homeserver, or via a "Connect" button), run a
  worker: build a client for that homeserver, `await client.login_flows()`,
  then render:
  - password form (homeserver/username/password/submit) **iff** `flows.password`.
  - one "Sign in with SSO" button **iff** `flows.sso` and no IdPs; or one button
    per `IdentityProvider` (label = provider name) when IdPs are listed.
  - if neither flow is available → error: "This homeserver advertises no
    supported login method."
  - on `login_flows` failure → inline error, keep the homeserver field editable.
- **SSO (loopback) flow** (worker, on SSO button press):
  1. `server = sso_server_factory()`; `redirect_url = await server.start()`.
  2. `url = client.sso_redirect_url(redirect_url, idp_id)`.
  3. `opened = open_browser(url)`. Always also display the URL on screen
     ("Opening your browser… if it didn't open, visit: <url>"). If
     `opened is False`, **auto-switch to manual mode** (show the URL + a
     loginToken input) and still keep the loopback server waiting.
  4. `token = await server.wait_for_token(timeout=300)`.
  5. `session = await client.login_with_token(token)`; `await server.stop()`;
     `post LoggedIn(session)`.
  6. On `SsoTimeoutError` / `LoginError` / any error: `await server.stop()`,
     show inline error, re-enable controls, keep manual mode available.
- **Manual fallback (always available)**: a "Paste token manually" toggle/button
  reveals the SSO `url` (read-only) + an `Input(id="login-token")` +
  confirm button. On confirm: `session = await client.login_with_token(token)` →
  `post LoggedIn`. This path needs no loopback server, so it works over SSH where
  the homeserver redirects the browser on another machine.
- Reuse existing helpers (`_show_error`, `_set_form_enabled`, loading indicator)
  and the `@work(exclusive=True)` worker pattern already in `login.py`.

### Security notes (document in code)
- `redirectUrl` is loopback-only (`http://localhost:PORT/<nonce>`); the nonce
  path prevents an unrelated request from resolving the token.
- The `loginToken` is single-use and short-lived — exchange immediately; never
  log it.
- Some homeservers restrict allowed SSO redirect URLs; if loopback is rejected,
  the manual-paste path is the documented escape hatch.
- `.well-known` homeserver discovery (resolving a bare server name to a base
  URL) is **out of scope** here — the user enters a base URL. Note as a future
  enhancement.

## Test cases (write first)

### `tests/matrix/test_auth.py` (pure / aioresponses)
1. `test_parse_login_flows_password_and_sso` — payload with password + sso (+ 2
   identity_providers) → `LoginFlows(password=True, sso=True, token=...,
   identity_providers=[...2])` with correct ids/names.
2. `test_parse_login_flows_password_only` — only `m.login.password` →
   `sso=False`, empty IdPs.
3. `test_parse_login_flows_sso_no_idps` — `m.login.sso` without
   `identity_providers` → `sso=True`, empty IdPs.
4. `test_build_sso_redirect_url_no_idp` — correct path
   `/_matrix/client/v3/login/sso/redirect` and URL-encoded `redirectUrl`.
5. `test_build_sso_redirect_url_with_idp` — includes `/{idp_id}` segment.
6. `test_login_flows_http` (aioresponses) — stub `GET /login`; `login_flows()`
   returns the parsed flows; HTTP error → `LoginError`.
7. `test_login_with_token_success` (DI mock nio) — nio `login(token=...)`
   returns a `LoginResponse` → `Session` with token/device/user.
8. `test_login_with_token_failure` — nio returns `LoginError` → telemente
   `LoginError`.
9. `test_password_login_sets_user` (regression) — `login("@me:hs","pw")` sets the
   user identifier on the nio client before calling `login` (assert
   `nio_client.user == "@me:hs"` or that it's passed through).

### `tests/matrix/test_sso_server.py` (real loopback aiohttp, no external net)
10. `test_captures_login_token` — `start()`; GET the returned redirect_url with
    `?loginToken=abc123` (via `aiohttp.ClientSession`); `wait_for_token()`
    returns `"abc123"`; response status 200.
11. `test_missing_token_returns_400` — GET without `loginToken` → 400, future
    unresolved.
12. `test_wrong_path_ignored` — GET a different path → 404, token not resolved.
13. `test_timeout_raises` — `wait_for_token(timeout=0.1)` with no request →
    `SsoTimeoutError`. `stop()` is clean/idempotent.

### `tests/tui/test_login_sso.py` (Textual `run_test`)
14. `test_sso_button_shown_when_supported` — factory yields a `FakeMatrixClient`
    scripted with `sso=True, password=False`; after flow detection the SSO
    button exists and the password form is hidden.
15. `test_both_flows_shown` — `password=True, sso=True` → both present.
16. `test_no_supported_flow_shows_error` — `password=False, sso=False` → error
    message, no submit controls.
17. `test_sso_loopback_success` — inject a fake `SsoCallbackServer` (scriptable
    `start()`/`wait_for_token()` returning a token) + a stub `open_browser`
    returning `True`; press SSO; `await pilot.pause()`; assert
    `FakeMatrixClient.login_with_token` was called and `LoggedIn(session)` posted.
18. `test_sso_no_browser_switches_to_manual` — `open_browser` returns `False`;
    assert the URL + `#login-token` input become visible; submitting a token
    calls `login_with_token` → `LoggedIn`.
19. `test_sso_timeout_shows_error` — fake server `wait_for_token` raises
    `SsoTimeoutError`; assert inline error and controls re-enabled; server
    `stop()` called.
20. `test_idp_buttons` — flows with 2 IdPs → 2 SSO buttons; clicking one builds
    the redirect URL with that `idp_id` (assert via the fake client spy).

## Mocking strategy

- **`login_flows` / HTTP**: `aioresponses` (same shim as `tests/matrix/
  conftest.py`) stubbing `GET /_matrix/client/v3/login`.
- **`login_with_token`**: DI `AsyncMock(spec=nio.AsyncClient)`; `make_login_
  response` builder already in `tests/fakes.py`.
- **SsoCallbackServer tests**: real loopback aiohttp + `aiohttp.ClientSession`
  (127.0.0.1 only — allowed, not "real network").
- **LoginScreen tests**: never start a real server or browser. Inject a
  `FakeSsoCallbackServer` via `sso_server_factory` and a stub `open_browser`.
  Extend `FakeMatrixClient` with: `set_flows(LoginFlows)`, `login_flows()`,
  `sso_redirect_url()` (records the idp_id), and `login_with_token()` (scriptable
  success/failure) + spies. The factory passed to `LoginScreen` returns the
  fake. No nio, no libolm, no network.

## Bug fix (in scope)

Fix `MatrixClient.login` so the `user` argument is actually used as the Matrix
user identifier (set `self._client.user = user` before `await
self._client.login(password, ...)`), covered by test #9. This is currently
silently broken for real password logins.

## Done-when

- [ ] All new tests (auth, sso server, login-sso UI) pass; existing 64 tests
      still pass (update `test_login_screen.py` for the factory signature).
- [ ] Login screen detects flows and shows password and/or SSO appropriately;
      SSO works via loopback browser AND manual token paste; sensible errors and
      timeout handling; loopback server always stopped.
- [ ] `nio` stays confined to `src/telemente/matrix/`; no nio types leak; UI goes
      only through the client/auth helpers.
- [ ] Password-login `user` bug fixed and regression-tested.
- [ ] loginToken never logged; loopback + nonce documented.
- [ ] `mypy --strict` + `ruff` clean.

## Notes for sequencing

- Naming: the TOFU comment in `client.py` loosely referenced "plan 0011+" for
  interactive device verification — that is unrelated; device verification is a
  separate future plan (0012+). This plan (0011) is SSO login.
