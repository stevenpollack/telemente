"""RoomList widget — searchable, filterable room list panel (plan 0006).

Displays a list of Matrix rooms sorted by recent activity, with live
substring filtering, unread badges, and encryption indicators.

Plan 0012: migrated from ListView/RoomItem(ListItem) to OptionList/Option
for synchronous, flicker-free DOM updates.
"""

from __future__ import annotations

import contextlib
import logging
import re
from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.events import Key
from textual.message import Message as TextualMessage
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option, OptionDoesNotExist

from telemente.matrix.models import RoomSummary
from telemente.matrix.sort import sort_rooms_by_recency

logger = logging.getLogger(__name__)

_INVALID_ID_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def _option_id(room_id: str) -> str:
    """Map a room_id to a valid CSS/option id string."""
    return "opt-room-" + _INVALID_ID_CHARS.sub("-", room_id)


def _render_name(room: RoomSummary) -> str:
    """Render the display name for a room, including badges and markup."""
    name = f"\U0001f512 {room.display_name}" if room.encrypted else room.display_name
    if "m.favourite" in room.tags:
        name = f"★ {name}"
    if "m.lowpriority" in room.tags:
        name = f"{name} ↓"
    if "m.mute" in room.tags:
        name = f"{name} 🔕"
    if room.unread_count > 0:
        name = f"[bold]{name} ({room.unread_count})[/bold]"
    return name


