"""Sync/state integration tests (plan 0009).

All 7 test cases drive a TelementeApp wired with FakeMatrixClient.
Events are pushed via ``fake.emit(...)``; assertions follow
``await pilot.pause()`` to let Textual messages settle.

No real homeserver, no nio, no threads.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import fakes as fakes_module
from conftest import wait_for_workers
from telemente.config import CredentialStore, Paths
from telemente.matrix.client import MembersChanged, NewMessage, RoomsChanged
from telemente.matrix.models import Member, Message, RoomSummary
from telemente.tui.app import TelementeApp
from telemente.tui.screens.main import MainScreen
from telemente.tui.widgets.member_list import MemberList
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


_msg_counter: int = 0


def _msg(room_id: str, body: str = "hello", event_id: str | None = None) -> Message:
    global _msg_counter
    _msg_counter += 1
    return Message(
        event_id=event_id if event_id is not None else f"$ev{_msg_counter}",
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


def _make_isolated_store(tmp_dir: Path) -> CredentialStore:
    """Create a CredentialStore backed by a temp directory with a unique service
    name so the OS keyring never returns a real session during tests."""
    paths = Paths(
        config_dir=tmp_dir / "config",
        data_dir=tmp_dir / "data",
        store_dir=tmp_dir / "store",
    )
    return CredentialStore(paths, service="telemente-test-sync")


def _make_app() -> tuple[TelementeApp, FakeMatrixClient]:
    """Create a TelementeApp wired with a FakeMatrixClient.

    The fake is pre-marked as logged in and the app's subscription to
    client events is established (mirrors what happens post-login/restore).

    Uses an isolated credential store so the real user session is never
    loaded, preventing a second start_sync_and_subscribe() call on mount
    that would cause duplicate event delivery.
    """
    tmp_dir = Path(tempfile.mkdtemp())
    fake = FakeMatrixClient()
    fake.logged_in = True
    store = _make_isolated_store(tmp_dir)
    app = TelementeApp(client=fake, credential_store=store)  # type: ignore[arg-type]
    # Set up the client→app event bridge (normally done after login/restore).
    app.start_sync_and_subscribe()
    return app, fake


# ---------------------------------------------------------------------------
# Test 1: RoomsChanged updates the room list
# ---------------------------------------------------------------------------


async def test_rooms_changed_updates_room_list() -> None:
    """emit(RoomsChanged([...3 rooms])) → RoomList shows 3 visible rooms."""
    app, fake = _make_app()

    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        rooms = [
            _room("!a:h", "General"),
            _room("!b:h", "Random"),
            _room("!c:h", "Dev"),
        ]
        await fake.emit(RoomsChanged(rooms=rooms))
        await pilot.pause()

        screen: MainScreen = cast(MainScreen, app.screen)
        assert isinstance(screen, MainScreen)
        room_list = screen.query_one(RoomList)
        assert len(room_list.visible_rooms) == 3


# ---------------------------------------------------------------------------
# Test 2: NewMessage appends to active room
# ---------------------------------------------------------------------------


async def test_new_message_appends_to_active_room() -> None:
    """Select room A; emit NewMessage in A → message appears in MessageView."""
    app, fake = _make_app()
    fake.messages_data["!a:h"] = []
    fake.members_data["!a:h"] = []

    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen: MainScreen = cast(MainScreen, app.screen)
        assert isinstance(screen, MainScreen)

        # Open room A via RoomSelected (opens a tab and loads messages)
        room_list = screen.query_one(RoomList)
        room_list.set_rooms([_room("!a:h", "General")])
        room_list.post_message(RoomList.RoomSelected("!a:h"))
        await wait_for_workers(app)

        msg_view = screen.message_view_for("!a:h")
        assert msg_view is not None
        assert msg_view.current_room_id == "!a:h"

        # Emit a message for room A
        msg = _msg("!a:h", body="hello from A")
        await fake.emit(NewMessage(message=msg))
        await pilot.pause()

        # The message should be in the timeline
        from telemente.tui.widgets.message_view import MessageRow

        rows = list(screen.query(MessageRow))
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Test 3: NewMessage in another room bumps unread, leaves MessageView alone
# ---------------------------------------------------------------------------


async def test_new_message_other_room_bumps_unread() -> None:
    """Active room A; emit NewMessage for room B → MessageView unchanged, B unread +1."""
    app, fake = _make_app()
    fake.messages_data["!a:h"] = []
    fake.members_data["!a:h"] = []

    rooms_ab = [_room("!a:h", "General"), _room("!b:h", "Random")]
    fake.rooms_data = list(rooms_ab)

    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen: MainScreen = cast(MainScreen, app.screen)
        assert isinstance(screen, MainScreen)

        # Open room A via RoomSelected (makes it active)
        room_list = screen.query_one(RoomList)
        room_list.set_rooms(rooms_ab)
        room_list.post_message(RoomList.RoomSelected("!a:h"))
        await wait_for_workers(app)

        msg_view = screen.message_view_for("!a:h")
        assert msg_view is not None
        assert msg_view.current_room_id == "!a:h"

        # Emit a message for room B (not active)
        msg = _msg("!b:h", body="hello from B")
        await fake.emit(NewMessage(message=msg))
        await pilot.pause()

        # MessageView for A still has no rows (only initial empty load)
        from telemente.tui.widgets.message_view import MessageRow

        assert msg_view.current_room_id == "!a:h"
        rows = list(msg_view.query(MessageRow))
        assert len(rows) == 0

        # Room B should have unread count bumped in the room list
        visible = room_list.visible_rooms
        b_room = next((r for r in visible if r.room_id == "!b:h"), None)
        assert b_room is not None
        assert b_room.unread_count == 1


# ---------------------------------------------------------------------------
# Test 4: MembersChanged for active room updates MemberList
# ---------------------------------------------------------------------------


async def test_members_changed_updates_active_room() -> None:
    """Active room A; emit MembersChanged(A, [...]) → MemberList re-renders."""
    app, fake = _make_app()
    fake.messages_data["!a:h"] = []
    fake.members_data["!a:h"] = []

    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen: MainScreen = cast(MainScreen, app.screen)
        assert isinstance(screen, MainScreen)
        # Open room A (makes it the active tab)
        room_list = screen.query_one(RoomList)
        room_list.set_rooms([_room("!a:h", "General")])
        room_list.post_message(RoomList.RoomSelected("!a:h"))
        await wait_for_workers(app)

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


async def test_members_changed_other_room_ignored() -> None:
    """Active room A; emit MembersChanged(B, ...) → MemberList unchanged."""
    app, fake = _make_app()
    fake.messages_data["!a:h"] = []
    fake.members_data["!a:h"] = []

    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen: MainScreen = cast(MainScreen, app.screen)
        assert isinstance(screen, MainScreen)
        # Open room A (makes it the active tab)
        room_list = screen.query_one(RoomList)
        room_list.set_rooms([_room("!a:h", "General")])
        room_list.post_message(RoomList.RoomSelected("!a:h"))
        await wait_for_workers(app)

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


async def test_room_selected_loads_messages_and_members() -> None:
    """Post RoomSelected(B) → MessageView.current_room_id == B, MemberList shows B's members."""
    app, fake = _make_app()
    fake.messages_data["!b:h"] = [_msg("!b:h", "hello from b")]
    fake.members_data["!b:h"] = [_member("@bob:h", "Bob")]

    rooms = [_room("!a:h", "General"), _room("!b:h", "Random", unread_count=2)]
    fake.rooms_data = list(rooms)

    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen: MainScreen = cast(MainScreen, app.screen)
        assert isinstance(screen, MainScreen)
        room_list = screen.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        # Post RoomSelected for room B
        room_list.post_message(RoomList.RoomSelected("!b:h"))
        await wait_for_workers(app)

        msg_view = screen.message_view_for("!b:h")
        assert msg_view is not None
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


