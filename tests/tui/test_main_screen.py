"""Tests for MainScreen three-panel layout (plan 0005).

All tests inject FakeMatrixClient — no real network.
A minimal host App pushes MainScreen and lets us assert layout/focus.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

import fakes as fakes_module
from telemente.tui.screens.main import MainScreen

if TYPE_CHECKING:
    from telemente.tui.app import TelementeApp

FakeMatrixClient = fakes_module.FakeMatrixClient


# ---------------------------------------------------------------------------
# Minimal host app that pushes MainScreen
# ---------------------------------------------------------------------------


class HostApp(App[None]):
    """Minimal app that pushes MainScreen for layout/binding tests."""

    def __init__(self, client: FakeMatrixClient) -> None:
        super().__init__()
        self._client = client

    def compose(self) -> ComposeResult:
        yield Label("host")

    def on_mount(self) -> None:
        self.push_screen(MainScreen(self._client))


def _make_app() -> HostApp:
    return HostApp(FakeMatrixClient())


# ---------------------------------------------------------------------------
# Test 1: all three panels present and displayed after mount
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_panels_present() -> None:
    app = _make_app()

    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        rooms = screen.query_one("#rooms-panel")
        message = screen.query_one("#message-panel")
        members = screen.query_one("#members-panel")

        assert rooms.display is True
        assert message.display is True
        assert members.display is True


# ---------------------------------------------------------------------------
# Test 2: ctrl+b toggles rooms panel visibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toggle_rooms_hides_and_shows() -> None:
    app = _make_app()

    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        rooms = screen.query_one("#rooms-panel")
        message = screen.query_one("#message-panel")

        # Initially visible
        assert rooms.display is True

        # Hide rooms panel
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert rooms.display is False
        # Center must stay visible
        assert message.display is True

        # Show rooms panel again
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert rooms.display is True
        assert message.display is True


# ---------------------------------------------------------------------------
# Test 3: ctrl+r toggles members panel visibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toggle_members_hides_and_shows() -> None:
    app = _make_app()

    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        members = screen.query_one("#members-panel")
        message = screen.query_one("#message-panel")

        # Initially visible
        assert members.display is True

        # Hide members panel
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert members.display is False
        # Center must stay visible
        assert message.display is True

        # Show members panel again
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert members.display is True
        assert message.display is True


# ---------------------------------------------------------------------------
# Test 4: center always visible even when both sides collapsed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_center_always_visible() -> None:
    app = _make_app()

    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        message = screen.query_one("#message-panel")

        # Capture baseline width of center panel
        baseline_width = message.region.width

        # Collapse both side panels
        await pilot.press("ctrl+b")
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause()

        # Center still displayed and wider than baseline
        assert message.display is True
        assert message.region.width >= baseline_width


# ---------------------------------------------------------------------------
# Test 5: ctrl+k focuses the room-search input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_focus_search_binding() -> None:
    app = _make_app()

    async with app.run_test() as pilot:
        await pilot.pause()

        await pilot.press("ctrl+k")
        await pilot.pause()

        assert app.focused is not None
        assert app.focused.id == "room-search"


# ---------------------------------------------------------------------------
# Test 6: selecting a room opens a tab in the message panel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_selecting_room_opens_tab() -> None:
    """RoomSelected(A) → a tab for room A appears in the TabbedContent."""
    from textual.widgets import TabbedContent

    from telemente.matrix.models import RoomSummary
    from telemente.tui.widgets.room_list import RoomList

    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.messages_data["!a:h"] = []
    fake.members_data["!a:h"] = []
    app = HostApp(fake)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        room_list = screen.query_one(RoomList)
        room_list.set_rooms([RoomSummary(room_id="!a:h", display_name="Alpha")])
        await pilot.pause()

        room_list.post_message(RoomList.RoomSelected("!a:h"))
        await pilot.pause()
        await pilot.pause()

        tc = screen.query_one(TabbedContent)
        assert tc.tab_count == 1
        assert tc.active == "tab-room--a-h"


# ---------------------------------------------------------------------------
# Test 7: selecting same room twice focuses existing tab, doesn't open a second
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_selecting_same_room_reuses_tab() -> None:
    """RoomSelected(A) twice → still only one tab."""
    from textual.widgets import TabbedContent

    from telemente.matrix.models import RoomSummary
    from telemente.tui.widgets.room_list import RoomList

    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.messages_data["!a:h"] = []
    fake.members_data["!a:h"] = []
    app = HostApp(fake)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        room_list = screen.query_one(RoomList)
        room_list.set_rooms([RoomSummary(room_id="!a:h", display_name="Alpha")])
        await pilot.pause()

        room_list.post_message(RoomList.RoomSelected("!a:h"))
        await pilot.pause()
        await pilot.pause()
        room_list.post_message(RoomList.RoomSelected("!a:h"))
        await pilot.pause()
        await pilot.pause()

        tc = screen.query_one(TabbedContent)
        assert tc.tab_count == 1


# ---------------------------------------------------------------------------
# Test 8: cap at 8 tabs — opening a 9th evicts the oldest (LRU)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tab_cap_evicts_oldest() -> None:
    """Opening 9 rooms → only 8 tabs; the first room's tab was evicted."""
    from textual.widgets import TabbedContent

    from telemente.matrix.models import RoomSummary
    from telemente.tui.widgets.room_list import RoomList

    fake = FakeMatrixClient()
    fake.logged_in = True
    for i in range(9):
        fake.messages_data[f"!r{i}:h"] = []
        fake.members_data[f"!r{i}:h"] = []
    app = HostApp(fake)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        room_list = screen.query_one(RoomList)
        rooms = [RoomSummary(room_id=f"!r{i}:h", display_name=f"Room{i}") for i in range(9)]
        room_list.set_rooms(rooms)
        await pilot.pause()

        for i in range(9):
            room_list.post_message(RoomList.RoomSelected(f"!r{i}:h"))
            await pilot.pause()
            await pilot.pause()

        from telemente.tui.screens.main import MainScreen as MS

        assert isinstance(screen, MS)
        tc = screen.query_one(TabbedContent)
        assert tc.tab_count == 8
        # First room's tab should have been evicted — not in open tabs
        assert "!r0:h" not in screen.open_tabs
        # Last room's tab should be present and active
        assert "!r8:h" in screen.open_tabs