class RoomList(Widget):
    """Searchable, filterable room list.

    Public API
    ----------
    set_rooms(rooms)    Replace the full room list and rebuild the view.
    apply_filter(query) Case-insensitive substring filter on display_name.
    visible_rooms       Current filtered + sorted list.
    """

    BINDINGS: ClassVar[list[BindingType]] = []

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    class RoomSelected(TextualMessage):
        """Posted when the user selects a room (Enter / click)."""

        def __init__(self, room_id: str) -> None:
            super().__init__()
            self.room_id = room_id

    class RoomContextMenu(TextualMessage):
        """Posted when the user right-clicks a room item; bubbles to MainScreen."""

        def __init__(self, room: RoomSummary, screen_x: int, screen_y: int) -> None:
            super().__init__()
            self.room = room
            self.screen_x = screen_x
            self.screen_y = screen_y

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._all_rooms: list[RoomSummary] = []
        self._filter: str = ""
        self._visible_rooms: list[RoomSummary] = []
        self._has_loaded: bool = False
        self._active_room_id: str | None = None
        self._sort_mode: str = "recent"  # "recent" | "alpha"
        self._filter_timer: Timer | None = None
        self.pending_filter: str = ""
        # side-table: option_id -> original room_id (mapping is not always invertible)
        self._opt_to_room: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="search-bar"):
            yield Input(id="room-search", placeholder="Search rooms…")
            yield Button("✕", id="clear-search")
        yield Static(
            "Syncing…",
            id="room-list--loading",
            classes="room-loading-state",
        )
        yield OptionList(id="room-list-view")
        yield Static(
            "No rooms match",
            id="room-list--empty-state",
            classes="room-empty-state",
        )

    def on_mount(self) -> None:
        self._sync_loading_state()
        self._sync_empty_state()
        self._sync_clear_button()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_rooms(self, rooms: list[RoomSummary]) -> None:
        """Replace the full room list and rebuild the visible view."""
        logger.debug("set_rooms: %d rooms total", len(rooms))
        self._all_rooms = list(rooms)
        if not self._has_loaded and rooms:
            self._has_loaded = True
        self._rebuild()

    def set_active_room(self, room_id: str | None) -> None:
        """Highlight the option matching room_id; survives list rebuilds."""
        self._active_room_id = room_id
        self._apply_active_highlight()

    def apply_filter(self, query: str) -> None:
        """Apply a case-insensitive substring filter on display_name."""
        self._filter = query
        self._rebuild()

    def set_sort_mode(self, mode: str) -> None:
        """Set sort order: 'recent' (newest first) or 'alpha' (A-Z)."""
        logger.info("set_sort_mode: %r (was %r)", mode, self._sort_mode)
        self._sort_mode = mode
        self._rebuild()

    def update_unread(self, room_id: str, count: int) -> None:
        """Update the unread count for a single room without a full list rebuild.

        Uses replace_option_prompt for a surgical patch — no clear_options.
        """

        def _patch(rooms: list[RoomSummary]) -> list[RoomSummary]:
            return [
                RoomSummary(
                    room_id=r.room_id,
                    display_name=r.display_name,
                    unread_count=count if r.room_id == room_id else r.unread_count,
                    last_activity=r.last_activity,
                    encrypted=r.encrypted,
                    tags=r.tags,
                )
                if r.room_id == room_id
                else r
                for r in rooms
            ]

        self._all_rooms = _patch(self._all_rooms)
        self._visible_rooms = _patch(self._visible_rooms)

        ol = self.query_one("#room-list-view", OptionList)
        oid = _option_id(room_id)
        updated = next((r for r in self._visible_rooms if r.room_id == room_id), None)
        if updated is not None:
            with contextlib.suppress(OptionDoesNotExist):
                ol.replace_option_prompt(oid, _render_name(updated))

    @property
    def all_rooms(self) -> list[RoomSummary]:
        """Full unfiltered room list."""
        return list(self._all_rooms)

    @property
    def visible_rooms(self) -> list[RoomSummary]:
        """Current filtered + sorted list."""
        return list(self._visible_rooms)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rebuild(self) -> None:
        """Recompute visible_rooms from the full list and current filter."""
        q = self._filter.casefold()
        if q:
            filtered = [r for r in self._all_rooms if q in r.display_name.casefold()]
        else:
            filtered = list(self._all_rooms)

        if self._sort_mode == "alpha":
            self._visible_rooms = sorted(filtered, key=lambda r: r.display_name.casefold())
        else:
            self._visible_rooms = sort_rooms_by_recency(filtered)

        self._refresh_list()
        self._sync_loading_state()
        self._sync_clear_button()

    def _refresh_list(self) -> None:
        """Sync the OptionList DOM to match _visible_rooms.

        Synchronous — no call_after_refresh deferred callback.
        Rebuilds the full option list on every call.
        """
        ol = self.query_one("#room-list-view", OptionList)
        self._opt_to_room.clear()
        ol.clear_options()
        for room in self._visible_rooms:
            oid = _option_id(room.room_id)
            self._opt_to_room[oid] = room.room_id
            ol.add_option(Option(_render_name(room), id=oid))
        self._apply_active_highlight()
        self._sync_empty_state()

    def _apply_active_highlight(self) -> None:
        """Set ol.highlighted to the active room's index."""
        if self._active_room_id is None:
            return
        ol = self.query_one("#room-list-view", OptionList)
        oid = _option_id(self._active_room_id)
        with contextlib.suppress(OptionDoesNotExist):
            ol.highlighted = ol.get_option_index(oid)

    def _sync_loading_state(self) -> None:
        """Show 'Syncing…' until the first batch of rooms arrives."""
        loading = self.query_one("#room-list--loading", Static)
        loading.display = not self._has_loaded

    def _sync_empty_state(self) -> None:
        """Show or hide the empty-state label."""
        empty = self.query_one("#room-list--empty-state", Static)
        has_items = len(self._visible_rooms) > 0 or len(self._all_rooms) == 0
        empty.display = not has_items and self._has_loaded

    def _sync_clear_button(self) -> None:
        """Show the ✕ button only when the search input is non-empty."""
        btn = self.query_one("#clear-search", Button)
        btn.display = bool(self._filter)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        """Live-filter rooms as the user types in #room-search (debounced 150ms)."""
        if event.input.id != "room-search":
            return
        logger.debug("on_input_changed: filter=%r", event.value)
        self.pending_filter = event.value
        if self._filter_timer is not None:
            self._filter_timer.stop()
        self._filter_timer = self.set_timer(0.15, self.apply_pending_filter)

    def apply_pending_filter(self) -> None:
        self._filter_timer = None
        self.apply_filter(self.pending_filter)

    def on_key(self, event: Key) -> None:
        """ESC in the search input clears the filter."""
        if event.key == "escape":
            search_input = self.query_one("#room-search", Input)
            if search_input.has_focus and search_input.value:
                search_input.clear()
                event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Clear the search filter when the ✕ button is pressed."""
        if event.button.id == "clear-search":
            search_input = self.query_one("#room-search", Input)
            search_input.clear()
            search_input.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Translate OptionList.OptionSelected into RoomList.RoomSelected."""
        room_id = self._opt_to_room.get(event.option.id or "")
        if room_id:
            logger.info("Room selected: %s", room_id)
            self.post_message(RoomList.RoomSelected(room_id))

    def on_mouse_down(self, event: events.MouseDown) -> None:
        """Right-click on the OptionList posts RoomList.RoomContextMenu.

        on_option_list_mouse_down would not be called — MouseDown is a core
        event, not a Message subclass, so Textual's node-scoped handler naming
        does not apply.  We use on_mouse_down and check that the click landed
        on the OptionList by reading the Rich strip metadata embedded by the
        OptionList renderer (event.style.meta["option"] == option index).
        """
        if event.button != 3:
            return
        # The option index is embedded by OptionList in the Rich strip metadata
        # the same way it is for Click and MouseMove events.
        idx: int | None = event.style.meta.get("option")
        if idx is None or idx >= len(self._visible_rooms):
            return
        event.stop()
        room = self._visible_rooms[idx]
        self.post_message(RoomList.RoomContextMenu(room, event.screen_x, event.screen_y))
