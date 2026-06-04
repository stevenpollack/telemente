"""Main screen for telemente (plan 0005).

Three-panel layout: collapsible room list (left), message view (center),
collapsible member list (right).  Plan 0006 replaces the placeholder left
panel with the real RoomList widget.
"""

from __future__ import annotations

import logging
from typing import ClassVar, Protocol

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, Input

from telemente.matrix.models import Member, Message
from telemente.tui.widgets.member_list import MemberList
from telemente.tui.widgets.message_view import MessageView
from telemente.tui.widgets.room_list import RoomList

logger = logging.getLogger(__name__)


class _MainClient(Protocol):
    """Structural protocol for the subset of MatrixClient used by MainScreen.

    MainScreen delegates data access to child widgets (0006-0008).
    The methods here match the subset consumed by MessageView and MemberList
    so mypy can verify DI without coupling to the real client.
    """

    async def messages(self, room_id: str, limit: int = 50) -> list[Message]: ...

    async def send_text(self, room_id: str, body: str) -> None: ...

    def members(self, room_id: str) -> list[Member]: ...


# ---------------------------------------------------------------------------
# MainScreen
# ---------------------------------------------------------------------------


class MainScreen(Screen[None]):
    """Three-panel main screen: room list | message view | member list."""

    BINDINGS: ClassVar[list[BindingType]] = [
        ("ctrl+b", "toggle_rooms", "Rooms"),
        ("ctrl+r", "toggle_members", "Members"),
        ("ctrl+k", "focus_search", "Search rooms"),
    ]

    rooms_visible: reactive[bool] = reactive(True)
    members_visible: reactive[bool] = reactive(True)

    def __init__(self, client: _MainClient) -> None:
        super().__init__()
        self._client = client

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-layout"):
            yield RoomList(id="rooms-panel")
            yield MessageView(self._client, id="message-panel")
            yield MemberList(self._client, id="members-panel")
        yield Footer()

    def on_mount(self) -> None:
        logger.debug("MainScreen mounted")

    # ------------------------------------------------------------------
    # Reactive watchers
    # ------------------------------------------------------------------

    def watch_rooms_visible(self, visible: bool) -> None:
        self.query_one("#rooms-panel").display = visible

    def watch_members_visible(self, visible: bool) -> None:
        self.query_one("#members-panel").display = visible

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_toggle_rooms(self) -> None:
        """Toggle the left rooms panel (ctrl+b)."""
        self.rooms_visible = not self.rooms_visible

    def action_toggle_members(self) -> None:
        """Toggle the right members panel (ctrl+r)."""
        self.members_visible = not self.members_visible

    def action_focus_search(self) -> None:
        """Focus the room-search input (ctrl+k)."""
        self.query_one("#room-search", Input).focus()
