"""RoomList widget — searchable, filterable room list panel (plan 0006).

Displays a list of Matrix rooms sorted by recent activity, with live
substring filtering, unread badges, and encryption indicators.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.events import Key
from textual.message import Message as TextualMessage
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

from telemente.matrix.models import RoomSummary
from telemente.matrix.sort import sort_rooms_by_recency

logger = logging.getLogger(__name__)


class RoomItem(ListItem):
    """A single room entry in the ListView."""

    DEFAULT_CSS = """
    RoomItem {
        height: auto;
        padding: 0 1;
    }
    RoomItem:hover {
        background: $boost;
    }
    RoomItem.-highlight {
        background: $accent 20%;
    }
    """

    class ContextMenuRequest(TextualMessage):
        """Posted when the user right-clicks a room item."""

        def __init__(self, room: RoomSummary, screen_x: int, screen_y: int) -> None:
            super().__init__()
            self.room = room
            self.screen_x = screen_x
            self.screen_y = screen_y

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
        yield Label(self._render_name(), markup=True, classes=self._name_classes())

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 3:
            return
        event.stop()
        self.post_message(
            RoomItem.ContextMenuRequest(
                room=self._room,
                screen_x=event.screen_x,
                screen_y=event.screen_y,
            )
        )

    def update_room(self, room: RoomSummary) -> None:
        """Mutate this item's data and re-render its label in place.

        If the item has not yet been composed (e.g. it was just appended
        via call_after_refresh and compose() hasn't run), the stored data is
        updated but the DOM is left alone — the next compose() pass will pick
        up _room and render correctly.
        """
        self._room = room
        labels = self.query(".room-name")
        if not labels:
            return
        label = labels.first(Label)
        label.update(self._render_name())
        # Sync CSS classes without forcing a full remount.
        new_classes = self._name_classes()
        label.set_classes(new_classes)

    def _render_name(self) -> str:
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
        return name

    def _name_classes(self) -> str:
        extra = " -unread" if self._room.unread_count > 0 else ""
        return f"room-name{extra}"


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
        logger.debug("set_rooms: %d rooms total", len(rooms))
        self._all_rooms = list(rooms)
        if not self._has_loaded and rooms:
            self._has_loaded = True
        self._rebuild()

    def set_active_room(self, room_id: str | None) -> None:
        """Highlight the RoomItem matching room_id; survives list rebuilds."""
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

        Mutates the matching RoomItem in place and updates _all_rooms and
        _visible_rooms so the count survives the next set_rooms() call.
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
        for item in self.query(RoomItem):
            if item.room.room_id == room_id:
                updated = next(r for r in self._all_rooms if r.room_id == room_id)
                item.update_room(updated)
                break

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

        self.call_after_refresh(self._refresh_list)
        self._sync_loading_state()
        self._sync_clear_button()

    def _refresh_list(self) -> None:
        """Sync the ListView DOM to match _visible_rooms with minimal mutation.

        If room order and membership are unchanged, patch each item in-place
        (no DOM churn). Otherwise do a full clear+rebuild wrapped in
        batch_update so Textual repaints exactly once.
        """
        list_view = self.query_one("#room-list-view", ListView)
        current_items = list(list_view.query(RoomItem))
        new_rooms = self._visible_rooms

        # Fast path: same rooms in the same order — just patch data in-place.
        if len(current_items) == len(new_rooms) and all(
            item.room.room_id == room.room_id
            for item, room in zip(current_items, new_rooms, strict=True)
        ):
            for item, room in zip(current_items, new_rooms, strict=True):
                item.update_room(room)
                if self._active_room_id == room.room_id:
                    item.add_class("-highlight")
                else:
                    item.remove_class("-highlight")
            self._sync_empty_state()
            return

        # Slow path: order or membership changed — rebuild once without flicker.
        with self.app.batch_update():  # pyright: ignore[reportUnknownMemberType]
            list_view.clear()
            for room in new_rooms:
                list_view.append(RoomItem(room, active=self._active_room_id == room.room_id))
        self._sync_empty_state()

    def _apply_active_highlight(self) -> None:
        """Re-apply -highlight to the active room (used by set_active_room)."""
        for item in self.query(RoomItem):
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

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Translate ListView.Selected into RoomList.RoomSelected."""
        if isinstance(event.item, RoomItem):
            logger.info("Room selected: %s", event.item.room.room_id)
            self.post_message(RoomList.RoomSelected(event.item.room.room_id))

    def on_room_item_context_menu_request(self, event: RoomItem.ContextMenuRequest) -> None:
        """Re-post the context menu request upward as RoomList.RoomContextMenu."""
        self.post_message(RoomList.RoomContextMenu(event.room, event.screen_x, event.screen_y))
