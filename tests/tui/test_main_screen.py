"""Tests for MainScreen three-panel layout (plan 0005).

All tests inject FakeMatrixClient — no real network.
A minimal host App pushes MainScreen and lets us assert layout/focus.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

import fakes as fakes_module
from telemente.tui.screens.main import MainScreen

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
