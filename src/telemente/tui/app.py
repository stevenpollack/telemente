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

import contextlib
import logging
from collections.abc import Callable
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.message import Message as TextualMessage
from textual.widgets import Footer, Header, Static  # Static used in compose() placeholder

from telemente.config import CredentialStore, Paths, RoomCache, Session, Settings
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
        self._room_cache = RoomCache(paths)
        self._default_homeserver = default_homeserver or settings.homeserver
        logger.info("TelementeApp.__init__: homeserver=%s", self._default_homeserver)

        # Client may be injected (tests) or lazily created per homeserver.
        self._client: MatrixClient = client or MatrixClient(
            self._default_homeserver,
            store_path=str(paths.store_dir),
            device_name=settings.default_device_name,
        )

        # Subscription handle; set once we subscribe.
        self._unsubscribe: Callable[[], None] | None = None
        self._cached_user_id: str | None = None

    @property
    def client(self) -> MatrixClient:
        return self._client

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
            logger.info("on_mount: no saved session, showing login screen")
            paths = Paths.default().ensure()
            settings_path = paths.config_dir / "settings.toml"
            settings = Settings.load(settings_path)
            store_path = str(paths.store_dir)
            device_name = settings.default_device_name

            def _client_factory(homeserver: str) -> MatrixClient:
                return MatrixClient(
                    homeserver,
                    store_path=store_path,
                    device_name=device_name,
                )

            self.push_screen(
                LoginScreen(
                    _client_factory,
                    default_homeserver=self._default_homeserver,
                )
            )

    async def on_unmount(self) -> None:
        """Teardown: unsubscribe from client events and close the client."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._cached_user_id is not None:
            rooms = self._client.rooms()
            if rooms:
                self._room_cache.save(self._cached_user_id, rooms)
        await self._client.close()

    async def _restore_session(self, session: Session) -> None:
        # Rebuild the client for the session's homeserver (it may differ from
        # the default in settings.toml if the user typed a different one at login).
        if session.homeserver != self._client.homeserver:
            logger.info(
                "Session homeserver %s differs from default %s — rebuilding client",
                session.homeserver,
                self._client.homeserver,
            )
            paths = Paths.default().ensure()
            settings_path = paths.config_dir / "settings.toml"
            settings = Settings.load(settings_path)
            self._client = MatrixClient(
                session.homeserver,
                store_path=str(paths.store_dir),
                device_name=settings.default_device_name,
            )

        await self._client.restore(session)
        self._cached_user_id = session.user_id
        logger.info("Session restored for %s; pushing main screen", session.user_id)
        main = MainScreen(self._client)
        self.push_screen(main)
        # Pre-populate the room list from cache before the first sync returns.
        cached = self._room_cache.load(session.user_id)
        if cached:
            logger.info("Loaded %d rooms from cache", len(cached))
            from telemente.matrix.models import RoomSummary
            from telemente.tui.widgets.room_list import RoomList

            with contextlib.suppress(Exception):
                main.query_one(RoomList).set_rooms(cached)  # type: ignore[arg-type]

            # Seed the client's last_activity dict so rooms() can sort by
            # recency immediately, before any incremental sync arrives.
            activities = {
                r.room_id: r.last_activity
                for r in cached
                if isinstance(r, RoomSummary) and r.last_activity is not None
            }
            self._client.seed_last_activity(activities)
        self.start_sync_and_subscribe()

    def on_login_screen_logged_in(self, message: LoginScreen.LoggedIn) -> None:
        """Persist the session and navigate to the main screen after successful login.

        Rebuilds the app-level client for the session's homeserver so that
        subsequent sync/messaging uses the correct server, then restores the
        session credentials into it.
        """
        session = message.session
        logger.info("Logged in as %s — persisting session", session.user_id)
        self._credential_store.save(session)
        self._cached_user_id = session.user_id

        # Rebuild the client for the (possibly user-entered) homeserver so it
        # is ready for sync and messaging via MainScreen.
        paths = Paths.default().ensure()
        settings_path = paths.config_dir / "settings.toml"
        settings = Settings.load(settings_path)
        self._client = MatrixClient(
            session.homeserver,
            store_path=str(paths.store_dir),
            device_name=settings.default_device_name,
        )
        # Restore credentials without re-authenticating (login already done)
        self.run_worker(
            self._restore_and_navigate(session),
            exclusive=True,
            exit_on_error=False,
        )

    async def _restore_and_navigate(self, session: Session) -> None:
        """Restore credentials into the app client and push MainScreen."""
        await self._client.restore(session)
        # Push the screen FIRST (same ordering fix as _restore_session).
        self.push_screen(MainScreen(self._client))
        self.start_sync_and_subscribe()

    # ------------------------------------------------------------------
    # Sync lifecycle (plan 0009)
    # ------------------------------------------------------------------

    def start_sync_and_subscribe(self) -> None:
        """Subscribe to client events and start sync as a Textual worker."""
        logger.info("Starting sync and subscribing to client events")
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
            logger.debug("ClientEvent: RoomsChanged with %d rooms", len(event.rooms))
            if self._cached_user_id:
                self._room_cache.save(self._cached_user_id, event.rooms)
            self.post_message(_ClientRoomsChanged(event))
        elif isinstance(event, NewMessage):
            logger.info(
                "ClientEvent: NewMessage room=%s sender=%s event_id=%s",
                event.message.room_id,
                event.message.sender,
                event.message.event_id,
            )
            self.post_message(_ClientNewMessage(event))
        else:
            logger.debug("ClientEvent: MembersChanged room=%s", event.room_id)
            self.post_message(_ClientMembersChanged(event))

    # ------------------------------------------------------------------
    # Textual message handlers — route to the active MainScreen
    # ------------------------------------------------------------------

    def on__client_rooms_changed(self, message: _ClientRoomsChanged) -> None:
        screen = self.screen
        if not isinstance(screen, MainScreen):
            logger.debug("on__client_rooms_changed: no MainScreen active, discarding")
            return
        screen.handle_rooms_changed(message.event)

    def on__client_new_message(self, message: _ClientNewMessage) -> None:
        screen = self.screen
        if not isinstance(screen, MainScreen):
            logger.debug("on__client_new_message: no MainScreen active, discarding")
            return
        screen.handle_new_message(message.event)

    def on__client_members_changed(self, message: _ClientMembersChanged) -> None:
        screen = self.screen
        if not isinstance(screen, MainScreen):
            logger.debug("on__client_members_changed: no MainScreen active, discarding")
            return
        screen.handle_members_changed(message.event)

    # ------------------------------------------------------------------
    # App-level actions
    # ------------------------------------------------------------------

    async def action_logout(self) -> None:
        """Log out: clear credentials, close client, return to login screen."""
        logger.info("Logging out — clearing credentials and returning to login")
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._credential_store.clear()
        await self._client.close()

        # Pop all screens until we're at the base, then push LoginScreen.
        while len(self.screen_stack) > 1:
            self.pop_screen()

        paths = Paths.default().ensure()
        settings_path = paths.config_dir / "settings.toml"
        settings = Settings.load(settings_path)
        store_path = str(paths.store_dir)
        device_name = settings.default_device_name

        def _client_factory(homeserver: str) -> MatrixClient:
            return MatrixClient(
                homeserver,
                store_path=store_path,
                device_name=device_name,
            )

        self.push_screen(
            LoginScreen(
                _client_factory,
                default_homeserver=self._default_homeserver,
            )
        )


# ---------------------------------------------------------------------------
# Register command palette provider (done after class body to avoid circular
# imports — commands.py imports TelementeApp only inside function bodies).
# ---------------------------------------------------------------------------

from telemente.tui.commands import TelementeCommands  # noqa: E402

TelementeApp.COMMANDS = TelementeApp.COMMANDS | {TelementeCommands}
