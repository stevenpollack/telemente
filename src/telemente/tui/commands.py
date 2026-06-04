"""Command palette provider for telemente.

The command palette (Ctrl+P) is the canonical source of truth for what the app
can do — every user-facing feature must appear here.  Keybindings are shortcuts
to palette commands, not replacements.

Commands exposed:
  Search rooms            — focus the room search bar
  Toggle members pane     — show/hide the right-hand members panel
  Close tab               — close the active room's tab
  Sort: Recent activity   — sort room list by newest message first (default)
  Sort: Alphabetical      — sort room list A-Z
  Toggle favourite        — add/remove m.favourite tag on active room
  Toggle low priority     — add/remove m.lowpriority tag on active room
  Toggle mute             — add/remove m.mute tag on active room
  Leave room              — confirm + leave the currently selected room
  Logout                  — clear credentials and return to login
"""

from __future__ import annotations

import logging
from collections.abc import Callable

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
    """A minimal Y / N confirmation modal screen."""

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
    """Command palette provider — the source of truth for app features."""

    def _commands(self) -> list[tuple[str, Callable[[], None], str]]:
        """Return (name, callback, help_text) for every palette command.

        Direct method references instead of string names so that a rename
        shows as a type error rather than a silent AttributeError at runtime.
        """
        return [
            ("Search rooms", self._cmd_search_rooms, "Focus the room search bar"),
            ("Toggle members pane", self._cmd_toggle_members, "Show/hide the members panel"),
            ("Close tab", self._cmd_close_tab, "Close the active room's tab"),
            (
                "Sort: Recent activity",
                self._cmd_sort_recent,
                "Sort room list by newest message first",
            ),
            ("Sort: Alphabetical", self._cmd_sort_alpha, "Sort room list A-Z by name"),
            ("Toggle favourite ★", self._cmd_toggle_favourite, "Add or remove the m.favourite tag"),
            (
                "Toggle low priority ↓",
                self._cmd_toggle_lowpriority,
                "Add or remove the m.lowpriority tag",
            ),
            ("Toggle mute 🔕", self._cmd_toggle_mute, "Add or remove the m.mute tag"),
            (
                "Leave room",
                self._cmd_leave_room,
                "Leave the currently selected room (asks for confirmation)",
            ),
            ("Logout", self._cmd_logout, "Log out and return to the login screen"),
        ]

    async def discover(self) -> Hits:
        for name, callback, help_text in self._commands():
            yield DiscoveryHit(name, callback, help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, callback, help_text in self._commands():
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), callback, help=help_text)

    # ------------------------------------------------------------------
    # Navigation / layout
    # ------------------------------------------------------------------

    def _cmd_search_rooms(self) -> None:
        from textual.widgets import Input

        from telemente.tui.screens.main import MainScreen

        screen = self.app.screen
        if isinstance(screen, MainScreen):
            try:
                screen.query_one("#room-search", Input).focus()
            except Exception:
                logger.debug("_cmd_search_rooms: #room-search not found")

    def _cmd_toggle_members(self) -> None:
        from telemente.tui.screens.main import MainScreen

        screen = self.app.screen
        if isinstance(screen, MainScreen):
            screen.action_toggle_members()

    def _cmd_close_tab(self) -> None:
        """Close the currently active room tab."""
        from telemente.tui.screens.main import MainScreen

        screen = self.app.screen
        if not isinstance(screen, MainScreen):
            return
        room_id = screen.active_room_id
        if room_id is None:
            self.app.notify("No tab is open", severity="warning")
            return
        self.app.run_worker(screen.close_tab(room_id), exclusive=False, exit_on_error=False)

    # ------------------------------------------------------------------
    # Sort
    # ------------------------------------------------------------------

    def _cmd_sort_recent(self) -> None:
        from telemente.tui.screens.main import MainScreen
        from telemente.tui.widgets.room_list import RoomList

        screen = self.app.screen
        if isinstance(screen, MainScreen):
            screen.query_one(RoomList).set_sort_mode("recent")
            self.app.notify("Sorted by recent activity", timeout=2)

    def _cmd_sort_alpha(self) -> None:
        from telemente.tui.screens.main import MainScreen
        from telemente.tui.widgets.room_list import RoomList

        screen = self.app.screen
        if isinstance(screen, MainScreen):
            screen.query_one(RoomList).set_sort_mode("alpha")
            self.app.notify("Sorted alphabetically", timeout=2)

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def _cmd_toggle_favourite(self) -> None:
        self._toggle_tag("m.favourite")

    def _cmd_toggle_lowpriority(self) -> None:
        self._toggle_tag("m.lowpriority")

    def _cmd_toggle_mute(self) -> None:
        self._toggle_tag("m.mute")

    def _toggle_tag(self, tag: str) -> None:
        from telemente.tui.screens.main import MainScreen

        screen = self.app.screen
        if not isinstance(screen, MainScreen):
            self.app.notify("No active room", severity="warning")
            return
        room_id = screen.active_room_id
        if room_id is None:
            self.app.notify("No room selected", severity="warning")
            return
        self.app.run_worker(
            self._do_toggle_tag(room_id, tag),
            exclusive=False,
            exit_on_error=False,
        )

    async def _do_toggle_tag(self, room_id: str, tag: str) -> None:
        from telemente.tui.app import TelementeApp
        from telemente.tui.screens.main import MainScreen
        from telemente.tui.widgets.room_list import RoomList

        app = self.app
        if not isinstance(app, TelementeApp):
            return
        client = app._client

        is_tagged = False
        screen = app.screen
        if isinstance(screen, MainScreen):
            try:
                room_list = screen.query_one(RoomList)
                room = next((r for r in room_list.all_rooms if r.room_id == room_id), None)
                if room is not None:
                    is_tagged = tag in room.tags
            except Exception:
                pass

        tag_labels = {
            "m.favourite": "favourite ★",
            "m.lowpriority": "low priority ↓",
            "m.mute": "mute 🔕",
        }
        label = tag_labels.get(tag, tag)

        try:
            if is_tagged:
                await client.remove_room_tag(room_id, tag)
                app.notify(f"Removed {label}", timeout=3)
            else:
                await client.set_room_tag(room_id, tag)
                app.notify(f"Set {label}", timeout=3)
        except Exception as exc:
            logger.warning("tag operation failed for %s %s: %s", tag, room_id, exc)
            app.notify(f"Tag operation failed: {exc}", severity="error")

    # ------------------------------------------------------------------
    # Room / session
    # ------------------------------------------------------------------

    def _cmd_leave_room(self) -> None:
        from telemente.tui.screens.main import MainScreen

        screen = self.app.screen
        if not isinstance(screen, MainScreen):
            self.app.notify("No active room to leave", severity="warning")
            return
        room_id = screen.active_room_id
        if room_id is None:
            self.app.notify("No room selected", severity="warning")
            return

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

    def _cmd_logout(self) -> None:
        from telemente.tui.app import TelementeApp

        app = self.app
        if isinstance(app, TelementeApp):
            app.run_worker(
                app.action_logout(),
                exclusive=True,
                exit_on_error=False,
            )
