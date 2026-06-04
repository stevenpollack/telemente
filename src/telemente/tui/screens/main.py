"""Main screen for telemente (plan 0005 / 0009).

Three-panel layout: collapsible room list (left), message view (center),
collapsible member list (right).  Plan 0006 replaces the placeholder left
panel with the real RoomList widget.

Plan 0009 adds live event routing: RoomsChanged, NewMessage, MembersChanged
are dispatched to the appropriate widgets; RoomList.RoomSelected triggers
message + member loading and clears unread.
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

from telemente.matrix.client import MembersChanged, NewMessage, RoomsChanged
from telemente.matrix.models import Member, Message, RoomSummary
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

    async def send_text(
        self, room_id: str, body: str, reply_to_event_id: str | None = None
    ) -> str: ...

    async def send_reaction(self, room_id: str, event_id: str, emoji: str) -> None: ...

    def me(self) -> tuple[str, str]: ...

    def members(self, room_id: str) -> list[Member]: ...

    def rooms(self) -> list[RoomSummary]: ...


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
        self._active_room_id: str | None = None
        # Track unread counts by room_id; keys are rooms with non-zero unread.
        self._unread: dict[str, int] = {}

    @property
    def active_room_id(self) -> str | None:
        """The currently active room ID, or None if no room is selected."""
        return self._active_room_id

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
        # Belt-and-suspenders: load whatever rooms the client already knows
        # about. This handles the case where the first RoomsChanged sync event
        # fired before this screen was fully mounted and was therefore dropped.
        rooms = self._client.rooms()
        if rooms:
            logger.debug("MainScreen.on_mount: pre-loading %d rooms from client", len(rooms))
            self.query_one(RoomList).set_rooms(rooms)

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

    # ------------------------------------------------------------------
    # Client event handlers (plan 0009)
    # ------------------------------------------------------------------

    def handle_rooms_changed(self, event: RoomsChanged) -> None:
        """Route a RoomsChanged event to RoomList, preserving unread counts."""
        rooms_with_unread = [
            RoomSummary(
                room_id=r.room_id,
                display_name=r.display_name,
                unread_count=self._unread.get(r.room_id, r.unread_count),
                last_activity=r.last_activity,
                encrypted=r.encrypted,
                tags=r.tags,
            )
            for r in event.rooms
        ]
        self.query_one(RoomList).set_rooms(rooms_with_unread)

    def handle_new_message(self, event: NewMessage) -> None:
        """Route a NewMessage event.

        - If the message is for the active room, append it to MessageView.
        - Otherwise bump the unread count in RoomList.
        """
        msg = event.message
        if msg.room_id == self._active_room_id:
            self.query_one(MessageView).append_message(msg)
        else:
            # Bump unread for the room
            self._unread[msg.room_id] = self._unread.get(msg.room_id, 0) + 1
            # Update RoomList with the new unread count
            room_list = self.query_one(RoomList)
            updated: list[RoomSummary] = []
            for room in room_list.all_rooms:
                if room.room_id == msg.room_id:
                    updated.append(
                        RoomSummary(
                            room_id=room.room_id,
                            display_name=room.display_name,
                            unread_count=self._unread[msg.room_id],
                            last_activity=room.last_activity,
                            encrypted=room.encrypted,
                            tags=room.tags,
                        )
                    )
                else:
                    updated.append(room)
            room_list.set_rooms(updated)

    def handle_members_changed(self, event: MembersChanged) -> None:
        """Route a MembersChanged event to MemberList (active room only)."""
        if event.room_id == self._active_room_id:
            self.query_one(MemberList).set_members(event.members)

    # ------------------------------------------------------------------
    # RoomSelected handler (plan 0009)
    # ------------------------------------------------------------------

    def on_room_list_room_selected(self, message: RoomList.RoomSelected) -> None:
        """Load messages + members for the selected room and clear its unread."""
        room_id = message.room_id
        self._active_room_id = room_id

        # Clear unread for the selected room
        if room_id in self._unread:
            del self._unread[room_id]
        # Update RoomList to reflect cleared unread
        room_list = self.query_one(RoomList)
        updated = [
            RoomSummary(
                room_id=r.room_id,
                display_name=r.display_name,
                unread_count=0 if r.room_id == room_id else r.unread_count,
                last_activity=r.last_activity,
                encrypted=r.encrypted,
                tags=r.tags,
            )
            for r in room_list.all_rooms
        ]
        room_list.set_rooms(updated)
        room_list.set_active_room(room_id)

        # Load messages and members (async work in a worker)
        self.run_worker(
            self._load_room(room_id),
            exclusive=False,
        )

    async def _load_room(self, room_id: str) -> None:
        """Fetch messages and members for the given room."""
        await self.query_one(MessageView).load_room(room_id)
        self.query_one(MemberList).load_room(room_id)
