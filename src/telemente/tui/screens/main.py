"""Main screen for telemente (plan 0005 / 0009).

Three-panel layout: collapsible room list (left), tabbed message views (center),
collapsible member list (right).  Each selected room opens in its own tab (up to
TAB_CAP=8); the oldest tab is evicted LRU when the cap is exceeded.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from typing import ClassVar, Protocol

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, TabbedContent, TabPane

from telemente.matrix.client import MembersChanged, NewMessage, RoomsChanged
from telemente.matrix.models import Member, Message, RoomSummary
from telemente.tui.widgets.member_list import MemberList
from telemente.tui.widgets.message_view import MessageView
from telemente.tui.widgets.room_list import RoomList

logger = logging.getLogger(__name__)

TAB_CAP = 8

# IDs in Textual must match [a-zA-Z0-9_-]+; convert room_id characters.
_INVALID_ID_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def _tab_id(room_id: str) -> str:
    """Stable, valid Textual widget ID derived from a Matrix room_id."""
    safe = _INVALID_ID_CHARS.sub("-", room_id)
    return f"tab-room-{safe}"


class _MainClient(Protocol):
    """Structural protocol for the subset of MatrixClient used by MainScreen."""

    async def messages(self, room_id: str, limit: int = 50) -> list[Message]: ...

    async def send_text(
        self, room_id: str, body: str, reply_to_event_id: str | None = None
    ) -> str: ...

    async def send_reaction(self, room_id: str, event_id: str, emoji: str) -> None: ...

    async def edit_message(self, room_id: str, event_id: str, new_body: str) -> str: ...

    async def redact_message(self, room_id: str, event_id: str, reason: str = "") -> None: ...

    def me(self) -> tuple[str, str]: ...

    def members(self, room_id: str) -> list[Member]: ...

    def rooms(self) -> list[RoomSummary]: ...


# ---------------------------------------------------------------------------
# MainScreen
# ---------------------------------------------------------------------------


class MainScreen(Screen[None]):
    """Three-panel main screen: room list | tabbed message views | member list."""

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
        # LRU-ordered dict: room_id -> display_name; oldest first.
        self._open_tabs: OrderedDict[str, str] = OrderedDict()
        # Track unread counts by room_id.
        self._unread: dict[str, int] = {}

    @property
    def active_room_id(self) -> str | None:
        """The currently active room ID (frontmost tab), or None."""
        tc = self.query_one(TabbedContent)
        active = tc.active
        if not active:
            return None
        for room_id in self._open_tabs:
            if _tab_id(room_id) == active:
                return room_id
        return None

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-layout"):
            yield RoomList(id="rooms-panel")
            with TabbedContent(id="message-panel"):
                pass
            yield MemberList(self._client, id="members-panel")
        yield Footer()

    def on_mount(self) -> None:
        logger.debug("MainScreen mounted")
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
        self.rooms_visible = not self.rooms_visible

    def action_toggle_members(self) -> None:
        self.members_visible = not self.members_visible

    def action_focus_search(self) -> None:
        self.query_one("#room-search", Input).focus()

    # ------------------------------------------------------------------
    # Client event handlers (plan 0009)
    # ------------------------------------------------------------------

    def handle_rooms_changed(self, event: RoomsChanged) -> None:
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
        msg = event.message
        active_room = self.active_room_id
        if msg.room_id == active_room:
            # Route to the active tab's MessageView
            view = self._message_view_for(msg.room_id)
            if view is not None:
                view.append_message(msg)
        else:
            # Bump unread and show a toast if the room has an open tab
            self._unread[msg.room_id] = self._unread.get(msg.room_id, 0) + 1
            room_list = self.query_one(RoomList)
            updated: list[RoomSummary] = []
            display_name = msg.room_id
            for room in room_list.all_rooms:
                if room.room_id == msg.room_id:
                    display_name = room.display_name
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
            # Toast only when the room is in an open (but non-active) tab
            if msg.room_id in self._open_tabs:
                self.app.notify(
                    f"{msg.sender_display_name}: {msg.body[:60]}",
                    title=display_name,
                    severity="information",
                    timeout=4,
                )

    def handle_members_changed(self, event: MembersChanged) -> None:
        if event.room_id == self.active_room_id:
            self.query_one(MemberList).set_members(event.members)

    # ------------------------------------------------------------------
    # RoomSelected handler
    # ------------------------------------------------------------------

    def on_room_list_room_selected(self, message: RoomList.RoomSelected) -> None:
        room_id = message.room_id
        tid = _tab_id(room_id)
        tc = self.query_one(TabbedContent)

        if room_id in self._open_tabs:
            # Tab exists — just focus it and refresh the highlight
            self._open_tabs.move_to_end(room_id)
            tc.active = tid
            self._sync_room_highlight()
            return

        # Evict oldest tab if at cap
        if len(self._open_tabs) >= TAB_CAP:
            oldest_room_id, _ = next(iter(self._open_tabs.items()))
            oldest_tid = _tab_id(oldest_room_id)
            self.run_worker(self._remove_tab(tc, oldest_tid), exclusive=False)
            del self._open_tabs[oldest_room_id]

        # Find display name from room list
        room_list = self.query_one(RoomList)
        display_name = next(
            (r.display_name for r in room_list.all_rooms if r.room_id == room_id),
            room_id,
        )
        self._open_tabs[room_id] = display_name

        # Clear unread for this room
        if room_id in self._unread:
            del self._unread[room_id]
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
        room_list.set_active_room(room_id)
        room_list.set_rooms(updated)

        # Open the tab and load content
        self.run_worker(
            self._open_tab(tc, room_id, tid, display_name),
            exclusive=False,
        )

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Sync room highlight when the user switches tabs manually."""
        self._sync_room_highlight()
        active = self.active_room_id
        if active is not None:
            self.query_one(RoomList).set_active_room(active)
            self.query_one(MemberList).load_room(active)

    # ------------------------------------------------------------------
    # Async helpers
    # ------------------------------------------------------------------

    async def close_tab(self, room_id: str) -> None:
        """Close the tab for room_id (no-op if not open)."""
        if room_id not in self._open_tabs:
            return
        tid = _tab_id(room_id)
        del self._open_tabs[room_id]
        await self.query_one(TabbedContent).remove_pane(tid)
        self._sync_room_highlight()

    async def _open_tab(self, tc: TabbedContent, room_id: str, tid: str, display_name: str) -> None:
        view = MessageView(self._client, id=f"mv-{tid}")
        pane = TabPane(display_name, view, id=tid)
        await tc.add_pane(pane)
        tc.active = tid
        self._sync_room_highlight()
        await view.load_room(room_id)
        self.query_one(MemberList).load_room(room_id)

    async def _remove_tab(self, tc: TabbedContent, tid: str) -> None:
        await tc.remove_pane(tid)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _message_view_for(self, room_id: str) -> MessageView | None:
        tid = _tab_id(room_id)
        try:
            return self.query_one(f"#mv-{tid}", MessageView)
        except Exception:
            return None

    def _sync_room_highlight(self) -> None:
        room_list = self.query_one(RoomList)
        room_list.set_active_room(self.active_room_id)
