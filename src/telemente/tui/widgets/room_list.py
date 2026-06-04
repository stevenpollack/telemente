"""RoomList widget — searchable, filterable room list panel (plan 0006).

Displays a list of Matrix rooms sorted by recent activity, with live
substring filtering, unread badges, and encryption indicators.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.events import Key
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

from telemente.matrix.models import RoomSummary

logger = logging.getLogger(__name__)


class _RoomItem(ListItem):
    """A single room entry in the ListView."""

    DEFAULT_CSS = """
    _RoomItem {
        height: auto;
        padding: 0 1;
    }
    _RoomItem:hover {
        background: $boost;
    }
    _RoomItem.-highlight {
        background: $accent 20%;
    }
    """

    def __init__(self, room: RoomSummary, *, active: bool = False) -> None:
        super().__init__()
        self._room = room
        self._active = active

    def on_mount(self) -> None:
        if self._active:
            self.call_after_refresh(self.add_class, "-highlight")

    @property
    def room(self) -> RoomSummary:
        return self._room

    def compose(self) -> ComposeResult:
        room = self._room
        name = f"\U0001f512 {room.display_name}" if room.encrypted else room.display_name
        if "m.favourite" in room.tags:
            name = f"★ {name}"
        if "m.lowpriority" in room.tags:
            name = f"{name} ↓"
        if "m.mute" in room.tags:
            name = f"{name} 🔕"
        if room.unread_count > 0:
            name = f"[bold]{name} ({room.unread_count})[/bold]"
        yield Label(name, markup=True, classes="room-name")


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
    # Message
    # ------------------------------------------------------------------

    class RoomSelected(TextualMessage):
        """Posted when the user selects a room (Enter / click)."""

        def __init__(self, room_id: str) -> None:
            super().__init__()
            self.room_id = room_id

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

    def compose(self) -> ComposeResult:
        with Horizontal(id="search-bar"):
            yield Input(id="room-search", placeholder="Search rooms…")
            yield Button("✕", id="clear-search")
        yield Static(
            "Syncing…",
            id="room-list--loading",
            classes="room-loading-state",
        )
        yield ListView(id="room-list-view")
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
        self._all_rooms = list(rooms)
        if not self._has_loaded and rooms:
            self._has_loaded = True
        self._rebuild()

    def set_active_room(self, room_id: str | None) -> None:
        """Highlight the _RoomItem matching room_id; survives list rebuilds."""
        self._active_room_id = room_id
        self._apply_active_highlight()

    def apply_filter(self, query: str) -> None:
        """Apply a case-insensitive substring filter on display_name."""
        self._filter = query
        self._rebuild()

    def set_sort_mode(self, mode: str) -> None:
        """Set sort order: 'recent' (newest first) or 'alpha' (A-Z)."""
        self._sort_mode = mode
        self._rebuild()

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
            # "recent": rooms with a timestamp newest-first, then no-timestamp A-Z.
            rooms_with_dt = [r for r in filtered if r.last_activity is not None]
            rooms_without_dt = [r for r in filtered if r.last_activity is None]

            def _by_activity(r: RoomSummary) -> datetime:
                assert r.last_activity is not None
                return r.last_activity

            rooms_with_dt.sort(key=_by_activity, reverse=True)
            rooms_without_dt.sort(key=lambda r: r.display_name)
            self._visible_rooms = rooms_with_dt + rooms_without_dt

        self._refresh_list()
        self._sync_loading_state()
        self._sync_clear_button()

    def _refresh_list(self) -> None:
        """Rebuild the ListView DOM to match _visible_rooms."""
        list_view = self.query_one("#room-list-view", ListView)
        list_view.clear()
        for room in self._visible_rooms:
            active = self._active_room_id is not None and room.room_id == self._active_room_id
            list_view.append(_RoomItem(room, active=active))
        self._sync_empty_state()

    def _apply_active_highlight(self) -> None:
        """Re-apply -highlight to the active room (used by set_active_room)."""
        for item in self.query(_RoomItem):
            if self._active_room_id is not None and item.room.room_id == self._active_room_id:
                item.add_class("-highlight")
            else:
                item.remove_class("-highlight")

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
        """Live-filter rooms as the user types in #room-search."""
        if event.input.id == "room-search":
            self.apply_filter(event.value)

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

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Translate ListView.Selected into RoomList.RoomSelected."""
        if isinstance(event.item, _RoomItem):
            logger.debug("Room selected: %s", event.item.room.room_id)
            self.post_message(RoomList.RoomSelected(event.item.room.room_id))
