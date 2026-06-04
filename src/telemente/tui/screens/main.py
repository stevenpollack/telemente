"""Main screen for telemente (plan 0005).

Three-panel layout: collapsible room list (left), message view (center),
collapsible member list (right).  The real panel widgets are provided by
plans 0006/0007/0008; this plan mounts lightweight placeholders behind the
same widget ids so layout and collapse can be built and tested independently.
"""

from __future__ import annotations

import logging
from typing import ClassVar, Protocol

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Static

logger = logging.getLogger(__name__)


class _MainClient(Protocol):
    """Structural protocol for the subset of MatrixClient used by MainScreen.

    Currently empty — MainScreen delegates data access to child widgets (0006-0008).
    Keeping this explicit lets mypy verify DI without coupling to the real client.
    """


# ---------------------------------------------------------------------------
# Placeholder panel widgets
#
# These use the correct widget ids/classes so that plans 0006/0007/0008 can
# swap in the real widgets without touching MainScreen's compose().
# ---------------------------------------------------------------------------


class _RoomsPanel(Vertical):
    """Placeholder for the left rooms panel (real widget: RoomList, plan 0006).

    Contains a room-search Input (id="room-search") so that ctrl+k focus works
    before the real RoomList is wired in.
    """

    def compose(self) -> ComposeResult:
        yield Input(id="room-search", placeholder="Search rooms…")
        yield Static("Rooms panel")


class _MessagePanel(Static):
    """Placeholder for the center message panel (real widget: MessageView, plan 0007)."""

    def __init__(self, widget_id: str) -> None:
        super().__init__("Message panel", id=widget_id)


class _MembersPanel(Static):
    """Placeholder for the right member panel (real widget: MemberList, plan 0008)."""

    def __init__(self, widget_id: str) -> None:
        super().__init__("Members panel", id=widget_id)


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
            yield _RoomsPanel(id="rooms-panel")
            yield _MessagePanel("message-panel")
            yield _MembersPanel("members-panel")
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
