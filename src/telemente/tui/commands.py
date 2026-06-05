"""Command palette provider for telemente.

The command palette (Ctrl+P) is the canonical source of truth for what the app
can do — every user-facing feature must appear here.  Keybindings are shortcuts
to palette commands, not replacements.

Commands exposed:
  Search rooms            — focus the room search bar
  Toggle members pane     — show/hide the right-hand members panel
  Toggle log viewer       — show/hide the bottom log tail panel
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

from textual.command import DiscoveryHit, Hit, Hits, Provider

logger = logging.getLogger(__name__)


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
            ("Search rooms", self.cmd_search_rooms, "Focus the room search bar"),
            ("Toggle members pane", self.cmd_toggle_members, "Show/hide the members panel"),
            ("Toggle log viewer", self.cmd_toggle_log, "Show/hide the log tail panel"),
            ("Close tab", self.cmd_close_tab, "Close the active room's tab"),
            (
                "Sort: Recent activity",
                self.cmd_sort_recent,
                "Sort room list by newest message first",
            ),
            ("Sort: Alphabetical", self.cmd_sort_alpha, "Sort room list A-Z by name"),
            ("Toggle favourite ★", self.cmd_toggle_favourite, "Add or remove the m.favourite tag"),
            (
                "Toggle low priority ↓",
                self.cmd_toggle_lowpriority,
                "Add or remove the m.lowpriority tag",
            ),
            ("Toggle mute 🔕", self.cmd_toggle_mute, "Add or remove the m.mute tag"),
            (
                "Leave room",
                self.cmd_leave_room,
                "Leave the currently selected room (asks for confirmation)",
            ),
            (
                "React to message",
                self.cmd_react_to_message,
                "Open emoji picker to react to the focused message",
            ),
            (
                "Open thread",
                self.cmd_open_thread,
                "View the thread for the focused message (if any)",
            ),
            (
                "Search in room",
                self.cmd_search_in_room,
                "Search message history of the active room (Ctrl+F)",
            ),
            ("Logout", self.cmd_logout, "Log out and return to the login screen"),
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

    def cmd_search_rooms(self) -> None:
        from textual.widgets import Input

        from telemente.tui.screens.main import MainScreen

        screen = self.app.screen
        if isinstance(screen, MainScreen):
            try:
                screen.query_one("#room-search", Input).focus()
            except Exception:
                logger.debug("_cmd_search_rooms: #room-search not found")

    def cmd_toggle_members(self) -> None:
        from telemente.tui.screens.main import MainScreen

        screen = self.app.screen
        if isinstance(screen, MainScreen):
            screen.action_toggle_members()

    def cmd_toggle_log(self) -> None:
        from telemente.tui.screens.main import MainScreen

        screen = self.app.screen
        if isinstance(screen, MainScreen):
            screen.action_toggle_log()

    def cmd_close_tab(self) -> None:
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

    def cmd_sort_recent(self) -> None:
        from telemente.tui.screens.main import MainScreen
        from telemente.tui.widgets.room_list import RoomList

        screen = self.app.screen
        logger.info("_cmd_sort_recent: screen=%s", type(screen).__name__)
        if isinstance(screen, MainScreen):
            screen.query_one(RoomList).set_sort_mode("recent")
            self.app.notify("Sorted by recent activity", timeout=2)
        else:
            logger.warning("_cmd_sort_recent: screen is not MainScreen, sort skipped")

    def cmd_sort_alpha(self) -> None:
        from telemente.tui.screens.main import MainScreen
        from telemente.tui.widgets.room_list import RoomList

        screen = self.app.screen
        logger.info("_cmd_sort_alpha: screen=%s", type(screen).__name__)
        if isinstance(screen, MainScreen):
            screen.query_one(RoomList).set_sort_mode("alpha")
            self.app.notify("Sorted alphabetically", timeout=2)
        else:
            logger.warning("_cmd_sort_alpha: screen is not MainScreen, sort skipped")

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def cmd_toggle_favourite(self) -> None:
        self._toggle_tag("m.favourite")

    def cmd_toggle_lowpriority(self) -> None:
        self._toggle_tag("m.lowpriority")

    def cmd_toggle_mute(self) -> None:
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
        screen._toggle_tag_for(room_id, tag)  # pyright: ignore[reportPrivateUsage]

    # ------------------------------------------------------------------
    # Room / session
    # ------------------------------------------------------------------

    def cmd_leave_room(self) -> None:
        from telemente.tui.screens.main import MainScreen

        screen = self.app.screen
        if not isinstance(screen, MainScreen):
            self.app.notify("No active room to leave", severity="warning")
            return
        room_id = screen.active_room_id
        if room_id is None:
            self.app.notify("No room selected", severity="warning")
            return
        screen._confirm_leave_room(room_id)  # pyright: ignore[reportPrivateUsage]

    def cmd_react_to_message(self) -> None:
        """Open emoji picker to react to the focused (or last) message."""
        from telemente.tui.screens.main import MainScreen
        from telemente.tui.widgets.message_view import MessageRow

        screen = self.app.screen
        if not isinstance(screen, MainScreen):
            return
        active_room = screen.active_room_id
        if active_room is None:
            self.app.notify("No room selected", severity="warning")
            return
        view = screen.message_view_for(active_room)
        if view is None:
            return
        focused = list(view.query("MessageRow:focus"))
        if focused:
            row = focused[0]
            if isinstance(row, MessageRow):
                view._open_emoji_picker_for(row.message.event_id)  # pyright: ignore[reportPrivateUsage]
                return
        rows = list(view.query(MessageRow))
        if not rows:
            return
        view._open_emoji_picker_for(rows[-1].message.event_id)  # pyright: ignore[reportPrivateUsage]

    def cmd_open_thread(self) -> None:
        """Open the thread panel for the focused message (if it is a thread reply)."""
        from telemente.tui.screens.main import MainScreen
        from telemente.tui.widgets.message_view import MessageRow

        screen = self.app.screen
        if not isinstance(screen, MainScreen):
            return
        active_room = screen.active_room_id
        if active_room is None:
            self.app.notify("No room selected", severity="warning")
            return
        view = screen.message_view_for(active_room)
        if view is None:
            return
        # Prefer focused row, fall back to last row.
        focused = list(view.query("MessageRow:focus"))
        row: MessageRow | None = None
        if focused and isinstance(focused[0], MessageRow):
            row = focused[0]
        else:
            rows = list(view.query(MessageRow))
            if rows:
                row = rows[-1]
        if row is None:
            return
        if row.message.thread_root_id is None:
            self.app.notify("Focused message is not part of a thread.", severity="warning")
            return
        screen.open_thread(row.message.room_id, row.message.thread_root_id)

    def cmd_search_in_room(self) -> None:
        """Open the in-room search bar for the active room's MessageView."""
        from telemente.tui.screens.main import MainScreen

        screen = self.app.screen
        if not isinstance(screen, MainScreen):
            return
        active_room = screen.active_room_id
        if active_room is None:
            self.app.notify("No room selected", severity="warning")
            return
        view = screen.message_view_for(active_room)
        if view is None:
            return
        view.action_open_search()  # type: ignore[attr-defined]  # action_open_search added by debug agent

    def cmd_logout(self) -> None:
        from telemente.tui.app import TelementeApp

        app = self.app
        if isinstance(app, TelementeApp):
            app.run_worker(
                app.action_logout(),
                exclusive=True,
                exit_on_error=False,
            )
