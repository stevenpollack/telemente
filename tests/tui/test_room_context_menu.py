"""Tests for room list right-click context menu (plan 0020, Part 4).

Tier-2 tests: FakeMatrixClient with rooms_data; spy on set_tags/removed_tags/left_rooms.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label, Static

import fakes as fakes_module
from telemente.matrix.models import RoomSummary
from telemente.tui.screens.main import MainScreen
from telemente.tui.widgets.confirm_screen import ConfirmScreen
from telemente.tui.widgets.context_menu import ContextMenu
from telemente.tui.widgets.room_list import RoomItem, RoomList

FakeMatrixClient = fakes_module.FakeMatrixClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _room(
    room_id: str,
    name: str,
    tags: dict[str, float | None] | None = None,
) -> RoomSummary:
    return RoomSummary(room_id=room_id, display_name=name, tags=tags or {})


class HostApp(App[None]):
    def __init__(self, client: FakeMatrixClient) -> None:
        super().__init__()
        self._client = client

    def compose(self) -> ComposeResult:
        yield Label("host")

    def on_mount(self) -> None:
        self.push_screen(MainScreen(self._client))


def _menu_item_labels(app: App[None]) -> list[str]:
    return [
        str(w.render()) for w in app.screen.query(Static) if "menu-item" in (w.classes or set())
    ]


async def _right_click_room(pilot: object, app: HostApp, room_id: str) -> None:
    """Right-click the RoomItem for the given room_id."""
    import asyncio

    from textual.pilot import Pilot

    screen = app.screen
    assert isinstance(screen, MainScreen)
    room_list = screen.query_one(RoomList)
    room_list.set_rooms(app._client.rooms_data)
    await asyncio.sleep(0.05)
    for item in screen.query(RoomItem):
        if item.room.room_id == room_id:
            assert isinstance(pilot, Pilot)
            await pilot.click(item, button=3)
            await pilot.pause()
            return
    raise AssertionError(f"RoomItem for {room_id} not found in DOM")


# ---------------------------------------------------------------------------
# Test 1: right-click shows menu with expected items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_room_right_click_shows_menu() -> None:
    room_id = "!room1:server"
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = [_room(room_id, "Room One")]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _right_click_room(pilot, app, room_id)

        menus = list(app.screen.query(ContextMenu))
        assert menus, "Expected ContextMenu in DOM after right-click"

        labels = _menu_item_labels(app)
        assert any("Favourite" in lbl for lbl in labels), f"Favourite not in {labels}"
        assert any("Leave room" in lbl for lbl in labels), f"Leave not in {labels}"


# ---------------------------------------------------------------------------
# Test 2: favourite toggle calls set_room_tag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_favourite_toggle_tags_room() -> None:
    room_id = "!room1:server"
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = [_room(room_id, "Room One")]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _right_click_room(pilot, app, room_id)

        fav_items = [
            w
            for w in app.screen.query(Static)
            if "menu-item" in (w.classes or set())
            and "Favourite" in str(w.render())
            and "Unfavourite" not in str(w.render())
        ]
        assert fav_items, "★ Favourite item not found"
        await pilot.click(fav_items[0])
        await pilot.pause()

        assert any(t[0] == room_id and t[1] == "m.favourite" for t in fake.set_tags), (
            f"set_tags not called with m.favourite: {fake.set_tags}"
        )


# ---------------------------------------------------------------------------
# Test 3: unfavourite removes the tag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unfavourite_toggle_removes_tag() -> None:
    room_id = "!room1:server"
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = [_room(room_id, "Room One", tags={"m.favourite": None})]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _right_click_room(pilot, app, room_id)

        unfav_items = [
            w
            for w in app.screen.query(Static)
            if "menu-item" in (w.classes or set()) and "Unfavourite" in str(w.render())
        ]
        assert unfav_items, "★ Unfavourite item not found"
        await pilot.click(unfav_items[0])
        await pilot.pause()

        assert any(t[0] == room_id and t[1] == "m.favourite" for t in fake.removed_tags), (
            f"removed_tags not called with m.favourite: {fake.removed_tags}"
        )


# ---------------------------------------------------------------------------
# Test 4: mute toggle calls set_room_tag with m.mute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mute_toggle() -> None:
    room_id = "!room1:server"
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = [_room(room_id, "Room One")]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _right_click_room(pilot, app, room_id)

        mute_items = [
            w
            for w in app.screen.query(Static)
            if "menu-item" in (w.classes or set())
            and "Mute" in str(w.render())
            and "Unmute" not in str(w.render())
        ]
        assert mute_items, "Mute item not found"
        await pilot.click(mute_items[0])
        await pilot.pause()

        assert any(t[0] == room_id and t[1] == "m.mute" for t in fake.set_tags), (
            f"set_tags not called with m.mute: {fake.set_tags}"
        )


# ---------------------------------------------------------------------------
# Test 5: "Leave room" shows ConfirmScreen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leave_shows_confirm_dialog() -> None:
    room_id = "!room1:server"
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = [_room(room_id, "Room One")]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _right_click_room(pilot, app, room_id)

        leave_items = [
            w
            for w in app.screen.query(Static)
            if "menu-item" in (w.classes or set()) and "Leave room" in str(w.render())
        ]
        assert leave_items, "Leave room item not found"
        await pilot.click(leave_items[0])
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen), (
            f"Expected ConfirmScreen, got {type(app.screen).__name__}"
        )


# ---------------------------------------------------------------------------
# Test 6: confirming leave calls leave_room
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leave_confirmed_calls_leave_room() -> None:
    room_id = "!room1:server"
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = [_room(room_id, "Room One")]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()

        # Get the MainScreen and call _confirm_leave_room directly.
        screen = app.screen
        assert isinstance(screen, MainScreen)
        screen.query_one(RoomList).set_rooms(fake.rooms_data)
        await pilot.pause()

        import asyncio

        screen._confirm_leave_room(room_id)  # pyright: ignore[reportPrivateUsage]
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen), (
            f"Expected ConfirmScreen, got {type(app.screen).__name__}"
        )
        # Dismiss with True to simulate clicking Yes.
        app.screen.dismiss(True)
        await asyncio.sleep(0.2)
        await pilot.pause()

        assert room_id in fake.left_rooms, f"left_rooms: {fake.left_rooms}"


# ---------------------------------------------------------------------------
# Test 7: cancelling leave does nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leave_cancelled_does_nothing() -> None:
    room_id = "!room1:server"
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = [_room(room_id, "Room One")]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()

        # Directly call _confirm_leave_room to avoid click-on-menu timing issues.
        screen = app.screen
        assert isinstance(screen, MainScreen)
        screen.query_one(RoomList).set_rooms(fake.rooms_data)
        await pilot.pause()

        import asyncio

        screen._confirm_leave_room(room_id)  # pyright: ignore[reportPrivateUsage]
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen)
        # Dismiss with False to simulate clicking No.
        app.screen.dismiss(False)
        await asyncio.sleep(0.1)
        await pilot.pause()

        assert fake.left_rooms == [], f"Unexpected leaves: {fake.left_rooms}"
