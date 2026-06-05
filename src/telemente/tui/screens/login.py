"""Login screen for telemente (plan 0004 / plan 0011).

Detects homeserver login flows (password, SSO) and presents the appropriate
controls. Supports both loopback-browser SSO and manual loginToken paste.

Design:
- Takes a ``client_factory`` (not a pre-built client) so the client can be
  constructed for the user-entered homeserver.
- Seams ``sso_server_factory`` and ``open_browser`` allow tests to inject
  fakes without a real browser or network.
- The app (not this screen) persists the session and navigates after login.

Security notes (plan 0011):
- The SSO redirectUrl is always loopback ``http://localhost:PORT/<nonce>``.
- The loginToken is NEVER logged.
- Manual-paste fallback works over SSH where the browser is on another machine.
- Homeserver values (MXID, bare name, or URL) are resolved via ``matrix.discovery``.
"""

from __future__ import annotations

import asyncio
import logging
import webbrowser
from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar, Protocol

from textual import work
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.message import Message as TextualMessage
from textual.screen import Screen
from textual.widgets import Button, Input, LoadingIndicator, Static
from textual.worker import Worker, WorkerState

from telemente.config import Session
from telemente.matrix.auth import LoginFlows
from telemente.matrix.discovery import (
    DiscoveryError,
    resolve_homeserver,
    server_name_from_mxid,
    server_name_matches_resolved_url,
)
from telemente.matrix.sso import SsoCallbackServer, SsoTimeoutError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol: the subset of MatrixClient used by LoginScreen
# ---------------------------------------------------------------------------


class _LoginClient(Protocol):
    """Structural protocol for the subset of MatrixClient used by LoginScreen.

    Covers password login, SSO redirect URL building, token exchange, and
    flow detection.
    """

    async def login(self, user: str, password: str) -> Session: ...

    async def login_flows(self) -> LoginFlows: ...

    def sso_redirect_url(self, redirect_url: str, idp_id: str | None = None) -> str: ...

    async def login_with_token(self, token: str) -> Session: ...


# ---------------------------------------------------------------------------
# LoginScreen
# ---------------------------------------------------------------------------


