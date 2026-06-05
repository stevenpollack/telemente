"""Main screen for telemente (plan 0005 / 0009 / 0014).

Three-panel layout: collapsible room list (left), tabbed message views (center),
collapsible member list (right).  Each selected room opens in its own tab (up to
TAB_CAP=8); the oldest tab is evicted LRU when the cap is exceeded.

Plan 0014: optional log viewer panel docked at the bottom, toggled via
Ctrl+\\ and the command palette.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol, cast

if TYPE_CHECKING:
    from telemente.tui.app import TelementeApp

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, TabbedContent, TabPane

from telemente.config import Paths
from telemente.matrix.client import MembersChanged, NewMessage, RoomsChanged, TypingChanged
from telemente.matrix.models import Member, Message, RoomSummary
from telemente.tui.widgets.log_panel import LogPanel
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
        ("ctrl+backslash", "toggle_log", "Log"),
    ]

    rooms_visible: reactive[bool] = reactive(True)
    members_visible: reactive[bool] = reactive(True)
    log_visible: reactive[bool] = reactive(False)

    @property
    def app(self) -> TelementeApp:  # type: ignore[override]
        return cast("TelementeApp", super().app)  # pyright: ignore[reportUnknownMemberType]

    def __init__(
        self,
        client: _MainClient,
        *,
        log_file: Path | None = None,
    ) -> None:
        super().__init__()
        self._client = client
        self._log_file: Path = log_file or (Paths.default().data_dir / "telemente.log")
        # LRU-ordered dict: room_id -> display_name; oldest first.
        self.open_tabs: OrderedDict[str, str] = OrderedDict()
        # Track unread counts by room_id.
        self.unread: dict[str, int] = {}

    @property
    def active_room_id(self) -> str | None:
        """The currently active room ID (frontmost tab), or None."""
        try:
            tc = self.query_one(TabbedContent)
        except Exception:
            return None
        active = tc.active
        if not active:
            return None
        for room_id in self.open_tabs:
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
        yield LogPanel(self._log_file, id="log-panel")
        yield Footer()

    def on_mount(self) -> None:
        logger.info("MainScreen mounted")
        rooms = self._client.rooms()
        if rooms:
            logger.info("MainScreen.on_mount: pre-loading %d rooms from client", len(rooms))
            self.query_one(RoomList).set_rooms(rooms)

    # ------------------------------------------------------------------
    # Reactive watchers
    # ------------------------------------------------------------------

    def watch_rooms_visible(self, visible: bool) -> None:
        self.query_one("#rooms-panel").display = visible

    def watch_members_visible(self, visible: bool) -> None:
        self.query_one("#members-panel").display = visible

    def watch_log_visible(self, visible: bool) -> None:
        self.query_one("#log-panel").display = visible

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_toggle_rooms(self) -> None:
        self.rooms_visible = not self.rooms_visible

    def action_toggle_members(self) -> None:
        self.members_visible = not self.members_visible

    def action_toggle_log(self) -> None:
        self.log_visible = not self.log_visible

    def action_focus_search(self) -> None:
        self.query_one("#room-search", Input).focus()

    # ------------------------------------------------------------------
    # LogPanel events
    # ------------------------------------------------------------------

    def on_log_panel_close_requested(self, _: LogPanel.CloseRequested) -> None:
        self.log_visible = False

    # ------------------------------------------------------------------
    # Client event handlers (plan 0009)
    # ------------------------------------------------------------------

    def handle_rooms_changed(self, event: RoomsChanged) -> None:
        logger.info("handle_rooms_changed: %d rooms", len(event.rooms))
        incoming_ids = {r.room_id for r in event.rooms}
        rooms_with_unread = [
            RoomSummary(
                room_id=r.room_id,
                display_name=r.display_name,
                unread_count=self.unread.get(r.room_id, r.unread_count),
                last_activity=r.last_activity,
                encrypted=r.encrypted,
                tags=r.tags,
            )
            for r in event.rooms
        ]
        self.query_one(RoomList).set_rooms(rooms_with_unread)
        # Close tabs for rooms that are no longer in the list (leave/kick/ban).
        departed = [rid for rid in list(self.open_tabs) if rid not in incoming_ids]
        for rid in departed:
            self.run_worker(self.close_tab(rid), exclusive=False)

    def handle_new_message(self, event: NewMessage) -> None:
        msg = event.message
        active_room = self.active_room_id
        logger.info(
            "handle_new_message: room=%s sender=%s active=%s",
            msg.room_id,
            msg.sender,
            msg.room_id == active_room,
        )
        if msg.room_id == active_room:
            # Route to the active tab's MessageView
            view = self.message_view_for(msg.room_id)
            if view is not None:
                view.append_message(msg)
        else:
            # Bump unread counter and patch the room-list item in place —
            # no full rebuild needed for a single unread count change.
            self.unread[msg.room_id] = self.unread.get(msg.room_id, 0) + 1
            room_list = self.query_one(RoomList)
            room_list.update_unread(msg.room_id, self.unread[msg.room_id])
            # Resolve display_name for the toast (cheaply, from cached all_rooms).
            display_name = next(
                (r.display_name for r in room_list.all_rooms if r.room_id == msg.room_id),
                msg.room_id,
            )
            # Toast only when the room is in an open (but non-active) tab
            if msg.room_id in self.open_tabs:
                self.app.notify(
                    f"{msg.sender_display_name}: {msg.body[:60]}",
                    title=display_name,
                    severity="information",
                    timeout=4,
                )

    def handle_members_changed(self, event: MembersChanged) -> None:
        logger.debug(
            "handle_members_changed: room=%s members=%d",
            event.room_id,
            len(event.members),
        )
        if event.room_id == self.active_room_id:
            self.query_one(MemberList).set_members(event.members)

    def handle_typing_changed(self, event: TypingChanged) -> None:
        logger.debug("handle_typing_changed: room=%s users=%s", event.room_id, event.user_ids)
        view = self.message_view_for(event.room_id)
        if view is not None:
            view.set_typing(event.room_id, event.user_ids)

    # ------------------------------------------------------------------
    # RoomSelected handler
    # ------------------------------------------------------------------

    def _clear_unread(self, room_id: str) -> None:
        """Zero the unread badge for room_id in local state and the RoomList.

        Clears both the local ``_unread`` counter (incremented by
        ``handle_new_message``) and the RoomList widget so that any unread
        count carried in the last ``set_rooms`` payload is also zeroed.
        """
        self.unread.pop(room_id, None)
        self.query_one(RoomList).update_unread(room_id, 0)

    def on_room_list_room_selected(self, message: RoomList.RoomSelected) -> None:
        room_id = message.room_id
        logger.info("on_room_list_room_selected: room_id=%s", room_id)
        tid = _tab_id(room_id)
        tc = self.query_one(TabbedContent)

        if room_id in self.open_tabs:
            # Tab exists — just focus it and refresh the highlight
            self.open_tabs.move_to_end(room_id)
            tc.active = tid
            self._clear_unread(room_id)
            self._sync_room_highlight()
            return

        # Determine eviction before mutating state.
        evict_tid: str | None = None
        if len(self.open_tabs) >= TAB_CAP:
            oldest_room_id, _ = next(iter(self.open_tabs.items()))
            evict_tid = _tab_id(oldest_room_id)
            del self.open_tabs[oldest_room_id]

        # Find display name from room list
        room_list = self.query_one(RoomList)
        display_name = next(
            (r.display_name for r in room_list.all_rooms if r.room_id == room_id),
            room_id,
        )
        self.open_tabs[room_id] = display_name

        room_list.set_active_room(room_id)
        self._clear_unread(room_id)

        # Open the tab (and evict if needed) in one exclusive worker so that
        # remove_pane always completes before add_pane, and concurrent room
        # selections are serialised — the last one wins for MemberList.
        self.run_worker(
            self._open_tab(tc, room_id, tid, display_name, evict_tid=evict_tid),
            exclusive=True,
        )

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Sync room highlight when the user switches tabs manually."""
        self._sync_room_highlight()
        active = self.active_room_id
        if active is not None:
            self.query_one(RoomList).set_active_room(active)
            self.query_one(MemberList).load_room(active)
            self._clear_unread(active)

    # ------------------------------------------------------------------
    # Async helpers
    # ------------------------------------------------------------------

    async def close_tab(self, room_id: str) -> None:
        """Close the tab for room_id (no-op if not open)."""
        if room_id not in self.open_tabs:
            return
        tid = _tab_id(room_id)
        del self.open_tabs[room_id]
        tc = self.query_one(TabbedContent)
        await tc.remove_pane(tid)
        self._sync_room_highlight()

    async def _open_tab(
        self,
        tc: TabbedContent,
        room_id: str,
        tid: str,
        display_name: str,
        *,
        evict_tid: str | None = None,
    ) -> None:
        # Evict first so we never exceed TAB_CAP.
        if evict_tid is not None:
            await tc.remove_pane(evict_tid)
        view = MessageView(self._client, id=f"mv-{tid}")
        pane = TabPane(display_name, view, id=tid)
        await tc.add_pane(pane)
        tc.active = tid
        self._sync_room_highlight()
        await view.load_room(room_id)
        # Guard against a tab switch that happened while load_room was awaiting.
        if self.active_room_id == room_id:
            self.query_one(MemberList).load_room(room_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def message_view_for(self, room_id: str) -> MessageView | None:
        tid = _tab_id(room_id)
        try:
            return self.query_one(f"#mv-{tid}", MessageView)
        except Exception:
            return None

    def _sync_room_highlight(self) -> None:
        room_list = self.query_one(RoomList)
        room_list.set_active_room(self.active_room_id)
