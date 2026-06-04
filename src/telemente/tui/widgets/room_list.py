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
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Input, Label, ListItem, ListView, Static

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

    def __init__(self, room: RoomSummary) -> None:
        super().__init__()
        self._room = room

    @property
    def room(self) -> RoomSummary:
        return self._room

    def compose(self) -> ComposeResult:
        room = self._room
        # Build display name with optional lock glyph
        name = f"\U0001f512 {room.display_name}" if room.encrypted else room.display_name
        yield Label(name, classes="room-name")
        if room.unread_count > 0:
            yield Label(f"({room.unread_count})", classes="room-unread-badge", id=None)


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

    def compose(self) -> ComposeResult:
        yield Input(id="room-search", placeholder="Search rooms…")
        yield ListView(id="room-list-view")
        yield Static(
            "No rooms match",
            id="room-list--empty-state",
            classes="room-empty-state",
        )

    def on_mount(self) -> None:
        self._sync_empty_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_rooms(self, rooms: list[RoomSummary]) -> None:
        """Replace the full room list and rebuild the visible view."""
        self._all_rooms = list(rooms)
        self._rebuild()

    def apply_filter(self, query: str) -> None:
        """Apply a case-insensitive substring filter on display_name."""
        self._filter = query
        self._rebuild()

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

        # Sort: rooms with a last_activity date come first (newest first),
        # then rooms with last_activity=None sorted alphabetically by name.
        rooms_with_dt = [r for r in filtered if r.last_activity is not None]
        rooms_without_dt = [r for r in filtered if r.last_activity is None]

        def _by_activity(r: RoomSummary) -> datetime:
            assert r.last_activity is not None  # narrowed above
            return r.last_activity

        rooms_with_dt.sort(key=_by_activity, reverse=True)
        rooms_without_dt.sort(key=lambda r: r.display_name)
        self._visible_rooms = rooms_with_dt + rooms_without_dt

        self._refresh_list()

    def _refresh_list(self) -> None:
        """Rebuild the ListView DOM to match _visible_rooms."""
        list_view = self.query_one("#room-list-view", ListView)
        list_view.clear()
        for room in self._visible_rooms:
            list_view.append(_RoomItem(room))
        self._sync_empty_state()

    def _sync_empty_state(self) -> None:
        """Show or hide the empty-state label."""
        empty = self.query_one("#room-list--empty-state", Static)
        has_items = len(self._visible_rooms) > 0 or len(self._all_rooms) == 0
        empty.display = not has_items

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        """Live-filter rooms as the user types in #room-search."""
        if event.input.id == "room-search":
            self.apply_filter(event.value)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Translate ListView.Selected into RoomList.RoomSelected."""
        if isinstance(event.item, _RoomItem):
            logger.debug("Room selected: %s", event.item.room.room_id)
            self.post_message(RoomList.RoomSelected(event.item.room.room_id))