class LoginScreen(Screen[None]):
    """Collects credentials and logs in via the injected client factory.

    Construction
    ------------
    ``client_factory(homeserver: str) -> _LoginClient``
        Called with the entered homeserver to build a client for that server.
    ``sso_server_factory``
        Factory for the loopback SSO callback server (default: SsoCallbackServer).
    ``open_browser``
        Callable that opens a URL in the browser (default: webbrowser.open).
        Must return ``bool``; if it returns ``False``, the screen auto-switches
        to manual token-paste mode.
    """

    BINDINGS: ClassVar[list[BindingType]] = []

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    class LoggedIn(TextualMessage):
        """Posted on successful login. The app handles persistence + navigation."""

        def __init__(self, session: Session) -> None:
            super().__init__()
            self.session = session

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def __init__(
        self,
        client_factory: Callable[[str], _LoginClient],
        *,
        default_homeserver: str = "https://matrix.org",
        sso_server_factory: Callable[[], SsoCallbackServer] = SsoCallbackServer,
        open_browser: Callable[[str], bool] = webbrowser.open,
    ) -> None:
        super().__init__()
        self._client_factory = client_factory
        self._default_homeserver = default_homeserver
        self._sso_server_factory = sso_server_factory
        self._open_browser = open_browser

        # Set after flow detection
        self._client: _LoginClient | None = None
        self._flows: LoginFlows | None = None
        self._active_homeserver: str | None = None
        # SSO redirect URL displayed to the user (read-only)
        self._sso_url: str = ""
        # Shared future: set by the manual-confirm button to deliver a token
        # to the running SSO worker.  Reset on each new SSO attempt.
        self._manual_token_future: asyncio.Future[str] | None = None

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("telemente — Log In", id="login-title")

        # Homeserver field (always visible)
        yield Input(
            value=self._default_homeserver,
            placeholder="https://matrix.org",
            id="homeserver",
        )

        # Password form (shown iff flows.password)
        yield Input(
            placeholder="@user:server or user",
            id="username",
        )
        yield Input(
            placeholder="password",
            password=True,
            id="password",
        )
        yield Button("Log in", id="submit", variant="primary")

        # SSO area: dynamically populated via _render_sso_buttons()
        yield Static("", id="sso-area")

        # SSO URL display (shown after browser open attempt)
        yield Static("", id="sso-url-display")

        # Manual token paste area (shown when browser unavailable or requested)
        yield Input(placeholder="Paste loginToken here", id="login-token")
        yield Button("Confirm token", id="token-confirm", variant="primary")

        # Status / error
        yield Static("", id="error")
        yield LoadingIndicator(id="loading")

    def on_mount(self) -> None:
        logger.info("LoginScreen mounted homeserver=%s", self._default_homeserver)
        # Hide dynamic widgets initially
        self.query_one("#loading", LoadingIndicator).display = False
        self.query_one("#error", Static).display = False
        self._hide_password_form()
        self._hide_sso_manual()
        self.query_one("#sso-url-display", Static).display = False

        # Kick off flow detection for the default homeserver
        self._detect_flows()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "submit":
            self._attempt_login()
        elif btn_id == "token-confirm":
            self._confirm_manual_token()
        elif btn_id is not None and btn_id.startswith("sso-btn-"):
            # Extract idp_id from button id: "sso-btn-<idp_id>" or "sso-btn-__default__"
            raw = btn_id[len("sso-btn-") :]
            idp_id: str | None = None if raw == "__default__" else raw
            self._attempt_sso_login(idp_id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        _ = event
        if event.input.id in ("username", "password"):
            self._attempt_login()
        elif event.input.id == "homeserver":
            self._detect_flows()

    # ------------------------------------------------------------------
    # Flow detection
    # ------------------------------------------------------------------

    def _detect_flows(self) -> None:
        homeserver = self.query_one("#homeserver", Input).value.strip()
        if not homeserver:
            return
        self._set_homeserver_enabled(False)
        self.query_one("#loading", LoadingIndicator).display = True
        self._clear_error()
        self._do_detect_flows(homeserver)

    async def _resolve_effective_homeserver(self, username: str | None = None) -> str:
        """Resolve homeserver from the field and optional MXID username."""
        hs_raw = self.query_one("#homeserver", Input).value.strip()
        user_raw = (username or "").strip()
        mxid_server = server_name_from_mxid(user_raw)

        if mxid_server:
            if not hs_raw:
                return await resolve_homeserver(user_raw)
            from_field = await resolve_homeserver(hs_raw)
            if not server_name_matches_resolved_url(mxid_server, from_field):
                raise DiscoveryError("Homeserver does not match Matrix ID.")
            return from_field

        if not hs_raw:
            raise DiscoveryError("Homeserver is required.")
        return await resolve_homeserver(hs_raw)

    async def _ensure_client_for_current_homeserver(
        self,
        username: str | None = None,
        *,
        update_ui: bool = True,
    ) -> bool:
        """Ensure ``self._client`` matches the current homeserver field (and MXID)."""
        try:
            resolved = await self._resolve_effective_homeserver(username)
        except DiscoveryError as exc:
            self._on_login_failure(str(exc))
            return False

        if resolved == self._active_homeserver and self._client is not None:
            return True

        if not await self._rebuild_client_for_resolved(resolved, update_ui=update_ui):
            self._on_login_failure("Could not connect to homeserver.")
            return False
        return True

    async def _rebuild_client_for_resolved(self, resolved: str, *, update_ui: bool) -> bool:
        """Fetch login flows and attach a client for ``resolved``."""
        from telemente.matrix.client import LoginError

        try:
            client = self._client_factory(resolved)
            flows = await client.login_flows()
        except LoginError as exc:
            logger.warning("login_flows failed: %s", exc)
            if update_ui:
                self._on_flows_failure(str(exc))
            else:
                self._on_login_failure(str(exc))
            return False
        except Exception as exc:
            logger.exception("Unexpected error fetching login flows: %s", exc)
            message = f"Could not reach homeserver: {exc}"
            if update_ui:
                self._on_flows_failure(message)
            else:
                self._on_login_failure(message)
            return False

        self._client = client
        self._flows = flows
        self._active_homeserver = resolved
        self.query_one("#homeserver", Input).value = resolved
        if update_ui:
            self._on_flows_detected(flows)
        return True

    async def _detect_flows_for_resolved(self, resolved: str) -> None:
        """Fetch login flows for a resolved homeserver base URL and refresh the UI."""
        await self._rebuild_client_for_resolved(resolved, update_ui=True)

    @work(exclusive=True, exit_on_error=False)
    async def _do_detect_flows(self, raw_homeserver: str) -> None:
        try:
            resolved = await resolve_homeserver(raw_homeserver)
        except DiscoveryError as exc:
            self._on_flows_failure(str(exc))
            return

        await self._detect_flows_for_resolved(resolved)

    def _on_flows_detected(self, flows: LoginFlows) -> None:
        self.query_one("#loading", LoadingIndicator).display = False
        self._set_homeserver_enabled(True)

        if not flows.password and not flows.sso:
            self._show_error("This homeserver advertises no supported login method.")
            self._hide_password_form()
            self._hide_sso_area()
            return

        self._clear_error()

        # Password form
        if flows.password:
            self._show_password_form()
        else:
            self._hide_password_form()

        # SSO buttons
        if flows.sso:
            self._render_sso_buttons(flows)
        else:
            self._hide_sso_area()

    def _on_flows_failure(self, message: str) -> None:
        self._active_homeserver = None
        self._client = None
        self._flows = None
        self.query_one("#loading", LoadingIndicator).display = False
        self._set_homeserver_enabled(True)
        self._show_error(message)

    # ------------------------------------------------------------------
    # Password login
    # ------------------------------------------------------------------

    def _attempt_login(self) -> None:
        homeserver = self.query_one("#homeserver", Input).value.strip()
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value.strip()

        if not homeserver or not username or not password:
            self._show_error("All fields are required.")
            return

        logger.info("_attempt_login: user=%s homeserver=%s", username, homeserver)
        self._clear_error()
        self._set_form_enabled(False)
        self.query_one("#loading", LoadingIndicator).display = True

        self._do_login(username, password)

    @work(exclusive=True, exit_on_error=False)
    async def _do_login(self, username: str, password: str) -> None:
        from telemente.matrix.client import LoginError

        if not await self._ensure_client_for_current_homeserver(username):
            return

        client = self._client
        assert client is not None

        try:
            session = await client.login(username, password)
        except LoginError as exc:
            logger.info("Login failed: %s", exc)
            self._on_login_failure(str(exc))
            return
        except Exception as exc:
            logger.exception("Unexpected error during login: %s", exc)
            self._on_login_failure(f"Unexpected error: {exc}")
            return

        self._on_login_success(session)

    # ------------------------------------------------------------------
    # SSO login (loopback + manual fallback)
    # ------------------------------------------------------------------

    def _attempt_sso_login(self, idp_id: str | None) -> None:
        self._clear_error()
        self._set_all_controls_enabled(False)
        self.query_one("#loading", LoadingIndicator).display = True
        self._do_sso_login(idp_id)

    @work(exclusive=True, exit_on_error=False)
    async def _do_sso_login(self, idp_id: str | None) -> None:
        if not await self._ensure_client_for_current_homeserver(update_ui=False):
            return

        client = self._client
        assert client is not None
        if self._flows is not None and not self._flows.sso:
            self._on_login_failure("This homeserver does not support SSO.")
            return

        # Create a fresh future for manual-token delivery on this SSO attempt
        self._manual_token_future = asyncio.get_event_loop().create_future()

        server = self._sso_server_factory()
        try:
            redirect_url = await server.start()
            sso_url = client.sso_redirect_url(redirect_url, idp_id)
            self._sso_url = sso_url

            # Show the URL on screen regardless of browser availability
            url_display = self.query_one("#sso-url-display", Static)
            url_display.update(f"Opening your browser… if it didn't open, visit:\n{sso_url}")
            url_display.display = True

            opened = self._open_browser(sso_url)

            if not opened:
                # Auto-switch to manual mode: show token input + re-enable it
                self._show_sso_manual()
                # Re-enable the manual controls so user can interact
                ti = self.query_one("#login-token", Input)
                ti.disabled = False
                confirm = self.query_one("#token-confirm", Button)
                confirm.disabled = False

            # Wait for a token from either the loopback server OR manual input.
            # We race both sources: whichever resolves first wins.
            loop = asyncio.get_event_loop()
            server_future: asyncio.Future[str] = loop.create_future()

            async def _run_server_wait() -> None:
                try:
                    result = await server.wait_for_token(timeout=300.0)
                    if not server_future.done():
                        server_future.set_result(result)
                except SsoTimeoutError as exc:
                    if not server_future.done():
                        server_future.set_exception(exc)
                except Exception as exc:
                    if not server_future.done():
                        server_future.set_exception(exc)

            server_task = asyncio.create_task(_run_server_wait())

            # Race: loopback server vs manual paste.
            # Wrap both in object-typed futures so asyncio.wait is homogeneous.
            manual_future = self._manual_token_future
            # Shield the manual future to allow cancellation of the wrapper
            # without cancelling the underlying future.
            manual_shielded = asyncio.ensure_future(asyncio.shield(manual_future))
            # Wrap server_task as an object-typed awaitable for asyncio.wait
            races: list[asyncio.Future[object]] = [
                server_task,  # type: ignore[list-item]
                manual_shielded,  # type: ignore[list-item]
            ]
            try:
                _done, _pending = await asyncio.wait(
                    races,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                server_task.cancel()
                manual_shielded.cancel()

            token: str
            if manual_future.done() and not manual_future.exception():
                token = manual_future.result()
            elif server_future.done() and not server_future.exception():
                token = server_future.result()
            else:
                # Both failed or the server timed out
                exc_val: BaseException | None = None
                if server_future.done():
                    exc_val = server_future.exception()
                raise exc_val or SsoTimeoutError("SSO timed out")

        except SsoTimeoutError as exc:
            logger.warning("SSO timed out: %s", exc)
            await server.stop()
            self._on_login_failure("SSO login timed out. Please try again.")
            return
        except Exception as exc:
            logger.exception("Unexpected error during SSO login: %s", exc)
            await server.stop()
            self._on_login_failure(f"SSO error: {exc}")
            return

        try:
            session = await client.login_with_token(token)
        except Exception as exc:
            logger.warning("Token exchange failed: %s", exc)
            await server.stop()
            self._on_login_failure(f"Token exchange failed: {exc}")
            return

        await server.stop()
        self._on_login_success(session)

    def _confirm_manual_token(self) -> None:
        """Deliver a manually-pasted token into the running SSO worker's future."""
        token = self.query_one("#login-token", Input).value.strip()
        if not token:
            self._show_error("Please paste the loginToken.")
            return
        self._clear_error()

        if self._manual_token_future is not None and not self._manual_token_future.done():
            # Resolve the future — the SSO worker picks it up
            self._manual_token_future.set_result(token)
            # Disable the manual controls to prevent double-submission
            self.query_one("#login-token", Input).disabled = True
            self.query_one("#token-confirm", Button).disabled = True
        else:
            # No SSO worker running — do a direct token login
            self._clear_error()
            self._set_all_controls_enabled(False)
            self.query_one("#loading", LoadingIndicator).display = True
            self._do_direct_token_login(token)

    @work(exclusive=True, exit_on_error=False)
    async def _do_direct_token_login(self, token: str) -> None:
        """Direct token login: no loopback server needed (manual-only path)."""
        from telemente.matrix.client import LoginError

        if not await self._ensure_client_for_current_homeserver():
            return

        client = self._client
        assert client is not None

        try:
            session = await client.login_with_token(token)
        except LoginError as exc:
            logger.warning("Token login failed: %s", exc)
            self._on_login_failure(str(exc))
            return
        except Exception as exc:
            logger.exception("Unexpected error during token login: %s", exc)
            self._on_login_failure(f"Unexpected error: {exc}")
            return

        self._on_login_success(session)

    # ------------------------------------------------------------------
    # Success / failure callbacks
    # ------------------------------------------------------------------

    def _on_login_success(self, session: Session) -> None:
        logger.info("_on_login_success: user_id=%s", session.user_id)
        self._set_form_enabled(True)
        self._set_all_controls_enabled(True)
        self.query_one("#loading", LoadingIndicator).display = False
        self.post_message(LoginScreen.LoggedIn(session))

    def _on_login_failure(self, message: str) -> None:
        logger.warning("_on_login_failure: %s", message)
        self._set_form_enabled(True)
        self._set_all_controls_enabled(True)
        self.query_one("#loading", LoadingIndicator).display = False
        self._show_error(message)

    # ------------------------------------------------------------------
    # Widget visibility helpers
    # ------------------------------------------------------------------

    def _show_password_form(self) -> None:
        for wid in ("#username", "#password", "#submit"):
            w = self.query(wid)
            if w:
                w.first().display = True

    def _hide_password_form(self) -> None:
        for wid in ("#username", "#password", "#submit"):
            w = self.query(wid)
            if w:
                w.first().display = False

    def _hide_sso_area(self) -> None:
        area = self.query_one("#sso-area", Static)
        area.display = False
        # Remove any dynamically added SSO buttons
        for btn in self.query(".sso-button"):
            btn.remove()

    def _render_sso_buttons(self, flows: LoginFlows) -> None:
        """Mount SSO buttons below the sso-area static, in order."""
        # Remove existing SSO buttons first
        for btn in self.query(".sso-button"):
            btn.remove()

        area = self.query_one("#sso-area", Static)
        area.display = True

        if flows.identity_providers:
            # Mount in order by tracking the last inserted widget
            last_widget: Button | Static = area
            for idp in flows.identity_providers:
                btn = Button(
                    f"Sign in with {idp.name}",
                    id=f"sso-btn-{idp.id}",
                    classes="sso-button",
                )
                self.mount(btn, after=last_widget)
                last_widget = btn
        else:
            btn = Button(
                "Sign in with SSO",
                id="sso-btn-__default__",
                classes="sso-button",
            )
            self.mount(btn, after=area)

    def _show_sso_manual(self) -> None:
        """Show the manual token-paste UI."""
        ti = self.query_one("#login-token", Input)
        ti.display = True
        confirm = self.query_one("#token-confirm", Button)
        confirm.display = True

    def _hide_sso_manual(self) -> None:
        """Hide the manual token-paste UI."""
        ti = self.query_one("#login-token", Input)
        ti.display = False
        confirm = self.query_one("#token-confirm", Button)
        confirm.display = False

    # ------------------------------------------------------------------
    # Enabled-state helpers
    # ------------------------------------------------------------------

    def _show_error(self, message: str) -> None:
        error = self.query_one("#error", Static)
        error.update(message)
        error.display = True

    def _clear_error(self) -> None:
        error = self.query_one("#error", Static)
        error.update("")
        error.display = False

    def _set_form_enabled(self, enabled: bool) -> None:
        """Enable/disable only the password form controls."""
        for widget_id in ("#homeserver", "#username", "#password"):
            w = self.query(widget_id)
            if w:
                w.first().disabled = not enabled
        sb = self.query("#submit")
        if sb:
            sb.first().disabled = not enabled

    def _set_homeserver_enabled(self, enabled: bool) -> None:
        hs = self.query("#homeserver")
        if hs:
            hs.first().disabled = not enabled

    def _set_all_controls_enabled(self, enabled: bool) -> None:
        """Enable/disable all interactive controls."""
        self._set_form_enabled(enabled)
        for btn in self.query(".sso-button"):
            btn.disabled = not enabled
        tc = self.query("#token-confirm")
        if tc:
            tc.first().disabled = not enabled
        ti = self.query("#login-token")
        if ti:
            ti.first().disabled = not enabled

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Log worker state changes for debugging."""
        if event.state == WorkerState.ERROR:
            logger.error("Login worker errored: %s", event.worker)
