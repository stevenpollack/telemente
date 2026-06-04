"""Login screen for telemente (plan 0004).

Collects homeserver / username / password, logs in via MatrixClient,
and posts a LoggedIn message on success. The app (not this screen) is
responsible for persisting the session and navigating to the main screen.

Design note: the screen takes a MatrixClient by injection and is entirely
ignorant of CredentialStore (single responsibility, easier to test).
If the user edits the homeserver field, the app should reconstruct the
MatrixClient with the new homeserver before calling login — this screen
uses whatever client it was given.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar, Protocol

from textual import work
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.message import Message as TextualMessage
from textual.screen import Screen
from textual.widgets import Button, Input, LoadingIndicator, Static
from textual.worker import Worker, WorkerState

from telemente.config import Session

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class _LoginClient(Protocol):
    """Structural protocol for the subset of MatrixClient used by LoginScreen.

    Defines only the ``login`` method so that FakeMatrixClient satisfies it
    without inheriting from the real client, keeping tests independent of nio.
    """

    async def login(self, user: str, password: str) -> Session: ...


class LoginScreen(Screen[None]):
    """Collects credentials and logs in via the injected MatrixClient."""

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
        client: _LoginClient,
        *,
        default_homeserver: str = "https://matrix.org",
    ) -> None:
        super().__init__()
        self._client = client
        self._default_homeserver = default_homeserver

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("telemente — Log In", id="login-title")
        yield Input(
            value=self._default_homeserver,
            placeholder="https://matrix.org",
            id="homeserver",
        )
        yield Input(
            placeholder="@user:server or user",
            id="username",
        )
        yield Input(
            placeholder="password",
            password=True,
            id="password",
        )
        yield Static("", id="error")
        yield Button("Log in", id="submit", variant="primary")
        yield LoadingIndicator(id="loading")

    def on_mount(self) -> None:
        # Hide loading and error initially
        self.query_one("#loading", LoadingIndicator).display = False
        self.query_one("#error", Static).display = False

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self._attempt_login()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        _ = event  # unused; any field submission triggers login
        self._attempt_login()

    # ------------------------------------------------------------------
    # Login logic
    # ------------------------------------------------------------------

    def _attempt_login(self) -> None:
        """Validate fields and kick off the login worker."""
        homeserver = self.query_one("#homeserver", Input).value.strip()
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value.strip()

        if not homeserver or not username or not password:
            self._show_error("All fields are required.")
            return

        self._clear_error()
        self._set_form_enabled(False)
        self.query_one("#loading", LoadingIndicator).display = True

        self._do_login(username, password)

    @work(exclusive=True, exit_on_error=False)
    async def _do_login(self, username: str, password: str) -> None:
        """Textual worker: calls client.login on the async event loop."""
        from telemente.matrix.client import LoginError

        try:
            session = await self._client.login(username, password)
        except LoginError as exc:
            logger.warning("Login failed: %s", exc)
            self._on_login_failure(str(exc))
            return
        except Exception as exc:
            logger.exception("Unexpected error during login: %s", exc)
            self._on_login_failure(f"Unexpected error: {exc}")
            return

        self._on_login_success(session)

    def _on_login_success(self, session: Session) -> None:
        self._set_form_enabled(True)
        self.query_one("#loading", LoadingIndicator).display = False
        self.post_message(LoginScreen.LoggedIn(session))

    def _on_login_failure(self, message: str) -> None:
        self._set_form_enabled(True)
        self.query_one("#loading", LoadingIndicator).display = False
        self._show_error(message)

    # ------------------------------------------------------------------
    # Helpers
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
        for widget_id in ("#homeserver", "#username", "#password"):
            self.query_one(widget_id, Input).disabled = not enabled
        self.query_one("#submit", Button).disabled = not enabled

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Log worker state changes for debugging."""
        if event.state == WorkerState.ERROR:
            logger.error("Login worker errored: %s", event.worker)
