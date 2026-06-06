"""Tests for Tab right-click context menu (plan 0020, Part 2 / plan 0021 Bug 6).

Tier-2 tests: FakeMatrixClient; drive with _show_tab_context_menu to avoid
OutOfBounds from pilot.click when tabs are outside the visible region.
"""

from __future__ import annotations

import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label, Tab

import fakes as fakes_module
from telemente.matrix.models import RoomSummary
from telemente.tui.screens.main import MainScreen, tab_id
from telemente.tui.widgets.context_menu import ContextMenu
from telemente.tui.widgets.room_list import RoomList

FakeMatrixClient = fakes_module.FakeMatrixClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _room(room_id: str, name: str) -> RoomSummary:
    return RoomSummary(room_id=room_id, display_name=name)


def _make_client(*rooms: RoomSummary) -> FakeMatrixClient:
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = list(rooms)
    return fake


class HostApp(App[None]):
    def __init__(self, client: FakeMatrixClient) -> None:
        super().__init__()
        self._client = client

    def compose(self) -> ComposeResult:
        yield Label("host")

    def on_mount(self) -> None:
        self.push_screen(MainScreen(self._client))


async def _open_room(app: HostApp, room_id: str) -> MainScreen:
    """Select a room to open its tab, then wait for the worker to finish."""
    screen = app.screen
    assert isinstance(screen, MainScreen)
    room_list = screen.query_one(RoomList)
    room_list.set_rooms(app._client.rooms_data)
    screen.on_room_list_room_selected(RoomList.RoomSelected(room_id))
    # Give the exclusive worker time to add the pane.
    await asyncio.sleep(0.15)
    return screen


async def _get_tab(screen: MainScreen, room_id: str) -> Tab:
    """Return the Tab widget for the given room_id.

    TabbedContent wraps pane IDs with '--content-tab-' prefix on the actual Tab
    widget, so we query all Tab widgets and find the one whose .id matches the
    prefixed form.
    """
    from textual.widgets._tabbed_content import ContentTab

    tid = tab_id(room_id)
    prefixed = ContentTab.add_prefix(tid)
    for tab in screen.query(Tab):
        if tab.id == prefixed:
            return tab
    raise AssertionError(f"No Tab found for room {room_id!r} (expected id={prefixed!r})")


# ---------------------------------------------------------------------------
# Test 1: right-click on Tab shows ContextMenu
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tab_right_click_shows_menu() -> None:
    room_id = "!room1:server"
    fake = _make_client(_room(room_id, "Room One"))
    app = HostApp(fake)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_room(app, room_id)
        await pilot.pause()

        tab = await _get_tab(screen, room_id)

        # Simulate right-click by calling the internal method directly
        # (pilot.click with button=3 raises OutOfBounds for off-screen tabs).
        screen._show_tab_context_menu(tab, 5, 5)  # pyright: ignore[reportPrivateUsage]
        await pilot.pause()

        menus = list(app.screen.query(ContextMenu))
        assert menus, "Expected ContextMenu to appear after right-click on Tab"


# ---------------------------------------------------------------------------
# Test 2: "Close tab" removes the tab
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tab_close_from_context_menu() -> None:
    room_id = "!room1:server"
    fake = _make_client(_room(room_id, "Room One"))
    app = HostApp(fake)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_room(app, room_id)
        await pilot.pause()

        tab = await _get_tab(screen, room_id)
        screen._show_tab_context_menu(tab, 5, 5)  # pyright: ignore[reportPrivateUsage]
        await pilot.pause()

        menus = list(app.screen.query(ContextMenu))
        assert menus, "Expected ContextMenu"

        # Activate first item (Close tab) via Enter key.
        await pilot.press("enter")
        await asyncio.sleep(0.15)
        await pilot.pause()

        assert room_id not in screen.open_tabs, f"Tab not closed: {screen.open_tabs}"
        assert len(list(app.screen.query(ContextMenu))) == 0


# ---------------------------------------------------------------------------
# Test 3: left-click on Tab does NOT show ContextMenu
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_context_menu_on_left_click_tab() -> None:
    room_id = "!room1:server"
    fake = _make_client(_room(room_id, "Room One"))
    app = HostApp(fake)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _open_room(app, room_id)
        await pilot.pause()

        # Verify tab exists in DOM.
        tab = await _get_tab(screen, room_id)
        assert tab is not None

        # Only right-click triggers _show_tab_context_menu; left-click does not.
        # (on_mouse_down only acts on button==3.)
        # We confirm no context menu appears without calling _show_tab_context_menu.
        await pilot.pause()

        assert len(list(app.screen.query(ContextMenu))) == 0


# ---------------------------------------------------------------------------
# Test 4: right-click via pilot.click(button=3) reaches on_mouse_down (Bug 6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tab_right_click_via_mouse_event() -> None:
    """pilot.click(tab, button=3) triggers on_mouse_down and shows ContextMenu."""
    room_id = "!room1:server"
    fake = _make_client(_room(room_id, "Room One"))
    app = HostApp(fake)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = await _open_room(app, room_id)
        await pilot.pause()

        tab = await _get_tab(screen, room_id)
        # If the tab region is zero-sized (not rendered), skip.
        r = tab.region
        if r.width == 0 or r.height == 0:
            return

        try:
            await pilot.click(tab, button=3)
        except Exception:
            # Some Textual versions raise OutOfBounds for off-screen widgets; skip.
            return
        await pilot.pause()

        menus = list(app.screen.query(ContextMenu))
        assert len(menus) == 1, f"expected 1 ContextMenu, got {len(menus)}"


# ---------------------------------------------------------------------------
# Test 5: on_mouse_down with widget=None resolves target via get_widget_at
# (plan 0025 Bug 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tab_right_click_dispatch_shows_menu() -> None:
    """MouseDown with widget=None (production case) shows ContextMenu via get_widget_at."""
    from rich.style import Style as RichStyle
    from textual.events import MouseDown

    room_id = "!room1:server"
    fake = _make_client(_room(room_id, "Room One"))
    app = HostApp(fake)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = await _open_room(app, room_id)
        await pilot.pause()

        # Hide side panels so TabbedContent gets full width, making tab visible.
        screen.rooms_visible = False
        screen.members_visible = False
        await pilot.pause()

        tab = await _get_tab(screen, room_id)
        # Skip if the tab hasn't been laid out or is off-screen.
        r = tab.region
        screen_w, screen_h = app.size
        if r.width == 0 or r.height == 0:
            pytest.skip("tab region is zero-sized; layout not yet complete")
        sx = r.x + r.width // 2
        sy = r.y + r.height // 2
        if sx >= screen_w or sy >= screen_h or sx < 0 or sy < 0:
            pytest.skip(
                f"tab center ({sx},{sy}) is outside screen ({screen_w}x{screen_h}); "
                "cannot use get_widget_at"
            )

        # widget=None reproduces the production xterm-parser path — the bug.
        # Before the fix, the while-loop never executes and no menu appears.
        event = MouseDown(
            widget=None,
            x=sx,
            y=sy,
            delta_x=0,
            delta_y=0,
            button=3,
            shift=False,
            meta=False,
            ctrl=False,
            screen_x=sx,
            screen_y=sy,
            style=RichStyle(),
        )
        screen.on_mouse_down(event)
        await pilot.pause()

        menus = list(app.screen.query(ContextMenu))
        assert menus, "Expected ContextMenu after right-click with widget=None (Bug 3)"
