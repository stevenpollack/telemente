"""Tier-2 TUI tests for read receipt sending (plan 0031).

Tests:
  test_read_receipt_sent_on_room_open
  test_read_receipt_sent_on_scroll_to_bottom
  test_read_receipt_not_sent_for_empty_room
  test_read_receipt_clears_unread_badge
  test_read_receipt_only_for_active_room
"""

from __future__ import annotations

from datetime import UTC, datetime

from textual.app import App, ComposeResult
from textual.widgets import Label

import fakes as fakes_module
from conftest import wait_for_workers
from telemente.matrix.models import Message, RoomSummary
from telemente.tui.screens.main import MainScreen
from telemente.tui.widgets.room_list import RoomList

FakeMatrixClient = fakes_module.FakeMatrixClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(
    event_id: str,
    room_id: str,
    body: str = "Hello",
    ts: datetime | None = None,
) -> Message:
    return Message(
        event_id=event_id,
        room_id=room_id,
        sender="@alice:matrix.org",
        sender_display_name="Alice",
        body=body,
        timestamp=ts or datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
    )


def _room(room_id: str, display_name: str = "Room", unread_count: int = 0) -> RoomSummary:
    return RoomSummary(room_id=room_id, display_name=display_name, unread_count=unread_count)


class HostApp(App[None]):
    """Minimal app that pushes MainScreen with an injected FakeMatrixClient."""

    def __init__(self, client: FakeMatrixClient) -> None:
        super().__init__()
        self.client = client

    def compose(self) -> ComposeResult:
        yield Label("host")

    def on_mount(self) -> None:
        self.push_screen(MainScreen(self.client))


async def _open_room(app: HostApp, room_id: str) -> None:
    """Select a room in the RoomList and wait for the tab + messages to load."""
    screen = app.screen
    room_list = screen.query_one(RoomList)
    room_list.post_message(RoomList.RoomSelected(room_id))
    await wait_for_workers(app)


# ---------------------------------------------------------------------------
# Test 1: receipt sent when room is opened
# ---------------------------------------------------------------------------


async def test_read_receipt_sent_on_room_open() -> None:
    """Opening a room sends send_read_receipt for the newest message's event_id."""
    room_id = "!room1:matrix.org"
    newest_event_id = "$ev3:matrix.org"

    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = [_room(room_id, "Room One")]
    fake.messages_data[room_id] = [
        _msg("$ev1:matrix.org", room_id),
        _msg("$ev2:matrix.org", room_id),
        _msg(newest_event_id, room_id),
    ]

    app = HostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        screen = app.screen
        room_list = screen.query_one(RoomList)
        room_list.set_rooms(fake.rooms_data)
        await pilot.pause()

        await _open_room(app, room_id)

    assert (room_id, newest_event_id) in fake.sent_receipts


# ---------------------------------------------------------------------------
# Test 2: receipt sent on scroll-to-bottom (action_scroll_latest)
# ---------------------------------------------------------------------------


async def test_read_receipt_sent_on_scroll_to_bottom() -> None:
    """Pressing G (action_scroll_latest) sends send_read_receipt for the newest message."""
    room_id = "!room2:matrix.org"
    newest_event_id = "$ev_last:matrix.org"

    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = [_room(room_id, "Room Two")]
    fake.messages_data[room_id] = [
        _msg("$ev_a:matrix.org", room_id),
        _msg(newest_event_id, room_id),
    ]

    app = HostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        screen = app.screen
        assert isinstance(screen, MainScreen)
        room_list = screen.query_one(RoomList)
        room_list.set_rooms(fake.rooms_data)
        await pilot.pause()

        await _open_room(app, room_id)

        # Clear spies so the second receipt is the one we care about
        fake.reset_spies()

        # Find the MessageView and trigger scroll_latest
        view = screen.message_view_for(room_id)
        assert view is not None
        view.action_scroll_latest()
        await wait_for_workers(app)

    assert (room_id, newest_event_id) in fake.sent_receipts


# ---------------------------------------------------------------------------
# Test 3: no receipt sent for empty room
# ---------------------------------------------------------------------------


async def test_read_receipt_not_sent_for_empty_room() -> None:
    """Opening a room with no messages does not call send_read_receipt."""
    room_id = "!empty:matrix.org"

    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = [_room(room_id, "Empty Room")]
    fake.messages_data[room_id] = []

    app = HostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        screen = app.screen
        room_list = screen.query_one(RoomList)
        room_list.set_rooms(fake.rooms_data)
        await pilot.pause()

        await _open_room(app, room_id)

    assert fake.sent_receipts == []


# ---------------------------------------------------------------------------
# Test 4: receipt clears the unread badge
# ---------------------------------------------------------------------------


async def test_read_receipt_clears_unread_badge() -> None:
    """After opening a room, the unread badge for that room is zeroed."""
    room_id = "!unread:matrix.org"

    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = [_room(room_id, "Unread Room", unread_count=5)]
    fake.messages_data[room_id] = [_msg("$ev1:matrix.org", room_id)]

    app = HostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        screen = app.screen
        assert isinstance(screen, MainScreen)
        room_list = screen.query_one(RoomList)
        room_list.set_rooms(fake.rooms_data)
        await pilot.pause()

        await _open_room(app, room_id)

        # The MainScreen.unread dict should have been cleared
        assert screen.unread.get(room_id, 0) == 0


# ---------------------------------------------------------------------------
# Test 5: receipt only for the active room, not other rooms
# ---------------------------------------------------------------------------


async def test_read_receipt_only_for_active_room() -> None:
    """Opening room A does not send a receipt for room B."""
    room_a = "!roomA:matrix.org"
    room_b = "!roomB:matrix.org"

    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = [_room(room_a, "Room A"), _room(room_b, "Room B")]
    fake.messages_data[room_a] = [_msg("$evA:matrix.org", room_a)]
    fake.messages_data[room_b] = [_msg("$evB:matrix.org", room_b)]

    app = HostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        screen = app.screen
        room_list = screen.query_one(RoomList)
        room_list.set_rooms(fake.rooms_data)
        await pilot.pause()

        await _open_room(app, room_a)

    # Only room A should have a receipt, not room B
    assert (room_a, "$evA:matrix.org") in fake.sent_receipts
    assert all(r != room_b for r, _ in fake.sent_receipts)
