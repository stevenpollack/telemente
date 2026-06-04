"""Command palette provider for telemente (plan jaunty-snacking-micali).

Provides discoverable commands accessible via the command palette (Ctrl+P):
- Search rooms     — focus the room search bar
- Toggle members   — show/hide the right-hand members panel
- Leave room       — confirm + leave the currently selected room
- Room actions     — favourite / low-priority tag toggles
- Logout           — clear credentials and return to login

The provider uses ``self.app`` at runtime (no import from app.py) to avoid
circular imports; it casts to ``TelementeApp`` only inside callbacks that
are called after the app is fully constructed.
"""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Simple confirmation modal
# ---------------------------------------------------------------------------


class _ConfirmScreen(ModalScreen[bool]):
    """A minimal Y / N confirmation modal screen.

    Returns ``True`` when the user confirms, ``False`` when they cancel.
    """

    DEFAULT_CSS = """
    _ConfirmScreen {
        align: center middle;
    }
    _ConfirmScreen > Vertical {
        width: 50;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }
    _ConfirmScreen Label {
        width: 1fr;
        content-align: center middle;
    }
    _ConfirmScreen Horizontal {
        height: auto;
        align: center middle;
    }
    _ConfirmScreen Button {
        margin: 1 1;
    }
    """

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._prompt)
            with Horizontal():
                yield Button("Yes", id="btn-yes", variant="error")
                yield Button("No", id="btn-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-yes")


# ---------------------------------------------------------------------------
# TelementeCommands
# ---------------------------------------------------------------------------


class TelementeCommands(Provider):
    """Command palette provider for telemente.

    Exposes Search rooms, Toggle members, Leave room, Room actions, Logout.
    """

    # ------------------------------------------------------------------
    # Discovery (shown before user types anything)
    # ------------------------------------------------------------------

    async def discover(self) -> Hits:
        yield DiscoveryHit(
            "Search rooms",
            self._cmd_search_rooms,
            help="Focus the room search bar",
        )
        yield DiscoveryHit(
            "Toggle members pane",
            self._cmd_toggle_members,
            help="Show/hide the members panel",
        )
        yield DiscoveryHit(
            "Leave room",
            self._cmd_leave_room,
            help="Leave the currently selected room (asks for confirmation)",
        )
        yield DiscoveryHit(
            "Room actions",
            self._cmd_room_actions,
            help="Favourite / low-priority toggles for the selected room",
        )
        yield DiscoveryHit(
            "Logout",
            self._cmd_logout,
            help="Log out and return to the login screen",
        )

    # ------------------------------------------------------------------
    # Search (shown as user types)
    # ------------------------------------------------------------------

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        commands: list[tuple[str, object, str]] = [
            ("Search rooms", self._cmd_search_rooms, "Focus the room search bar"),
            (
                "Toggle members pane",
                self._cmd_toggle_members,
                "Show/hide the members panel",
            ),
            (
                "Leave room",
                self._cmd_leave_room,
                "Leave the currently selected room (asks for confirmation)",
            ),
            (
                "Room actions",
                self._cmd_room_actions,
                "Favourite / low-priority toggles for the selected room",
            ),
            ("Logout", self._cmd_logout, "Log out and return to the login screen"),
        ]
        for name, callback, help_text in commands:
            score = matcher.match(name)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(name),
                    callback,  # type: ignore[arg-type]
                    help=help_text,
                )

    # ------------------------------------------------------------------
    # Command implementations
    # ------------------------------------------------------------------

    def _cmd_search_rooms(self) -> None:
        """Focus the room-search input."""
        from textual.widgets import Input

        from telemente.tui.screens.main import MainScreen

        screen = self.app.screen
        if isinstance(screen, MainScreen):
            try:
                screen.query_one("#room-search", Input).focus()
            except Exception:
                logger.debug("_cmd_search_rooms: #room-search not found")

    def _cmd_toggle_members(self) -> None:
        """Toggle the right-hand members panel visibility."""
        from telemente.tui.screens.main import MainScreen

        screen = self.app.screen
        if isinstance(screen, MainScreen):
            screen.action_toggle_members()

    def _cmd_leave_room(self) -> None:
        """Show a Y/N confirmation then leave the active room."""
        from telemente.tui.screens.main import MainScreen

        screen = self.app.screen
        if not isinstance(screen, MainScreen):
            self.app.notify("No active room to leave", severity="warning")
            return
        room_id = screen.active_room_id
        if room_id is None:
            self.app.notify("No room selected", severity="warning")
            return

        # Get display name from the room list for a nicer prompt.
        from telemente.tui.widgets.room_list import RoomList

        try:
            room_list = screen.query_one(RoomList)
            display_name = next(
                (r.display_name for r in room_list.all_rooms if r.room_id == room_id),
                room_id,
            )
        except Exception:
            display_name = room_id

        def _on_confirmed(confirmed: bool | None) -> None:
            if confirmed:
                self.app.run_worker(
                    _do_leave(room_id),
                    exclusive=False,
                    exit_on_error=False,
                )

        async def _do_leave(rid: str) -> None:
            from telemente.tui.app import TelementeApp

            app = self.app
            if not isinstance(app, TelementeApp):
                return
            try:
                await app._client.leave_room(rid)
                app.notify(f"Left {display_name}", severity="information")
            except Exception as exc:
                logger.warning("leave_room failed for %s: %s", rid, exc)
                app.notify(f"Failed to leave room: {exc}", severity="error")

        self.app.push_screen(
            _ConfirmScreen(f"Leave '{display_name}'?"),
            _on_confirmed,
        )

    def _cmd_room_actions(self) -> None:
        """Show favourite / low-priority tag toggles for the active room."""
        from telemente.tui.screens.main import MainScreen

        screen = self.app.screen
        if not isinstance(screen, MainScreen):
            self.app.notify("No active room", severity="warning")
            return
        room_id = screen.active_room_id
        if room_id is None:
            self.app.notify("No room selected", severity="warning")
            return

        self.app.push_screen(_RoomActionsScreen(room_id))

    def _cmd_logout(self) -> None:
        """Log out and return to login screen."""
        from telemente.tui.app import TelementeApp

        app = self.app
        if isinstance(app, TelementeApp):
            app.run_worker(
                app.action_logout(),
                exclusive=True,
                exit_on_error=False,
            )


