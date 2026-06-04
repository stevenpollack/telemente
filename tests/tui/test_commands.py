"""Smoke tests for the command palette provider (tui/commands.py).

Commands are thin wrappers over other features already tested in depth.
These tests verify: discover/search yield expected commands; representative
command callbacks reach their targets without crashing; error paths notify
rather than raise.

All tests use FakeMatrixClient — no real homeserver.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import fakes as fakes_module
from telemente.config import CredentialStore, Paths
from telemente.matrix.models import RoomSummary
from telemente.tui.app import TelementeApp
from telemente.tui.screens.main import MainScreen
from telemente.tui.widgets.room_list import RoomList

FakeMatrixClient = fakes_module.FakeMatrixClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_isolated_store(tmp_dir: Path) -> CredentialStore:
    paths = Paths(
        config_dir=tmp_dir / "config",
        data_dir=tmp_dir / "data",
        store_dir=tmp_dir / "store",
    )
    return CredentialStore(paths, service="telemente-test-commands")


def _make_app_with_main() -> tuple[TelementeApp, FakeMatrixClient]:
    """Create a TelementeApp+FakeMatrixClient wired for command testing."""
    tmp_dir = Path(tempfile.mkdtemp())
    fake = FakeMatrixClient()
    fake._logged_in = True
    store = _make_isolated_store(tmp_dir)
    app = TelementeApp(client=fake, credential_store=store)  # type: ignore[arg-type]
    app._start_sync_and_subscribe()
    return app, fake


# ---------------------------------------------------------------------------
# Test 1: discover() yields all expected command names
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_yields_all_commands() -> None:
    """discover() must yield at least the documented command names."""
    app, fake = _make_app_with_main()

    expected_names = {
        "Search rooms",
        "Toggle members pane",
        "Close tab",
        "Sort: Recent activity",
        "Sort: Alphabetical",
        "Toggle favourite ★",
        "Toggle low priority ↓",
        "Toggle mute 🔕",
        "Leave room",
        "Logout",
    }

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        from telemente.tui.commands import TelementeCommands

        provider = TelementeCommands(app.screen)
        hits = [h async for h in provider.discover()]
        names = {h.text for h in hits}
        assert expected_names.issubset(names)


# ---------------------------------------------------------------------------
# Test 2: search() filters commands by query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_filters_by_query() -> None:
    """search('sort') returns only sort-related commands."""
    app, fake = _make_app_with_main()

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        from telemente.tui.commands import TelementeCommands

        provider = TelementeCommands(app.screen)
        hits = [h async for h in provider.search("sort")]
        names = {h.text or "" for h in hits}
        # Both sort commands should match; logout should not
        assert any("Sort" in n for n in names)
        assert not any("Logout" in n for n in names)


# ---------------------------------------------------------------------------
# Test 3: search() with non-matching query returns nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_no_match_returns_empty() -> None:
    """search('xyzzy') returns no hits."""
    app, fake = _make_app_with_main()

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        from telemente.tui.commands import TelementeCommands

        provider = TelementeCommands(app.screen)
        hits = [h async for h in provider.search("xyzzy")]
        assert hits == []


# ---------------------------------------------------------------------------
# Test 4: _cmd_sort_alpha changes RoomList sort mode to alpha
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_sort_alpha_sets_alpha_mode() -> None:
    """_cmd_sort_alpha() calls set_sort_mode('alpha') on the RoomList."""
    app, fake = _make_app_with_main()
    rooms = [
        RoomSummary(room_id="!z:h", display_name="Zebra"),
        RoomSummary(room_id="!a:h", display_name="Alpha"),
    ]

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)
        room_list = screen.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        from telemente.tui.commands import TelementeCommands

        provider = TelementeCommands(screen)
        provider._cmd_sort_alpha()
        await pilot.pause()

        visible = room_list.visible_rooms
        assert visible[0].display_name == "Alpha"
        assert visible[1].display_name == "Zebra"


# ---------------------------------------------------------------------------
# Test 5: _cmd_sort_recent switches back to recent sort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_sort_recent_sets_recent_mode() -> None:
    """_cmd_sort_recent() restores 'recent' sort order."""
    from datetime import datetime

    app, fake = _make_app_with_main()
    rooms = [
        RoomSummary(
            room_id="!z:h",
            display_name="Zebra",
            last_activity=datetime(2024, 6, 1),
        ),
        RoomSummary(
            room_id="!a:h",
            display_name="Alpha",
            last_activity=datetime(2024, 1, 1),
        ),
    ]

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)
        room_list = screen.query_one(RoomList)
        room_list.set_rooms(rooms)
        room_list.set_sort_mode("alpha")
        await pilot.pause()

        from telemente.tui.commands import TelementeCommands

        provider = TelementeCommands(screen)
        provider._cmd_sort_recent()
        await pilot.pause()

        visible = room_list.visible_rooms
        # Zebra has newer activity — should appear first
        assert visible[0].room_id == "!z:h"


# ---------------------------------------------------------------------------
# Test 6: _cmd_search_rooms focuses the #room-search input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_search_rooms_focuses_input() -> None:
    """_cmd_search_rooms() focuses the #room-search Input widget."""
    app, fake = _make_app_with_main()

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)

        from telemente.tui.commands import TelementeCommands

        provider = TelementeCommands(screen)
        provider._cmd_search_rooms()
        await pilot.pause()

        assert app.focused is not None
        assert app.focused.id == "room-search"


# ---------------------------------------------------------------------------
# Test 7: _cmd_toggle_members toggles members pane visibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_toggle_members_toggles_panel() -> None:
    """_cmd_toggle_members() calls action_toggle_members() on MainScreen."""
    app, fake = _make_app_with_main()

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)
        assert screen.members_visible is True

        from telemente.tui.commands import TelementeCommands

        provider = TelementeCommands(screen)
        provider._cmd_toggle_members()
        await pilot.pause()

        assert screen.members_visible is False


# ---------------------------------------------------------------------------
# Test 8: _cmd_close_tab with no active room notifies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_close_tab_no_active_room_notifies() -> None:
    """_cmd_close_tab() with no open tab notifies the user with a warning."""
    app, fake = _make_app_with_main()

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)
        # No tab open → active_room_id is None
        assert screen.active_room_id is None

        from telemente.tui.commands import TelementeCommands

        provider = TelementeCommands(screen)
        with patch.object(app, "notify", MagicMock()) as mock_notify:
            provider._cmd_close_tab()
            await pilot.pause()
            assert mock_notify.called


# ---------------------------------------------------------------------------
# Test 9: _cmd_leave_room with no active room notifies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_leave_room_no_active_room_notifies() -> None:
    """_cmd_leave_room() with no active room shows a warning notification."""
    app, fake = _make_app_with_main()

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)

        from telemente.tui.commands import TelementeCommands

        provider = TelementeCommands(screen)
        with patch.object(app, "notify", MagicMock()) as mock_notify:
            provider._cmd_leave_room()
            await pilot.pause()
            assert mock_notify.called


# ---------------------------------------------------------------------------
# Test 10: _cmd_logout triggers logout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_logout_triggers_app_logout() -> None:
    """_cmd_logout() calls action_logout() which closes the client."""
    from telemente.tui.screens.login import LoginScreen

    app, fake = _make_app_with_main()

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)

        from telemente.tui.commands import TelementeCommands

        provider = TelementeCommands(screen)
        provider._cmd_logout()
        # Worker needs time to complete
        await pilot.pause()
        await pilot.pause()

        assert fake.close_called
        assert isinstance(app.screen, LoginScreen)
