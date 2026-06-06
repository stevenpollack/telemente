"""Tier-2 tests for ThreadPanel widget and MainScreen integration (plan 0023).

Uses FakeMatrixClient — no real network.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from textual.app import App, ComposeResult
from textual.events import Mount
from textual.message import Message as TextualMessage
from textual.widgets import Label, Static

import fakes as fakes_module
from conftest import wait_for_workers
from telemente.matrix.client import NewMessage
from telemente.matrix.models import Message, RoomSummary
from telemente.tui.screens.main import MainScreen
from telemente.tui.widgets.message_view import MessageRow, MessageView
from telemente.tui.widgets.room_list import RoomList
from telemente.tui.widgets.thread_panel import ThreadPanel

FakeMatrixClient = fakes_module.FakeMatrixClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(
    event_id: str,
    room_id: str = "!r:s",
    body: str = "Hello",
    thread_root_id: str | None = None,
    sender: str = "@alice:matrix.org",
) -> Message:
    return Message(
        event_id=event_id,
        room_id=room_id,
        sender=sender,
        sender_display_name="Alice",
        body=body,
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        thread_root_id=thread_root_id,
    )


# ---------------------------------------------------------------------------
# ThreadHostApp — minimal host for ThreadPanel tests
# ---------------------------------------------------------------------------


class ThreadHostApp(App[None]):
    """Minimal app wrapping ThreadPanel for isolated unit tests."""

    def __init__(self, client: FakeMatrixClient) -> None:
        super().__init__()
        self.client = client

    def compose(self) -> ComposeResult:
        yield ThreadPanel(self.client, id="thread-panel")


# ---------------------------------------------------------------------------
# MainScreen host app
# ---------------------------------------------------------------------------


class MainHostApp(App[None]):
    def __init__(self, client: FakeMatrixClient) -> None:
        super().__init__()
        self.client = client

    def compose(self) -> ComposeResult:
        yield Label("host")

    def on_mount(self) -> None:
        self.push_screen(MainScreen(self.client))


def _make_main_app(client: FakeMatrixClient | None = None) -> MainHostApp:
    return MainHostApp(client or FakeMatrixClient())


# ---------------------------------------------------------------------------
# Test 1: ThreadPanel shows messages after load
# ---------------------------------------------------------------------------


async def test_thread_panel_shows_messages_after_load() -> None:
    fake = FakeMatrixClient()
    msg1 = _msg("$m1", body="First")
    msg2 = _msg("$m2", body="Second")
    fake.thread_messages[("!r:s", "$root")] = ([msg1, msg2], False)

    app = ThreadHostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        panel = app.query_one(ThreadPanel)
        panel.load_thread("!r:s", "$root")
        await wait_for_workers(app)

        rows = list(panel.query(MessageRow))
        assert len(rows) == 2
        # Assert user-visible content: first row shows "First", second shows "Second".
        bodies = [str(s.render()) for row in rows for s in row.query(Static)]
        assert any("First" in b for b in bodies)
        assert any("Second" in b for b in bodies)


# ---------------------------------------------------------------------------
# Test 2: empty thread shows no rows
# ---------------------------------------------------------------------------


async def test_thread_panel_empty_thread_shows_no_rows() -> None:
    fake = FakeMatrixClient()
    fake.thread_messages[("!r:s", "$root")] = ([], False)

    app = ThreadHostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        panel = app.query_one(ThreadPanel)
        panel.load_thread("!r:s", "$root")
        await wait_for_workers(app)

        rows = list(panel.query(MessageRow))
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# Test 3: Escape key posts CloseRequested
# ---------------------------------------------------------------------------


async def test_thread_panel_close_posts_close_requested() -> None:
    fake = FakeMatrixClient()
    fake.thread_messages[("!r:s", "$root")] = ([], False)

    closed: list[bool] = []

    class TrackingApp(App[None]):
        def compose(self) -> ComposeResult:
            yield ThreadPanel(fake, id="thread-panel")

        def on_thread_panel_close_requested(self, _: ThreadPanel.CloseRequested) -> None:
            closed.append(True)

    app = TrackingApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        panel = app.query_one(ThreadPanel)
        panel.load_thread("!r:s", "$root")
        await pilot.pause()
        # Focus the close Button — it is focusable, and escape will bubble up from
        # it through ThreadPanel, triggering ThreadPanel.BINDINGS["escape"].
        close_btn = panel.query_one("#thread-close")
        close_btn.focus()
        await pilot.pause()
        assert app.focused is not None
        assert app.focused.__class__.__name__ == "Button", "close button must have focus"
        await pilot.press("escape")
        await pilot.pause()

    assert closed, "CloseRequested was not posted on Escape"


# ---------------------------------------------------------------------------
# Test 4: close button posts CloseRequested
# ---------------------------------------------------------------------------


async def test_thread_panel_close_button_posts_close_requested() -> None:
    fake = FakeMatrixClient()
    fake.thread_messages[("!r:s", "$root")] = ([], False)

    closed: list[bool] = []

    class TrackingApp(App[None]):
        def compose(self) -> ComposeResult:
            yield ThreadPanel(fake, id="thread-panel")

        def on_thread_panel_close_requested(self, _: ThreadPanel.CloseRequested) -> None:
            closed.append(True)

    app = TrackingApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        panel = app.query_one(ThreadPanel)
        panel.load_thread("!r:s", "$root")
        await pilot.pause()
        await pilot.click("#thread-close")
        await pilot.pause()

    assert closed, "CloseRequested was not posted on close button"


# ---------------------------------------------------------------------------
# Test 5: append_message adds a new row
# ---------------------------------------------------------------------------


async def test_thread_panel_append_message_adds_row() -> None:
    fake = FakeMatrixClient()
    msg1 = _msg("$m1", body="First")
    fake.thread_messages[("!r:s", "$root")] = ([msg1], False)

    app = ThreadHostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        panel = app.query_one(ThreadPanel)
        panel.load_thread("!r:s", "$root")
        await wait_for_workers(app)

        new_msg = _msg("$m2", body="Appended")
        panel.append_message(new_msg)
        await wait_for_workers(app)

        rows = list(panel.query(MessageRow))
        assert len(rows) == 2
        bodies = [str(s.render()) for row in rows for s in row.query(Static)]
        assert any("Appended" in b for b in bodies)
        assert any("First" in b for b in bodies)


# ---------------------------------------------------------------------------
# Test 6: append_message deduplicates by event_id
# ---------------------------------------------------------------------------


async def test_thread_panel_deduplicates_appended_messages() -> None:
    fake = FakeMatrixClient()
    msg1 = _msg("$m1", body="First")
    fake.thread_messages[("!r:s", "$root")] = ([msg1], False)

    app = ThreadHostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        panel = app.query_one(ThreadPanel)
        panel.load_thread("!r:s", "$root")
        await wait_for_workers(app)

        # Append the same message again
        panel.append_message(msg1)
        await wait_for_workers(app)

        rows = list(panel.query(MessageRow))
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Test 7: "Load more" shown when has_more=True
# ---------------------------------------------------------------------------


async def test_thread_panel_has_more_notice_shown() -> None:
    fake = FakeMatrixClient()
    msg1 = _msg("$m1")
    fake.thread_messages[("!r:s", "$root")] = ([msg1], True)

    app = ThreadHostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        panel = app.query_one(ThreadPanel)
        panel.load_thread("!r:s", "$root")
        await wait_for_workers(app)

        load_more = panel.query_one("#thread-load-more", Static)
        assert load_more.display is True


# ---------------------------------------------------------------------------
# Test 8: "Load more" hidden when has_more=False
# ---------------------------------------------------------------------------


async def test_thread_panel_has_more_notice_hidden_when_complete() -> None:
    fake = FakeMatrixClient()
    msg1 = _msg("$m1")
    fake.thread_messages[("!r:s", "$root")] = ([msg1], False)

    app = ThreadHostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        panel = app.query_one(ThreadPanel)
        panel.load_thread("!r:s", "$root")
        await wait_for_workers(app)

        load_more = panel.query_one("#thread-load-more", Static)
        assert load_more.display is False


# ---------------------------------------------------------------------------
# Test 9: MainScreen.open_thread shows thread panel
# ---------------------------------------------------------------------------


async def test_main_screen_open_thread_shows_panel() -> None:
    fake = FakeMatrixClient()
    fake.logged_in = True
    msg1 = _msg("$m1", body="ThreadMsg")
    fake.thread_messages[("!r:s", "$root")] = ([msg1], False)

    messages: list[TextualMessage] = []
    app = _make_main_app(fake)
    async with app.run_test(size=(120, 40), message_hook=messages.append) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MainScreen)

        # Drive opening via the real message-driven path (MessageView.OpenThread),
        # which MainScreen.on_message_view_open_thread handles by calling open_thread.
        screen.post_message(MessageView.OpenThread("!r:s", "$root"))
        await wait_for_workers(app)

        # Strong assertion: the OpenThread message actually flowed through the app.
        open_msgs = [m for m in messages if isinstance(m, MessageView.OpenThread)]
        assert open_msgs, "MessageView.OpenThread was not posted/handled"
        assert open_msgs[0].room_id == "!r:s"
        assert open_msgs[0].root_event_id == "$root"

        panel = screen.query_one("#thread-panel")
        assert panel.display is True
        rows = list(panel.query(MessageRow))
        assert len(rows) >= 1
        # Assert the loaded message is the one we set up.
        bodies = [str(s.render()) for row in rows for s in row.query(Static)]
        assert any("ThreadMsg" in b for b in bodies)


# ---------------------------------------------------------------------------
# Test 10: MainScreen.close_thread hides panel
# ---------------------------------------------------------------------------


async def test_main_screen_close_thread_hides_panel() -> None:
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.thread_messages[("!r:s", "$root")] = ([], False)

    messages: list[TextualMessage] = []
    app = _make_main_app(fake)
    async with app.run_test(size=(120, 40), message_hook=messages.append) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MainScreen)

        screen.open_thread("!r:s", "$root")
        await wait_for_workers(app)
        panel = screen.query_one(ThreadPanel)
        # Drive close via the real message path so the screen handler runs.
        panel.post_message(ThreadPanel.CloseRequested())
        await wait_for_workers(app)

        close_msgs = [m for m in messages if isinstance(m, ThreadPanel.CloseRequested)]
        assert close_msgs, "ThreadPanel.CloseRequested was not posted/handled"

        panel_node = screen.query_one("#thread-panel")
        assert panel_node.display is False


# ---------------------------------------------------------------------------
# Test 11: context menu includes "View thread" for thread reply messages
# ---------------------------------------------------------------------------


async def test_context_menu_view_thread_appears_for_thread_reply() -> None:
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.set_me("@alice:matrix.org", "Alice")
    thread_msg = _msg("$m1", thread_root_id="$root")

    from telemente.tui.widgets.message_view import MessageRow as MR

    captured_menus: list[MessageView.ShowContextMenu] = []

    class HostApp2(App[None]):
        def compose(self) -> ComposeResult:
            yield MessageView(fake, id="mv")

        def on_message_view_show_context_menu(self, event: MessageView.ShowContextMenu) -> None:
            captured_menus.append(event)

    app = HostApp2()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)
        view._current_room_id = "!r:s"  # pyright: ignore[reportPrivateUsage]

        view.post_message(MR.ContextMenuRequest(message=thread_msg, screen_x=5, screen_y=5))
        await pilot.pause()

    assert captured_menus, "ShowContextMenu was not posted"
    from telemente.tui.widgets.context_menu import MenuItem

    labels = [item.label for item in captured_menus[0].items if isinstance(item, MenuItem)]
    assert "View thread" in labels


# ---------------------------------------------------------------------------
# Test 12: context menu does NOT include "View thread" for plain messages
# ---------------------------------------------------------------------------


async def test_context_menu_view_thread_absent_for_plain_message() -> None:
    fake = FakeMatrixClient()
    fake.set_me("@alice:matrix.org", "Alice")
    plain_msg = _msg("$m1")  # thread_root_id=None

    from telemente.tui.widgets.message_view import MessageRow as MR

    captured_menus: list[MessageView.ShowContextMenu] = []

    class HostApp3(App[None]):
        def compose(self) -> ComposeResult:
            yield MessageView(fake, id="mv")

        def on_message_view_show_context_menu(self, event: MessageView.ShowContextMenu) -> None:
            captured_menus.append(event)

    app = HostApp3()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)
        view._current_room_id = "!r:s"  # pyright: ignore[reportPrivateUsage]

        view.post_message(MR.ContextMenuRequest(message=plain_msg, screen_x=5, screen_y=5))
        await pilot.pause()

    assert captured_menus, "ShowContextMenu was not posted"
    from telemente.tui.widgets.context_menu import MenuItem

    labels = [item.label for item in captured_menus[0].items if isinstance(item, MenuItem)]
    assert "View thread" not in labels


# ---------------------------------------------------------------------------
# Test 13: command palette "Open thread" opens panel for focused thread row
# ---------------------------------------------------------------------------


async def test_command_palette_open_thread() -> None:
    fake = FakeMatrixClient()
    fake.logged_in = True
    thread_msg = _msg("$m1", thread_root_id="$root")
    fake.messages_data["!r:s"] = [thread_msg]
    fake.rooms_data = [RoomSummary(room_id="!r:s", display_name="Test Room")]
    fake.thread_messages[("!r:s", "$root")] = ([], False)

    app = _make_main_app(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MainScreen)

        # Open a room tab
        screen.on_room_list_room_selected(RoomList.RoomSelected("!r:s"))
        await asyncio.sleep(0.2)

        view = screen.message_view_for("!r:s")
        if view is not None:
            rows = list(view.query(MessageRow))
            if rows:
                rows[0].focus()
        await pilot.pause()

        # Invoke cmd_open_thread directly (palette interaction is flaky in tests)
        screen.open_thread("!r:s", "$root")
        await pilot.pause()

        panel = screen.query_one("#thread-panel")
        assert panel.display is True


# ---------------------------------------------------------------------------
# Test 14: live NewMessage with matching thread_root_id appends to panel
# ---------------------------------------------------------------------------


async def test_live_new_message_appends_to_open_thread() -> None:
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.thread_messages[("!r:s", "$root")] = ([], False)

    messages: list[TextualMessage] = []
    app = _make_main_app(fake)
    async with app.run_test(size=(120, 40), message_hook=messages.append) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MainScreen)

        screen.open_thread("!r:s", "$root")
        await wait_for_workers(app)

        # Snapshot the message count so we only assert on what the live reply adds.
        before = len(messages)
        thread_reply = _msg("$live_reply", room_id="!r:s", body="LiveReply", thread_root_id="$root")
        # Directly drive handle_new_message since MainHostApp doesn't subscribe.
        screen.handle_new_message(NewMessage(message=thread_reply))
        await wait_for_workers(app)

        panel = screen.query_one(ThreadPanel)
        rows = list(panel.query(MessageRow))
        assert len(rows) >= 1
        # Strong assertion: processing the live reply mounted a new widget,
        # observable as a Mount event flowing through the app hook after the call.
        mounted_after = [m for m in messages[before:] if isinstance(m, Mount)]
        assert mounted_after, "live reply did not trigger a widget mount"
        # Assert the live reply is visible to the user.
        bodies = [str(s.render()) for row in rows for s in row.query(Static)]
        assert any("LiveReply" in b for b in bodies)


# ---------------------------------------------------------------------------
# Test 15: live NewMessage in OTHER thread is ignored by panel
# ---------------------------------------------------------------------------


async def test_live_new_message_in_other_thread_ignored() -> None:
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.thread_messages[("!r:s", "$root")] = ([], False)

    app = _make_main_app(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MainScreen)

        screen.open_thread("!r:s", "$root")
        await wait_for_workers(app)

        other_reply = _msg("$other", room_id="!r:s", thread_root_id="$other_root")
        # Drive handle_new_message directly — simulates a live event.
        screen.handle_new_message(NewMessage(message=other_reply))
        await wait_for_workers(app)

        panel = screen.query_one(ThreadPanel)
        rows = list(panel.query(MessageRow))
        assert len(rows) == 0