async def test_close_cancels_sync() -> None:
    """Exit the app → FakeMatrixClient.close was awaited, no asyncio warnings."""
    app, fake = _make_app()

    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()
        assert not fake.close_called

    # After the context manager exits, app has been stopped / unmounted
    assert fake.close_called


# ---------------------------------------------------------------------------
# Test 8 (regression): rooms appear after session restore
# ---------------------------------------------------------------------------


async def test_rooms_appear_after_session_restore() -> None:
    """Regression test for the 'rooms disappear on restart' bug.

    Reproduces the original ordering bug: _restore_session() used to call
    start_sync_and_subscribe() BEFORE push_screen(MainScreen), so the first
    RoomsChanged event was emitted while MainScreen was not yet mounted and
    was silently dropped.

    Fix: MainScreen.on_mount() reads the client's current rooms directly
    (belt-and-suspenders), so rooms appear even if RoomsChanged fired before
    the screen was ready.  Also, push_screen() must happen before sync starts.
    """
    import tempfile as _tempfile

    from telemente.config import CredentialStore, Paths, Session

    # Build a FakeMatrixClient with pre-loaded rooms (simulating rooms already
    # known to the client after restore_login, as they would be from nio store).
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.homeserver = "https://matrix.example.org"
    fake.rooms_data = [
        RoomSummary(room_id="!general:h", display_name="General"),
        RoomSummary(room_id="!random:h", display_name="Random"),
    ]

    tmp_dir = Path(_tempfile.mkdtemp())
    paths = Paths(
        config_dir=tmp_dir / "config",
        data_dir=tmp_dir / "data",
        store_dir=tmp_dir / "store",
    )
    store = CredentialStore(paths, service="telemente-test-restore")

    # Save a fake session so on_mount calls _restore_session (not LoginScreen).
    session = Session(
        homeserver="https://matrix.example.org",
        user_id="@alice:matrix.example.org",
        device_id="TESTDEV",
        access_token="fake_token",
    )
    store.save(session)

    # App uses the fake client and isolated store.
    app = TelementeApp(client=fake, credential_store=store)  # type: ignore[arg-type]

    async with app.run_test(size=(120, 40)):
        # Allow on_mount → _restore_session to run (async worker).
        await wait_for_workers(app)

        # After restore, the app should have pushed MainScreen.
        screen: MainScreen = cast(MainScreen, app.screen)
        assert isinstance(screen, MainScreen), (
            f"Expected MainScreen after restore, got {type(screen).__name__}"
        )

        # The rooms the FakeMatrixClient already knows about should appear in
        # the room list — this is what the on_mount belt-and-suspenders provides.
        room_list = screen.query_one(RoomList)
        visible = room_list.visible_rooms
        room_ids = {r.room_id for r in visible}
        assert "!general:h" in room_ids, f"Expected General room in list, got: {room_ids}"
        assert "!random:h" in room_ids, f"Expected Random room in list, got: {room_ids}"


