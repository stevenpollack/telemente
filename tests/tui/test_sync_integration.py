"""Sync/state integration tests (plan 0009).

All 7 test cases drive a TelementeApp wired with FakeMatrixClient.
Events are pushed via ``fake.emit(...)``; assertions follow
``await pilot.pause()`` to let Textual messages settle.

No real homeserver, no nio, no threads.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

import fakes as fakes_module
from telemente.matrix.client import MembersChanged, NewMessage, RoomsChanged
from telemente.matrix.models import Member, Message, RoomSummary
from telemente.tui.app import TelementeApp
from telemente.tui.screens.main import MainScreen
from telemente.tui.widgets.member_list import MemberList
from telemente.tui.widgets.message_view import MessageView
from telemente.tui.widgets.room_list import RoomList

FakeMatrixClient = fakes_module.FakeMatrixClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _room(
    room_id: str,
    display_name: str,
    unread_count: int = 0,
) -> RoomSummary:
    return RoomSummary(
        room_id=room_id,
        display_name=display_name,
        unread_count=unread_count,
    )


def _msg(room_id: str, body: str = "hello") -> Message:
    return Message(
        event_id="$ev1",
        room_id=room_id,
        sender="@alice:matrix.org",
        sender_display_name="Alice",
        body=body,
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
    )


def _member(user_id: str, display_name: str, power_level: int = 0) -> Member:
    return Member(
        user_id=user_id,
        display_name=display_name,
        power_level=power_level,
    )


def _make_app() -> tuple[TelementeApp, FakeMatrixClient]:
    """Create a TelementeApp wired with a FakeMatrixClient.

    The fake is pre-marked as logged in and the app's subscription to
    client events is established (mirrors what happens post-login/restore).
    """
    fake = FakeMatrixClient()
    fake._logged_in = True
    app = TelementeApp(client=fake)  # type: ignore[arg-type]
    # Set up the client→app event bridge (normally done after login/restore).
    app._start_sync_and_subscribe()
    return app, fake


# ---------------------------------------------------------------------------
# Test 1: RoomsChanged updates the room list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rooms_changed_updates_room_list() -> None:
    """emit(RoomsChanged([...3 rooms])) → RoomList shows 3 visible rooms."""
    app, fake = _make_app()

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        rooms = [
            _room("!a:h", "General"),
            _room("!b:h", "Random"),
            _room("!c:h", "Dev"),
        ]
        await fake.emit(RoomsChanged(rooms=rooms))
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)
        room_list = screen.query_one(RoomList)
        assert len(room_list.visible_rooms) == 3


# ---------------------------------------------------------------------------
# Test 2: NewMessage appends to active room
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_message_appends_to_active_room() -> None:
    """Select room A; emit NewMessage in A → message appears in MessageView."""
    app, fake = _make_app()
    fake._messages["!a:h"] = []
    fake._members["!a:h"] = []

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)

        # Set active room A on the screen
        screen._active_room_id = "!a:h"
        msg_view = screen.query_one(MessageView)
        await msg_view.load_room("!a:h")
        await pilot.pause()
        assert msg_view.current_room_id == "!a:h"

        # Emit a message for room A
        msg = _msg("!a:h", body="hello from A")
        await fake.emit(NewMessage(message=msg))
        await pilot.pause()

        # The message should be in the timeline
        from telemente.tui.widgets.message_view import _MessageRow

        rows = list(screen.query(_MessageRow))
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Test 3: NewMessage in another room bumps unread, leaves MessageView alone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_message_other_room_bumps_unread() -> None:
    """Active room A; emit NewMessage for room B → MessageView unchanged, B unread +1."""
    app, fake = _make_app()
    fake._messages["!a:h"] = []
    fake._members["!a:h"] = []

    rooms_ab = [_room("!a:h", "General"), _room("!b:h", "Random")]
    fake._rooms = list(rooms_ab)

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)

        # Populate room list and make room A active
        room_list = screen.query_one(RoomList)
        room_list.set_rooms(rooms_ab)
        screen._active_room_id = "!a:h"
        await pilot.pause()

        msg_view = screen.query_one(MessageView)
        await msg_view.load_room("!a:h")
        await pilot.pause()

        # Emit a message for room B (not active)
        msg = _msg("!b:h", body="hello from B")
        await fake.emit(NewMessage(message=msg))
        await pilot.pause()

        # MessageView is still on room A with no rows
        from telemente.tui.widgets.message_view import _MessageRow

        assert msg_view.current_room_id == "!a:h"
        rows = list(screen.query(_MessageRow))
        assert len(rows) == 0

        # Room B should have unread count bumped in the room list
        visible = room_list.visible_rooms
        b_room = next((r for r in visible if r.room_id == "!b:h"), None)
        assert b_room is not None
        assert b_room.unread_count == 1


# ---------------------------------------------------------------------------
# Test 4: MembersChanged for active room updates MemberList
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_members_changed_updates_active_room() -> None:
    """Active room A; emit MembersChanged(A, [...]) → MemberList re-renders."""
    app, fake = _make_app()

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)
        screen._active_room_id = "!a:h"

        member_list = screen.query_one(MemberList)
        assert member_list.member_count == 0

        members = [
            _member("@alice:h", "Alice", 100),
            _member("@bob:h", "Bob"),
        ]
        await fake.emit(MembersChanged(room_id="!a:h", members=members))
        await pilot.pause()

        assert member_list.member_count == 2


# ---------------------------------------------------------------------------
# Test 5: MembersChanged for another room is ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_members_changed_other_room_ignored() -> None:
    """Active room A; emit MembersChanged(B, ...) → MemberList unchanged."""
    app, fake = _make_app()

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)
        screen._active_room_id = "!a:h"

        member_list = screen.query_one(MemberList)
        assert member_list.member_count == 0

        members = [
            _member("@alice:h", "Alice", 100),
            _member("@bob:h", "Bob"),
        ]
        await fake.emit(MembersChanged(room_id="!b:h", members=members))
        await pilot.pause()

        # Should still be 0 — B's event was ignored
        assert member_list.member_count == 0


# ---------------------------------------------------------------------------
# Test 6: RoomSelected loads messages + members + clears unread
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_room_selected_loads_messages_and_members() -> None:
    """Post RoomSelected(B) → MessageView.current_room_id == B, MemberList shows B's members."""
    app, fake = _make_app()
    fake._messages["!b:h"] = [_msg("!b:h", "hello from b")]
    fake._members["!b:h"] = [_member("@bob:h", "Bob")]

    rooms = [_room("!a:h", "General"), _room("!b:h", "Random", unread_count=2)]
    fake._rooms = list(rooms)

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)
        room_list = screen.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        # Post RoomSelected for room B
        room_list.post_message(RoomList.RoomSelected("!b:h"))
        await pilot.pause()

        msg_view = screen.query_one(MessageView)
        assert msg_view.current_room_id == "!b:h"

        member_list = screen.query_one(MemberList)
        assert member_list.member_count == 1

        # Unread for B should be cleared
        visible = room_list.visible_rooms
        b_room = next((r for r in visible if r.room_id == "!b:h"), None)
        assert b_room is not None
        assert b_room.unread_count == 0


# ---------------------------------------------------------------------------
# Test 7: close() is awaited on app exit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_cancels_sync() -> None:
    """Exit the app → FakeMatrixClient.close was awaited, no asyncio warnings."""
    app, fake = _make_app()

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()
        assert not fake.close_called

    # After the context manager exits, app has been stopped / unmounted
    assert fake.close_called