# ---------------------------------------------------------------------------
# Test 9: close_tab removes the tab from TabbedContent and _open_tabs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_tab_removes_tab() -> None:
    """MainScreen.close_tab(room_id) removes the tab and its entry in _open_tabs."""
    from textual.widgets import TabbedContent

    from telemente.matrix.models import RoomSummary
    from telemente.tui.screens.main import MainScreen as MS
    from telemente.tui.widgets.room_list import RoomList

    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.messages_data["!a:h"] = []
    fake.members_data["!a:h"] = []
    app = HostApp(fake)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MS)

        room_list = screen.query_one(RoomList)
        room_list.set_rooms([RoomSummary(room_id="!a:h", display_name="Alpha")])
        room_list.post_message(RoomList.RoomSelected("!a:h"))
        await pilot.pause()
        await pilot.pause()

        tc = screen.query_one(TabbedContent)
        assert tc.tab_count == 1
        assert "!a:h" in screen.open_tabs

        await screen.close_tab("!a:h")
        await pilot.pause()

        assert tc.tab_count == 0
        assert "!a:h" not in screen.open_tabs


# ---------------------------------------------------------------------------
# Helpers for TelementeApp-based unread tests
# ---------------------------------------------------------------------------


def _make_sync_app() -> tuple[TelementeApp, FakeMatrixClient]:
    """Build a TelementeApp wired with a FakeMatrixClient, bypassing login."""
    import tempfile
    from pathlib import Path

    from telemente.config import CredentialStore, Paths
    from telemente.tui.app import TelementeApp

    tmp_dir = Path(tempfile.mkdtemp())
    paths = Paths(
        config_dir=tmp_dir / "config",
        data_dir=tmp_dir / "data",
        store_dir=tmp_dir / "store",
    )
    fake = FakeMatrixClient()
    fake.logged_in = True
    store = CredentialStore(paths, service="telemente-test-main")
    tapp = TelementeApp(client=fake, credential_store=store)  # type: ignore[arg-type]
    tapp.start_sync_and_subscribe()
    return tapp, fake