# ---------------------------------------------------------------------------
# Test 9: leaving a room removes it from the list AND closes its tab
# ---------------------------------------------------------------------------


async def test_leave_room_removes_from_list_and_closes_tab() -> None:
    """RoomsChanged without room B → B disappears from the list and its tab closes."""
    app, fake = _make_app()
    fake.messages_data["!b:h"] = []
    fake.members_data["!b:h"] = []

    rooms_ab = [_room("!a:h", "General"), _room("!b:h", "Random")]

    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        screen: MainScreen = cast(MainScreen, app.screen)
        assert isinstance(screen, MainScreen)

        room_list = screen.query_one(RoomList)
        room_list.set_rooms(rooms_ab)
        # Open room B so it has an active tab
        room_list.post_message(RoomList.RoomSelected("!b:h"))
        await wait_for_workers(app)

        assert screen.message_view_for("!b:h") is not None
        assert "!b:h" in screen.open_tabs

        # Simulate leave: emit RoomsChanged without room B
        await fake.emit(RoomsChanged(rooms=[_room("!a:h", "General")]))
        await wait_for_workers(app)

        # Room B gone from list
        visible_ids = {r.room_id for r in room_list.visible_rooms}
        assert "!b:h" not in visible_ids

        # Tab for B should be closed
        assert "!b:h" not in screen.open_tabs
        assert screen.message_view_for("!b:h") is None


