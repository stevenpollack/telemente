"""SVG snapshot tests for telemente TUI (plan 0026, Gap 9).

These tests use ``pytest-textual-snapshot`` to catch visual regressions in
layout, colours, and widget sizing.  Baselines are committed SVGs stored in
``tests/tui/snapshots/``.

All tests are **synchronous** — ``snap_compare`` manages its own async loop.
Use ``run_before`` for any async setup that must happen before the screenshot.
"""

from __future__ import annotations

import pathlib
from typing import Any

from textual.app import App, ComposeResult
from textual.pilot import Pilot
from textual.widgets import Label

import fakes as fakes_module
from telemente.matrix.auth import LoginFlows
from telemente.matrix.models import RoomSummary
from telemente.tui.screens.login import LoginScreen
from telemente.tui.screens.main import MainScreen
from telemente.tui.widgets.room_list import RoomList

FakeMatrixClient = fakes_module.FakeMatrixClient

_APP_TCSS = str(
    pathlib.Path(__file__).parent.parent.parent
    / "src"
    / "telemente"
    / "tui"
    / "styles"
    / "app.tcss"
)


# ---------------------------------------------------------------------------
# Shared host apps
# ---------------------------------------------------------------------------


class MainScreenApp(App[None]):
    """Host app that loads app.tcss and pushes MainScreen."""

    CSS_PATH = _APP_TCSS

    def __init__(self, client: FakeMatrixClient) -> None:
        super().__init__()
        self._client = client

    def compose(self) -> ComposeResult:
        yield Label("host")

    def on_mount(self) -> None:
        self.push_screen(MainScreen(self._client))


class LoginScreenApp(App[None]):
    """Host app that loads app.tcss and pushes LoginScreen."""

    CSS_PATH = _APP_TCSS

    def __init__(self, client: FakeMatrixClient) -> None:
        super().__init__()
        self._client = client

    def compose(self) -> ComposeResult:
        yield Label("host")

    def on_mount(self) -> None:
        def factory(_homeserver: str) -> FakeMatrixClient:
            return self._client

        self.push_screen(LoginScreen(factory, default_homeserver="https://matrix.org"))


# ---------------------------------------------------------------------------
# Snapshot test 1: main screen at rest (no tab open)
# ---------------------------------------------------------------------------


def test_snapshot_main_screen_at_rest(snap_compare: Any) -> None:
    """Main screen with one room in the list, no tab open."""
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = [RoomSummary(room_id="!general:matrix.org", display_name="General")]
    app = MainScreenApp(fake)

    async def _setup(pilot: Pilot[Any]) -> None:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MainScreen)
        room_list = screen.query_one(RoomList)
        room_list.set_rooms(fake.rooms_data)
        await pilot.pause()

    assert snap_compare(app, terminal_size=(120, 40), run_before=_setup)


# ---------------------------------------------------------------------------
# Snapshot test 2: main screen with one tab open
# ---------------------------------------------------------------------------


def test_snapshot_main_screen_one_tab(snap_compare: Any) -> None:
    """Main screen with a room tab open."""
    from conftest import wait_for_workers

    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = [RoomSummary(room_id="!general:matrix.org", display_name="General")]
    fake.messages_data["!general:matrix.org"] = []
    fake.members_data["!general:matrix.org"] = []
    app = MainScreenApp(fake)

    async def _setup(pilot: Pilot[Any]) -> None:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MainScreen)
        room_list = screen.query_one(RoomList)
        room_list.set_rooms(fake.rooms_data)
        await pilot.pause()
        room_list.post_message(RoomList.RoomSelected("!general:matrix.org"))
        await wait_for_workers(app)

    assert snap_compare(app, terminal_size=(120, 40), run_before=_setup)


# ---------------------------------------------------------------------------
# Snapshot test 3: login screen
# ---------------------------------------------------------------------------


def test_snapshot_login_screen(snap_compare: Any) -> None:
    """LoginScreen at rest (password form visible)."""
    fake = FakeMatrixClient()
    fake.set_flows(LoginFlows(password=True, sso=False, token=False))
    app = LoginScreenApp(fake)

    async def _setup(pilot: Pilot[Any]) -> None:
        await pilot.pause()

    assert snap_compare(app, terminal_size=(120, 40), run_before=_setup)


# ---------------------------------------------------------------------------
# Snapshot test 4: context menu open on a room
# ---------------------------------------------------------------------------


def test_snapshot_context_menu(snap_compare: Any) -> None:
    """Context menu open on a room in the room list."""
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = [RoomSummary(room_id="!general:matrix.org", display_name="General")]
    app = MainScreenApp(fake)

    async def _setup(pilot: Pilot[Any]) -> None:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MainScreen)
        room_list = screen.query_one(RoomList)
        room_list.set_rooms(fake.rooms_data)
        await pilot.pause()

        # Trigger the room context menu at a fixed position so the snapshot
        # is deterministic regardless of actual mouse coordinates.
        room = fake.rooms_data[0]
        room_list.post_message(RoomList.RoomContextMenu(room, screen_x=5, screen_y=5))
        await pilot.pause()

    assert snap_compare(app, terminal_size=(120, 40), run_before=_setup)
