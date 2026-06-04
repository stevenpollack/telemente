"""The root Textual application (plan 0004).

TelementeApp owns the MatrixClient and CredentialStore.  On startup it
either restores a saved session (skipping the login screen) or pushes
LoginScreen.  Successful login is handled here: the session is persisted
and the app navigates forward.

TODO (plan 0005): replace the post-login placeholder with the real main screen.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.widgets import Footer, Header, Static

from telemente.config import CredentialStore, Paths, Session, Settings
from telemente.matrix.client import MatrixClient
from telemente.tui.screens.login import LoginScreen

logger = logging.getLogger(__name__)


class TelementeApp(App[None]):
    """Top-level telemente application."""

    TITLE = "telemente"
    CSS_PATH = "styles/app.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [("q", "quit", "Quit")]

    def __init__(
        self,
        client: MatrixClient | None = None,
        credential_store: CredentialStore | None = None,
        default_homeserver: str | None = None,
    ) -> None:
        """Construct the app.

        Parameters
        ----------
        client:
            MatrixClient to use.  If None, one is created lazily from Settings.
        credential_store:
            CredentialStore to use.  If None, the default XDG-based store is used.
        default_homeserver:
            Override the homeserver shown in the login form.  Falls back to
            Settings.homeserver when None.
        """
        super().__init__()
        paths = Paths.default().ensure()
        settings_path = paths.config_dir / "settings.toml"
        settings = Settings.load(settings_path)

        self._credential_store = credential_store or CredentialStore(paths)
        self._default_homeserver = default_homeserver or settings.homeserver

        # Client may be injected (tests) or lazily created per homeserver.
        self._client: MatrixClient = client or MatrixClient(
            self._default_homeserver,
            store_path=str(paths.store_dir),
            device_name=settings.default_device_name,
        )

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="placeholder")
        yield Footer()

    def on_mount(self) -> None:
        """On startup: restore session or show login."""
        saved = self._credential_store.load()
        if saved is not None:
            # Session exists — restore without showing the login screen.
            # TODO (plan 0005): start sync and push the main screen instead.
            logger.info("Restoring saved session for %s", saved.user_id)
            self.run_worker(
                self._restore_session(saved),
                exclusive=True,
                exit_on_error=False,
            )
        else:
            self.push_screen(LoginScreen(self._client, default_homeserver=self._default_homeserver))

    async def _restore_session(self, session: Session) -> None:
        await self._client.restore(session)
        # TODO (plan 0005): push main screen here.
        logger.info("Session restored; main screen not yet implemented (plan 0005).")

    def on_login_screen_logged_in(self, message: LoginScreen.LoggedIn) -> None:
        """Persist the session and navigate forward after a successful login."""
        session = message.session
        logger.info("Logged in as %s — persisting session", session.user_id)
        self._credential_store.save(session)
        # TODO (plan 0005): push the main screen here instead of logging only.
        logger.info("Navigation to main screen not yet implemented (plan 0005).")