# ---------------------------------------------------------------------------
# Test 10: action_logout clears credentials, closes client, returns to login
# ---------------------------------------------------------------------------


async def test_action_logout_clears_session_and_shows_login() -> None:
    """action_logout() clears credentials, calls client.close(), navigates to LoginScreen."""
    from telemente.tui.screens.login import LoginScreen

    app, fake = _make_app()

    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        # Confirm we're on MainScreen
        assert isinstance(app.screen, MainScreen)

        await app.action_logout()
        await pilot.pause()

        # After logout: client was closed and we're on LoginScreen
        assert fake.close_called
        assert isinstance(app.screen, LoginScreen)


# ---------------------------------------------------------------------------
# Test 11: _on_client_event caches rooms when user_id is known
# ---------------------------------------------------------------------------


async def test_on_client_event_saves_rooms_to_cache() -> None:
    """RoomsChanged event triggers room cache save when user_id is known."""
    app, fake = _make_app()
    # Whitebox: no public API on TelementeApp to set _cached_user_id (it is
    # populated by on_login_screen_logged_in). Setting it directly is the only
    # way to put the app in the "user_id known" state that triggers cache saves.
    app._cached_user_id = "@alice:matrix.org"  # pyright: ignore[reportPrivateUsage]

    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        rooms = [_room("!a:h", "General"), _room("!b:h", "Random")]
        await fake.emit(RoomsChanged(rooms=rooms))
        await pilot.pause()

        # Room cache should have saved the rooms for this user.
        # Whitebox: _room_cache is private; no public API to query the cache.
        cached = app._room_cache.load("@alice:matrix.org")  # pyright: ignore[reportPrivateUsage]
        assert cached is not None
        assert len(cached) == 2


# ---------------------------------------------------------------------------
# Test 12: _restore_session rebuilds client when homeserver differs
# ---------------------------------------------------------------------------


async def test_restore_session_rebuilds_client_for_different_homeserver() -> None:
    """_restore_session rebuilds _client when session homeserver != app default."""
    import tempfile as _tempfile

    from telemente.config import CredentialStore, Paths, Session

    fake = FakeMatrixClient()
    fake.logged_in = True
    # fake.homeserver is "https://matrix.org" (default in FakeMatrixClient)

    tmp_dir = Path(_tempfile.mkdtemp())
    paths = Paths(
        config_dir=tmp_dir / "config",
        data_dir=tmp_dir / "data",
        store_dir=tmp_dir / "store",
    )
    store = CredentialStore(paths, service="telemente-test-rebuild")

    session = Session(
        homeserver="https://other.matrix.example.org",  # different from fake's homeserver
        user_id="@bob:other.matrix.example.org",
        device_id="TESTDEV2",
        access_token="fake_token2",
    )
    store.save(session)

    app = TelementeApp(client=fake, credential_store=store)  # type: ignore[arg-type]

    async with app.run_test(size=(120, 40)):
        await wait_for_workers(app)

        # The app should have pushed MainScreen after restore
        screen: MainScreen = cast(MainScreen, app.screen)
        assert isinstance(screen, MainScreen)
        # The client should have been rebuilt for the session's homeserver
        assert app.client.homeserver == "https://other.matrix.example.org"


# ---------------------------------------------------------------------------
# Test 13: message_hook captures RoomsChanged
# ---------------------------------------------------------------------------


async def test_message_hook_captures_rooms_changed() -> None:
    """Using message_hook=messages.append: emit RoomsChanged and verify it is captured."""
    from textual.message import Message as TextualMessage

    app, fake = _make_app()
    messages: list[TextualMessage] = []

    async with app.run_test(size=(120, 40), message_hook=messages.append) as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        rooms = [_room("!a:h", "General"), _room("!b:h", "Random")]
        await fake.emit(RoomsChanged(rooms=rooms))
        await wait_for_workers(app)

        # Verify the app-level Textual message (posted by _on_client_event)
        # arrived in the message hook.
        # The app wraps RoomsChanged in _ClientRoomsChanged Textual message
        rc_messages = [m for m in messages if type(m).__name__ == "_ClientRoomsChanged"]
        assert len(rc_messages) >= 1, (
            f"Expected _ClientRoomsChanged in message hook; got: "
            f"{[type(m).__name__ for m in messages]}"
        )


