"""Main screen for telemente (plan 0005 / 0009 / 0014).

Three-panel layout: collapsible room list (left), tabbed message views (center),
collapsible member list (right).  Each selected room opens in its own tab (up to
TAB_CAP=8); the oldest tab is evicted LRU when the cap is exceeded.

Plan 0014: optional log viewer panel docked at the bottom, toggled via
Ctrl+\\ and the command palette.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol, cast

if TYPE_CHECKING:
    from telemente.tui.app import TelementeApp

from textual import events
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Tab, TabbedContent, TabPane

from telemente.config import Paths
from telemente.matrix.client import (
    MembersChanged,
    MessageRedacted,
    NewMessage,
    RoomsChanged,
    TypingChanged,
)
from telemente.matrix.models import Member, Message, RoomSummary
from telemente.tui.widgets.confirm_screen import ConfirmScreen
from telemente.tui.widgets.context_menu import ContextMenu, MenuEntry, MenuItem, MenuSeparator
from telemente.tui.widgets.log_panel import LogPanel
from telemente.tui.widgets.member_list import MemberList
from telemente.tui.widgets.message_view import MessageView
from telemente.tui.widgets.room_list import RoomList
from telemente.tui.widgets.thread_panel import ThreadPanel

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
        self,
        room_id: str,
        body: str,
        reply_to_event_id: str | None = None,
        thread_root_event_id: str | None = None,
    ) -> str: ...

    async def get_thread_messages(
        self, room_id: str, root_event_id: str, limit: int = 50
    ) -> tuple[list[Message], bool]: ...

    async def send_reaction(self, room_id: str, event_id: str, emoji: str) -> None: ...

    async def edit_message(self, room_id: str, event_id: str, new_body: str) -> str: ...

    async def redact_message(self, room_id: str, event_id: str, reason: str = "") -> None: ...

    async def search_messages(self, room_id: str, query: str) -> list[str]: ...

    def me(self) -> tuple[str, str]: ...

    def can_redact(self, room_id: str, target_sender: str) -> bool: ...

    def members(self, room_id: str) -> list[Member]: ...

    def rooms(self) -> list[RoomSummary]: ...

    async def set_room_tag(self, room_id: str, tag: str, order: float | None = None) -> None: ...

    async def remove_room_tag(self, room_id: str, tag: str) -> None: ...

    async def leave_room(self, room_id: str) -> None: ...


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

    # Context menu layer sits above all panels.
    LAYERS: ClassVar[tuple[str, ...]] = ("context-menu",)

    rooms_visible: reactive[bool] = reactive(True)
    members_visible: reactive[bool] = reactive(True)
    log_visible: reactive[bool] = reactive(False)
    thread_visible: reactive[bool] = reactive(False)

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
        # Active floating context menu (only one at a time).
        self._active_context_menu: ContextMenu | None = None

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
            yield ThreadPanel(self._client, id="thread-panel")
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

    def watch_thread_visible(self, visible: bool) -> None:
        self.query_one("#thread-panel").display = visible

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

    def on_thread_panel_close_requested(self, _: ThreadPanel.CloseRequested) -> None:
        self.close_thread()

    def on_message_view_open_thread(self, event: MessageView.OpenThread) -> None:
        self.open_thread(event.room_id, event.root_event_id)

    def open_thread(self, room_id: str, root_event_id: str) -> None:
        """Show the thread panel and load the given thread."""
        panel = self.query_one(ThreadPanel)
        panel.load_thread(room_id, root_event_id)
        self.thread_visible = True

    def close_thread(self) -> None:
        """Hide the thread panel."""
        self.thread_visible = False

    # ------------------------------------------------------------------
    # Context menu infrastructure (plan 0020)
    # ------------------------------------------------------------------

    def _show_context_menu(self, items: list[MenuEntry], screen_x: int, screen_y: int) -> None:
        """Mount a ContextMenu at the given screen coordinates.

        Bug 7 fix: clamp coordinates so the menu never overflows the terminal.
        """
        self._dismiss_context_menu()
        # Estimate menu dimensions. min-width is 24 from CSS; 2 for the border.
        menu_width = 26
        menu_height = len(items) + 2  # padding
        max_x = max(0, self.size.width - menu_width)
        max_y = max(0, self.size.height - menu_height)
        clamped_x = min(screen_x, max_x)
        clamped_y = min(screen_y, max_y)
        menu = ContextMenu(items, clamped_x, clamped_y)
        self._active_context_menu = menu
        self.mount(menu)

    def _dismiss_context_menu(self) -> None:
        """Remove the active context menu if one is shown."""
        if self._active_context_menu is not None:
            with contextlib.suppress(Exception):
                self._active_context_menu.remove()
            self._active_context_menu = None

    def on_click(self, event: events.Click) -> None:
        """Dismiss the active context menu on any outside click."""
        self._dismiss_context_menu()

    def on_context_menu_dismissed(self, _: ContextMenu.Dismissed) -> None:
        """Clear the reference when the menu dismisses itself."""
        self._active_context_menu = None

    def on_mouse_down(self, event: events.MouseDown) -> None:
        """Intercept right-click on Tab widgets before Tab._on_click fires."""
        if event.button != 3:
            return
        widget = event.widget
        while widget is not None:
            if isinstance(widget, Tab):
                event.stop()
                self._show_tab_context_menu(widget, event.screen_x, event.screen_y)
                return
            widget = widget.parent  # type: ignore[assignment]

    def _show_tab_context_menu(self, tab: Tab, screen_x: int, screen_y: int) -> None:
        """Build and show context menu for a TabbedContent tab.

        TabbedContent wraps pane IDs with '--content-tab-' prefix on the actual
        Tab widget ID, so we compare against pane IDs by stripping that prefix.
        """
        # Textual prefixes ContentTab IDs with '--content-tab-'; strip it.
        _PREFIX = "--content-tab-"
        raw_id = tab.id or ""
        pane_id = raw_id[len(_PREFIX) :] if raw_id.startswith(_PREFIX) else raw_id

        room_id: str | None = None
        for rid in self.open_tabs:
            if _tab_id(rid) == pane_id:
                room_id = rid
                break
        if room_id is None:
            return

        rid = room_id  # captured by closure

        def _close() -> None:
            self.run_worker(self.close_tab(rid), exclusive=False)

        items: list[MenuEntry] = [MenuItem("Close tab", _close)]
        self._show_context_menu(items, screen_x, screen_y)

    # ------------------------------------------------------------------
    # Message context menu handler (plan 0020)
    # ------------------------------------------------------------------

    def on_message_view_show_context_menu(self, event: MessageView.ShowContextMenu) -> None:
        self._show_context_menu(event.items, event.screen_x, event.screen_y)

    # ------------------------------------------------------------------
    # Room context menu handler (plan 0020)
    # ------------------------------------------------------------------

    def on_room_list_room_context_menu(self, event: RoomList.RoomContextMenu) -> None:
        room = event.room
        tags = room.tags
        room_id = room.room_id  # captured by closures

        def _toggle_fav() -> None:
            self._toggle_tag_for(room_id, "m.favourite")

        def _toggle_lp() -> None:
            self._toggle_tag_for(room_id, "m.lowpriority")

        def _toggle_mute() -> None:
            # m.mute is a de-facto standard used by Element and others; MSC2175 proposes
            # m.muted but is not yet merged. We use m.mute for compatibility.
            self._toggle_tag_for(room_id, "m.mute")

        def _leave() -> None:
            self._confirm_leave_room(room_id)

        items: list[MenuEntry] = [
            MenuItem(
                "★ Unfavourite" if "m.favourite" in tags else "★ Favourite",
                _toggle_fav,
            ),
            MenuItem(
                "↓ Remove low priority" if "m.lowpriority" in tags else "↓ Low priority",
                _toggle_lp,
            ),
            MenuItem(
                "🔕 Unmute" if "m.mute" in tags else "🔕 Mute",
                _toggle_mute,
            ),
            MenuSeparator(),
            MenuItem("Leave room", _leave),
        ]
        self._show_context_menu(items, event.screen_x, event.screen_y)

    def _toggle_tag_for(self, room_id: str, tag: str) -> None:
        """Toggle a room tag on/off; delegates the async work to a worker."""
        self.run_worker(
            self._do_toggle_tag(room_id, tag),
            exclusive=False,
            exit_on_error=False,
        )

    async def _do_toggle_tag(self, room_id: str, tag: str) -> None:
        """Async toggle: checks current tag state then set/remove."""
        room_list = self.query_one(RoomList)
        room = next((r for r in room_list.all_rooms if r.room_id == room_id), None)
        is_tagged = room is not None and tag in room.tags

        tag_labels = {
            "m.favourite": "favourite ★",
            "m.lowpriority": "low priority ↓",
            "m.mute": "mute 🔕",
        }
        label = tag_labels.get(tag, tag)

        try:
            if is_tagged:
                await self._client.remove_room_tag(room_id, tag)
                self.app.notify(f"Removed {label}", timeout=3)
            else:
                await self._client.set_room_tag(room_id, tag)
                self.app.notify(f"Set {label}", timeout=3)
        except Exception as exc:
            logger.warning("tag operation failed for %s %s: %s", tag, room_id, exc)
            self.app.notify(f"Tag operation failed: {exc}", severity="error")

    def _confirm_leave_room(self, room_id: str) -> None:
        """Push the confirmation modal for leaving a room."""
        try:
            room_list = self.query_one(RoomList)
            display_name = next(
                (r.display_name for r in room_list.all_rooms if r.room_id == room_id),
                room_id,
            )
        except Exception:
            display_name = room_id

        def _on_confirmed(confirmed: bool | None) -> None:
            if confirmed:
                self.run_worker(
                    self._do_leave(room_id, display_name),
                    exclusive=False,
                    exit_on_error=False,
                )

        self.app.push_screen(
            ConfirmScreen(f"Leave '{display_name}'?"),
            _on_confirmed,
        )

    async def _do_leave(self, room_id: str, display_name: str) -> None:
        """Leave the room and notify the user."""
        try:
            await self._client.leave_room(room_id)
            self.app.notify(f"Left {display_name}", severity="information")
        except Exception as exc:
            logger.warning("leave_room failed for %s: %s", room_id, exc)
            self.app.notify(f"Failed to leave room: {exc}", severity="error")

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
        # If the active room's member list is empty, the initial load raced ahead
        # of the first sync. Reload both members and messages now that room state
        # is populated. Skip if the active room is itself being departed.
        active = self.active_room_id
        member_list = self.query_one(MemberList)
        if active is not None and active not in departed and member_list.member_count == 0:
            logger.debug(
                "handle_rooms_changed: patching active room=%s after sync populated room state",
                active,
            )
            member_list.load_room(active)
            view = self.message_view_for(active)
            if view is not None:
                names = {m.user_id: m.display_name for m in self._client.members(active)}
                view.patch_sender_names(names)

    def handle_new_message(self, event: NewMessage) -> None:
        msg = event.message
        active_room = self.active_room_id
        logger.info(
            "handle_new_message: room=%s sender=%s active=%s",
            msg.room_id,
            msg.sender,
            msg.room_id == active_room,
        )
        # Forward matching thread messages to the open ThreadPanel.
        if self.thread_visible and msg.thread_root_id is not None:
            panel = self.query_one(ThreadPanel)
            if msg.thread_root_id == panel._root_event_id and msg.room_id == panel._room_id:  # pyright: ignore[reportPrivateUsage]
                panel.append_message(msg)

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

    def handle_redaction(self, event: MessageRedacted) -> None:
        """Remove a redacted message row from the open MessageView, if visible."""
        logger.debug("handle_redaction: room=%s event_id=%s", event.room_id, event.event_id)
        view = self.message_view_for(event.room_id)
        if view is not None:
            view.remove_message(event.event_id)

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