# ---------------------------------------------------------------------------
# Test 10: unread clears when re-selecting an already-open tab
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unread_clears_on_reselect_existing_tab() -> None:
    """Selecting a room whose tab is already open must clear the unread badge."""
    from datetime import UTC, datetime

    from telemente.matrix.client import NewMessage
    from telemente.matrix.models import Message, RoomSummary
    from telemente.tui.screens.main import MainScreen as MS
    from telemente.tui.widgets.room_list import RoomList

    tapp, fake = _make_sync_app()
    fake.messages_data["!a:h"] = []
    fake.members_data["!a:h"] = []
    fake.messages_data["!b:h"] = []
    fake.members_data["!b:h"] = []

    async with tapp.run_test() as pilot:
        tapp.push_screen(MS(fake))
        await pilot.pause()
        screen = tapp.screen
        assert isinstance(screen, MS)

        room_list = screen.query_one(RoomList)
        room_list.set_rooms(
            [
                RoomSummary(room_id="!a:h", display_name="Alpha"),
                RoomSummary(room_id="!b:h", display_name="Beta"),
            ]
        )
        await pilot.pause()

        # Open room A
        room_list.post_message(RoomList.RoomSelected("!a:h"))
        await pilot.pause()
        await pilot.pause()

        # Switch to room B so A is no longer active
        room_list.post_message(RoomList.RoomSelected("!b:h"))
        await pilot.pause()
        await pilot.pause()

        # Simulate a message arriving in room A while it's not active
        msg = Message(
            event_id="$ev1",
            room_id="!a:h",
            sender="@alice:matrix.org",
            sender_display_name="Alice",
            body="hello",
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        )
        await fake.emit(NewMessage(message=msg))
        await pilot.pause()

        # Room A should now have unread count 1
        assert screen.unread.get("!a:h", 0) == 1

        # Re-select room A (tab already open)
        room_list.post_message(RoomList.RoomSelected("!a:h"))
        await pilot.pause()
        await pilot.pause()

        # Unread must be cleared
        assert screen.unread.get("!a:h", 0) == 0


# ---------------------------------------------------------------------------
# Test 11: unread clears on manual tab-bar switch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unread_clears_on_tab_bar_switch() -> None:
    """on_tabbed_content_tab_activated must clear unread for the activated room."""
    from datetime import UTC, datetime

    from textual.widgets import TabbedContent

    from telemente.matrix.client import NewMessage
    from telemente.matrix.models import Message, RoomSummary
    from telemente.tui.screens.main import MainScreen as MS
    from telemente.tui.widgets.room_list import RoomList

    tapp, fake = _make_sync_app()
    fake.messages_data["!a:h"] = []
    fake.members_data["!a:h"] = []
    fake.messages_data["!b:h"] = []
    fake.members_data["!b:h"] = []

    async with tapp.run_test() as pilot:
        tapp.push_screen(MS(fake))
        await pilot.pause()
        screen = tapp.screen
        assert isinstance(screen, MS)

        room_list = screen.query_one(RoomList)
        room_list.set_rooms(
            [
                RoomSummary(room_id="!a:h", display_name="Alpha"),
                RoomSummary(room_id="!b:h", display_name="Beta"),
            ]
        )
        await pilot.pause()

        # Open both rooms
        room_list.post_message(RoomList.RoomSelected("!a:h"))
        await pilot.pause()
        await pilot.pause()
        room_list.post_message(RoomList.RoomSelected("!b:h"))
        await pilot.pause()
        await pilot.pause()

        # Simulate a message arriving in room A while B is active
        msg = Message(
            event_id="$ev2",
            room_id="!a:h",
            sender="@alice:matrix.org",
            sender_display_name="Alice",
            body="hi there",
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        )
        await fake.emit(NewMessage(message=msg))
        await pilot.pause()

        assert screen.unread.get("!a:h", 0) == 1

        # Manually switch tab bar to room A's tab
        tc = screen.query_one(TabbedContent)
        tc.active = "tab-room--a-h"
        await pilot.pause()
        await pilot.pause()

        # Unread must be cleared
        assert screen.unread.get("!a:h", 0) == 0