# ---------------------------------------------------------------------------
# Test 14: test_full_login_to_main_flow
# ---------------------------------------------------------------------------


async def test_full_login_to_main_flow() -> None:
    """Full login flow: TelementeApp with no session shows LoginScreen;
    posting a LoggedIn message (as if login succeeded) triggers MainScreen push;
    then RoomsChanged populates the room list.

    We bypass the real MatrixClient construction by patching _restore_and_navigate
    before dispatching the LoggedIn message, so the fake client is used throughout.
    """
    import tempfile as _tempfile

    from telemente.config import CredentialStore, Paths, Session
    from telemente.tui.screens.login import LoginScreen

    # Isolated store with NO saved session -> LoginScreen will be shown.
    tmp_dir = Path(_tempfile.mkdtemp())
    paths = Paths(
        config_dir=tmp_dir / "config",
        data_dir=tmp_dir / "data",
        store_dir=tmp_dir / "store",
    )
    store = CredentialStore(paths, service="telemente-test-full-login")
    store.clear()  # Ensure no leftover session from a previous test run
    assert store.load() is None, "store should be empty to trigger LoginScreen"

    fake = FakeMatrixClient()
    fake.logged_in = True  # fake is pre-authenticated for restore()

    app = TelementeApp(client=fake, credential_store=store)  # type: ignore[arg-type]

    session = Session(
        homeserver="https://matrix.org",
        user_id="@alice:matrix.org",
        device_id="TESTDEV",
        access_token="fake_token",
    )

    # Patch _restore_and_navigate before the message is dispatched. The real
    # on_login_screen_logged_in replaces self._client with a real MatrixClient
    # then calls run_worker(_restore_and_navigate(session)). We override this
    # method to inject the fake back and navigate directly.
    async def _fake_restore_and_navigate(sess: Session) -> None:
        # Re-inject the fake (on_login_screen_logged_in overwrites _client)
        app._client = fake  # type: ignore[assignment]  # whitebox: FakeMatrixClient satisfies protocol
        app.push_screen(MainScreen(fake))
        app.start_sync_and_subscribe()

    app._restore_and_navigate = _fake_restore_and_navigate  # type: ignore[assignment]  # whitebox: patching method for test isolation
    # Whitebox: no public API to inject a custom navigation callback into
    # on_login_screen_logged_in; patching _restore_and_navigate is the only seam.

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        # Step 1: LoginScreen is the initial screen (no saved session).
        assert isinstance(app.screen, LoginScreen), (
            f"Expected LoginScreen on first run; got {type(app.screen).__name__}"
        )

        # Step 2: Post LoggedIn as if login succeeded (simulates submit callback).
        login_screen = app.screen
        assert isinstance(login_screen, LoginScreen)
        login_screen.post_message(LoginScreen.LoggedIn(session))
        await wait_for_workers(app)

        # Step 3: MainScreen is now pushed.
        assert isinstance(app.screen, MainScreen), (
            f"Expected MainScreen after login; got {type(app.screen).__name__}"
        )

        # Step 4: Emit RoomsChanged and assert rooms appear in the list.
        rooms = [_room("!a:h", "General"), _room("!b:h", "Random")]
        await fake.emit(RoomsChanged(rooms=rooms))
        await wait_for_workers(app)

        screen_now = cast(MainScreen, app.screen)
        room_list = screen_now.query_one(RoomList)
        visible_ids = {r.room_id for r in room_list.visible_rooms}
        assert "!a:h" in visible_ids, f"Expected General room in list, got: {visible_ids}"
        assert "!b:h" in visible_ids, f"Expected Random room in list, got: {visible_ids}"
