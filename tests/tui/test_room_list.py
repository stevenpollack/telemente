"""Tests for the RoomList widget (plan 0006).

All tests use a minimal host App that mounts RoomList directly.
No network — RoomSummary fixtures are built in-process.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from textual.app import App, ComposeResult

from telemente.matrix.models import RoomSummary
from telemente.tui.widgets.room_list import RoomList

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DT_OLD = datetime(2024, 1, 1, 12, 0, 0)
DT_NEW = datetime(2024, 6, 1, 12, 0, 0)
DT_MID = datetime(2024, 3, 1, 12, 0, 0)


def _room(
    room_id: str,
    display_name: str,
    unread_count: int = 0,
    last_activity: datetime | None = None,
    encrypted: bool = False,
) -> RoomSummary:
    return RoomSummary(
        room_id=room_id,
        display_name=display_name,
        unread_count=unread_count,
        last_activity=last_activity,
        encrypted=encrypted,
    )


# ---------------------------------------------------------------------------
# Host app
# ---------------------------------------------------------------------------


class HostApp(App[None]):
    """Minimal app that mounts a RoomList and records RoomSelected events."""

    def __init__(self) -> None:
        super().__init__()
        self.selected_room_ids: list[str] = []

    def compose(self) -> ComposeResult:
        yield RoomList(id="room-list")

    def on_room_list_room_selected(self, message: RoomList.RoomSelected) -> None:
        self.selected_room_ids.append(message.room_id)


# ---------------------------------------------------------------------------
# Test 1: set_rooms renders all rooms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_rooms_renders_all() -> None:
    app = HostApp()
    rooms = [
        _room("!a:h", "General"),
        _room("!b:h", "Random"),
        _room("!c:h", "Dev"),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        assert len(room_list.visible_rooms) == 3


# ---------------------------------------------------------------------------
# Test 2: filter substring is case-insensitive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filter_substring_case_insensitive() -> None:
    app = HostApp()
    rooms = [
        _room("!a:h", "General"),
        _room("!b:h", "Random"),
        _room("!c:h", "Dev"),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        room_list.apply_filter("ran")
        await pilot.pause()

        visible = room_list.visible_rooms
        assert len(visible) == 1
        assert visible[0].display_name == "Random"


# ---------------------------------------------------------------------------
# Test 3: clearing the filter restores all rooms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_filter_restores_all() -> None:
    app = HostApp()
    rooms = [
        _room("!a:h", "General"),
        _room("!b:h", "Random"),
        _room("!c:h", "Dev"),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        room_list.apply_filter("ran")
        await pilot.pause()
        assert len(room_list.visible_rooms) == 1

        room_list.apply_filter("")
        await pilot.pause()
        assert len(room_list.visible_rooms) == 3


# ---------------------------------------------------------------------------
# Test 4: no match shows empty-state, zero room items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_match_shows_empty_state() -> None:
    app = HostApp()
    rooms = [
        _room("!a:h", "General"),
        _room("!b:h", "Random"),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        room_list.apply_filter("zzz")
        await pilot.pause()

        assert len(room_list.visible_rooms) == 0
        # Empty-state label should be present in the DOM
        empty_labels = app.query("#room-list--empty-state")
        assert len(empty_labels) == 1
        assert empty_labels.first().display is True


# ---------------------------------------------------------------------------
# Test 5: visible_rooms sorted newest-first, None last
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sorted_by_recent_activity() -> None:
    app = HostApp()
    rooms = [
        _room("!old:h", "OldRoom", last_activity=DT_OLD),
        _room("!none:h", "NoActivity"),  # last_activity=None
        _room("!new:h", "NewRoom", last_activity=DT_NEW),
        _room("!mid:h", "MidRoom", last_activity=DT_MID),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        visible = room_list.visible_rooms
        assert len(visible) == 4
        assert visible[0].room_id == "!new:h"
        assert visible[1].room_id == "!mid:h"
        assert visible[2].room_id == "!old:h"
        assert visible[3].room_id == "!none:h"


# ---------------------------------------------------------------------------
# Test 6: selecting a room posts RoomSelected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_selecting_posts_roomselected() -> None:
    app = HostApp()
    rooms = [
        _room("!a:h", "Alpha", last_activity=DT_NEW),
        _room("!b:h", "Beta", last_activity=DT_OLD),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        # Focus the list, move to the first item (Alpha = newest), then select
        from textual.widgets import ListView

        list_view = room_list.query_one(ListView)
        list_view.focus()
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert len(app.selected_room_ids) == 1
        assert app.selected_room_ids[0] == "!a:h"


# ---------------------------------------------------------------------------
# Test 7: unread badge rendered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unread_badge_rendered() -> None:
    app = HostApp()
    rooms = [
        _room("!a:h", "Busy Room", unread_count=3),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        # Query the badge label inside the list item
        badges = app.query(".room-unread-badge")
        assert len(badges) == 1
        badge = badges.first()
        assert badge.display is True
        # The badge should contain "(3)"
        assert "(3)" in str(badge.render())
