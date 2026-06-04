"""The root Textual application (plan 0004 / 0005 / 0009).

TelementeApp owns the MatrixClient and CredentialStore.  On startup it
either restores a saved session (skipping the login screen) or pushes
LoginScreen.  Successful login is handled here: the session is persisted
and the app navigates to MainScreen.

Plan 0009: after reaching MainScreen the app starts the matrix sync loop
as a Textual worker (same event loop; no threads), subscribes to client
events, and bridges them via Textual messages to the active screen.
On exit it awaits client.close() to cancel sync and tear down cleanly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.message import Message as TextualMessage
from textual.widgets import Footer, Header, Static  # Static used in compose() placeholder

from telemente.config import CredentialStore, Paths, Session, Settings
from telemente.matrix.client import (
    ClientEvent,
    MatrixClient,
    MembersChanged,
    NewMessage,
    RoomsChanged,
)
from telemente.tui.screens.login import LoginScreen
from telemente.tui.screens.main import MainScreen

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Textual message wrappers for client events
# ---------------------------------------------------------------------------


class _ClientRoomsChanged(TextualMessage):
    """Wraps a RoomsChanged client event for Textual message routing."""

    def __init__(self, event: RoomsChanged) -> None:
        super().__init__()
        self.event = event


class _ClientNewMessage(TextualMessage):
    """Wraps a NewMessage client event for Textual message routing."""

    def __init__(self, event: NewMessage) -> None:
        super().__init__()
        self.event = event


class _ClientMembersChanged(TextualMessage):
    """Wraps a MembersChanged client event for Textual message routing."""

    def __init__(self, event: MembersChanged) -> None:
        super().__init__()
        self.event = event


# ---------------------------------------------------------------------------
# TelementeApp
# ---------------------------------------------------------------------------


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

        # Subscription handle; set once we subscribe.
        self._unsubscribe: Callable[[], None] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="placeholder")
        yield Footer()

    def on_mount(self) -> None:
        """On startup: restore session or show login."""
        saved = self._credential_store.load()
        if saved is not None:
            # Session exists — restore without showing the login screen.
            logger.info("Restoring saved session for %s", saved.user_id)
            self.run_worker(
                self._restore_session(saved),
                exclusive=True,
                exit_on_error=False,
            )
        else:
            self.push_screen(LoginScreen(self._client, default_homeserver=self._default_homeserver))

    async def on_unmount(self) -> None:
        """Teardown: unsubscribe from client events and close the client."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        await self._client.close()

    async def _restore_session(self, session: Session) -> None:
        await self._client.restore(session)
        logger.info("Session restored for %s; pushing main screen", session.user_id)
        self._start_sync_and_subscribe()
        self.push_screen(MainScreen(self._client))

    def on_login_screen_logged_in(self, message: LoginScreen.LoggedIn) -> None:
        """Persist the session and navigate to the main screen after successful login."""
        session = message.session
        logger.info("Logged in as %s — persisting session", session.user_id)
        self._credential_store.save(session)
        self._start_sync_and_subscribe()
        self.push_screen(MainScreen(self._client))

    # ------------------------------------------------------------------
    # Sync lifecycle (plan 0009)
    # ------------------------------------------------------------------

    def _start_sync_and_subscribe(self) -> None:
        """Subscribe to client events and start sync as a Textual worker."""
        # Subscribe before starting sync so no events are missed.
        self._unsubscribe = self._client.subscribe(self._on_client_event)
        self.run_worker(
            self._client.start_sync(),
            exclusive=False,
            exit_on_error=False,
        )

    def _on_client_event(self, event: ClientEvent) -> None:
        """Convert a ClientEvent into a Textual message and post it.

        This callback runs on Textual's asyncio loop (no threads).
        post_message is thread-safe and order-preserving.
        """
        if isinstance(event, RoomsChanged):
            self.post_message(_ClientRoomsChanged(event))
        elif isinstance(event, NewMessage):
            self.post_message(_ClientNewMessage(event))
        elif isinstance(event, MembersChanged):
            self.post_message(_ClientMembersChanged(event))

    # ------------------------------------------------------------------
    # Textual message handlers — route to the active MainScreen
    # ------------------------------------------------------------------

    def on__client_rooms_changed(self, message: _ClientRoomsChanged) -> None:
        screen = self.screen
        if isinstance(screen, MainScreen):
            screen.handle_rooms_changed(message.event)

    def on__client_new_message(self, message: _ClientNewMessage) -> None:
        screen = self.screen
        if isinstance(screen, MainScreen):
            screen.handle_new_message(message.event)

    def on__client_members_changed(self, message: _ClientMembersChanged) -> None:
        screen = self.screen
        if isinstance(screen, MainScreen):
            screen.handle_members_changed(message.event)