# ---------------------------------------------------------------------------
# Room actions modal
# ---------------------------------------------------------------------------


class _RoomActionsScreen(ModalScreen[None]):
    """Modal showing favourite / low-priority tag toggle buttons for a room."""

    DEFAULT_CSS = """
    _RoomActionsScreen {
        align: center middle;
    }
    _RoomActionsScreen > Vertical {
        width: 40;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }
    _RoomActionsScreen Label {
        width: 1fr;
        content-align: center middle;
        margin-bottom: 1;
    }
    _RoomActionsScreen Button {
        width: 1fr;
        margin: 0 0 1 0;
    }
    """

    def __init__(self, room_id: str) -> None:
        super().__init__()
        self._room_id = room_id

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Room actions")
            yield Button("★  Toggle favourite", id="btn-favourite")
            yield Button("↓  Toggle low priority", id="btn-lowpriority")
            yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-cancel":
            self.dismiss()
        elif btn_id == "btn-favourite":
            self.run_worker(
                self._toggle_tag("m.favourite"),
                exclusive=False,
            )
            self.dismiss()
        elif btn_id == "btn-lowpriority":
            self.run_worker(
                self._toggle_tag("m.lowpriority"),
                exclusive=False,
            )
            self.dismiss()

    async def _toggle_tag(self, tag: str) -> None:
        from telemente.tui.app import TelementeApp
        from telemente.tui.screens.main import MainScreen
        from telemente.tui.widgets.room_list import RoomList

        app = self.app
        if not isinstance(app, TelementeApp):
            return
        client = app._client
        room_id = self._room_id

        # Determine current tag state from room list.
        screen = app.screen
        is_tagged = False
        if isinstance(screen, MainScreen):
            try:
                room_list = screen.query_one(RoomList)
                room = next((r for r in room_list.all_rooms if r.room_id == room_id), None)
                if room is not None:
                    is_tagged = tag in room.tags
            except Exception:
                pass

        try:
            if is_tagged:
                await client.remove_room_tag(room_id, tag)
                app.notify(f"Removed tag {tag}")
            else:
                await client.set_room_tag(room_id, tag)
                app.notify(f"Set tag {tag}")
        except Exception as exc:
            logger.warning("tag operation failed for %s %s: %s", tag, room_id, exc)
            app.notify(f"Tag operation failed: {exc}", severity="error")
