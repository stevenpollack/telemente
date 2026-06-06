"""Tests for message right-click context menu (plan 0020, Part 3).

Tier-2 tests: FakeMatrixClient with scripted me() and can_redact_results.
Uses MainScreen as the app so ShowContextMenu is properly handled.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from textual.app import App, ComposeResult
from textual.widgets import Label, Static

import fakes as fakes_module
from telemente.matrix.models import Message, RoomSummary
from telemente.tui.screens.main import MainScreen
from telemente.tui.widgets.message_view import MessageRow, MessageView
from telemente.tui.widgets.room_list import RoomList

FakeMatrixClient = fakes_module.FakeMatrixClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(
    event_id: str,
    room_id: str,
    sender: str = "@alice:matrix.org",
    body: str = "hello",
) -> Message:
    return Message(
        event_id=event_id,
        room_id=room_id,
        sender=sender,
        sender_display_name=sender.split(":")[0].lstrip("@"),
        body=body,
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
    )


class HostApp(App[None]):
    """App that mounts MainScreen to handle ShowContextMenu properly."""

    def __init__(self, client: FakeMatrixClient) -> None:
        super().__init__()
        self._client = client

    def compose(self) -> ComposeResult:
        yield Label("host")

    def on_mount(self) -> None:
        self.push_screen(MainScreen(self._client))


async def _open_room_and_get_view(app: HostApp, room_id: str) -> MessageView:
    """Open a room tab and return its MessageView."""
    screen = app.screen
    assert isinstance(screen, MainScreen)
    room_list = screen.query_one(RoomList)
    room_list.set_rooms(app._client.rooms_data)
    screen.on_room_list_room_selected(RoomList.RoomSelected(room_id))
    await asyncio.sleep(0.15)
    view = screen.message_view_for(room_id)
    assert view is not None, "MessageView not found after opening room"
    return view


def _simulate_right_click(row: MessageRow) -> None:
    """Simulate a right-click on a MessageRow by posting MouseDown directly."""

    row.post_message(
        MessageRow.ContextMenuRequest(
            message=row.message,
            screen_x=5,
            screen_y=5,
        )
    )


def _menu_item_labels(app: App[None]) -> list[str]:
    """Return rendered text of all menu-item Statics in the active screen."""
    return [
        str(w.render()) for w in app.screen.query(Static) if "menu-item" in (w.classes or set())
    ]


def _is_disabled(app: App[None], label: str) -> bool:
    """Return True if the menu item with given label is disabled."""
    for w in app.screen.query(Static):
        if "menu-item" in (w.classes or set()) and label in str(w.render()):
            return "-disabled" in (w.classes or set())
    return False


# ---------------------------------------------------------------------------
# Test 1: right-click own message shows Edit and Delete enabled
# ---------------------------------------------------------------------------


async def test_right_click_own_message_shows_edit_delete() -> None:
    my_id = "@alice:matrix.org"
    room_id = "!room:server"
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.set_me(my_id, "Alice")
    fake.rooms_data = [RoomSummary(room_id=room_id, display_name="Room")]
    fake.messages_data[room_id] = [_msg("$ev1", room_id, sender=my_id)]

    app = HostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        view = await _open_room_and_get_view(app, room_id)
        await pilot.pause()

        rows = list(view.query(MessageRow))
        assert rows, "No MessageRows found"
        _simulate_right_click(rows[0])
        await pilot.pause()

        labels = _menu_item_labels(app)
        assert any("Edit" in lbl for lbl in labels), f"Edit missing from {labels}"
        assert any("Delete" in lbl for lbl in labels), f"Delete missing from {labels}"
        assert not _is_disabled(app, "Delete"), "Delete should be enabled for own message"


# ---------------------------------------------------------------------------
# Test 2: right-click other's message: no Edit; Delete disabled for normal user
# ---------------------------------------------------------------------------


async def test_right_click_other_message_no_edit_delete_for_normal_user() -> None:
    my_id = "@alice:matrix.org"
    other_id = "@bob:matrix.org"
    room_id = "!room:server"
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.set_me(my_id, "Alice")
    # can_redact_results defaults to False
    fake.rooms_data = [RoomSummary(room_id=room_id, display_name="Room")]
    fake.messages_data[room_id] = [_msg("$ev1", room_id, sender=other_id)]

    app = HostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        view = await _open_room_and_get_view(app, room_id)
        await pilot.pause()

        rows = list(view.query(MessageRow))
        assert rows
        _simulate_right_click(rows[0])
        await pilot.pause()

        labels = _menu_item_labels(app)
        assert not any("Edit" in lbl for lbl in labels), "Edit should not appear for other's msg"
        assert any("Delete" in lbl for lbl in labels), f"Delete missing from {labels}"
        assert _is_disabled(app, "Delete"), "Delete should be disabled for normal user"


# ---------------------------------------------------------------------------
# Test 3: moderator sees Delete enabled for other's message
# ---------------------------------------------------------------------------


async def test_right_click_other_message_delete_for_moderator() -> None:
    my_id = "@alice:matrix.org"
    other_id = "@bob:matrix.org"
    room_id = "!room:server"
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.set_me(my_id, "Alice")
    fake.can_redact_results[(room_id, other_id)] = True
    fake.rooms_data = [RoomSummary(room_id=room_id, display_name="Room")]
    fake.messages_data[room_id] = [_msg("$ev1", room_id, sender=other_id)]

    app = HostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        view = await _open_room_and_get_view(app, room_id)
        await pilot.pause()

        rows = list(view.query(MessageRow))
        assert rows
        _simulate_right_click(rows[0])
        await pilot.pause()

        assert not _is_disabled(app, "Delete"), "Delete should be enabled for moderator"


# ---------------------------------------------------------------------------
# Test 4: clicking "React" opens EmojiPickerScreen
# ---------------------------------------------------------------------------


async def test_react_item_opens_emoji_picker() -> None:
    from telemente.tui.screens.emoji_picker import EmojiPickerScreen

    my_id = "@alice:matrix.org"
    room_id = "!room:server"
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.set_me(my_id, "Alice")
    fake.rooms_data = [RoomSummary(room_id=room_id, display_name="Room")]
    fake.messages_data[room_id] = [_msg("$ev1", room_id, sender=my_id)]

    app = HostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        view = await _open_room_and_get_view(app, room_id)
        await pilot.pause()

        rows = list(view.query(MessageRow))
        assert rows
        _simulate_right_click(rows[0])
        await pilot.pause()

        react_items = [
            w
            for w in app.screen.query(Static)
            if "menu-item" in (w.classes or set()) and "React" in str(w.render())
        ]
        assert react_items, "React item not found in context menu"
        await pilot.click(react_items[0])
        await pilot.pause()

        assert isinstance(app.screen, EmojiPickerScreen), (
            f"Expected EmojiPickerScreen, got {type(app.screen).__name__}"
        )


# ---------------------------------------------------------------------------
# Test 5: clicking "Reply" posts a ReplyRequest
# ---------------------------------------------------------------------------


async def test_reply_item_posts_reply_request() -> None:
    my_id = "@alice:matrix.org"
    room_id = "!room:server"
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.set_me(my_id, "Alice")
    fake.rooms_data = [RoomSummary(room_id=room_id, display_name="Room")]
    fake.messages_data[room_id] = [_msg("$ev1", room_id, sender=my_id)]

    app = HostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        view = await _open_room_and_get_view(app, room_id)
        await pilot.pause()

        rows = list(view.query(MessageRow))
        assert rows
        _simulate_right_click(rows[0])
        await pilot.pause()

        reply_items = [
            w
            for w in app.screen.query(Static)
            if "menu-item" in (w.classes or set()) and "Reply" in str(w.render())
        ]
        assert reply_items, "Reply item not found in context menu"
        await pilot.click(reply_items[0])
        await pilot.pause()

        # The reply indicator should be shown.
        reply_indicator = view.query_one("#reply-indicator", Static)
        assert reply_indicator.display is True, "Reply indicator should be visible"


# ---------------------------------------------------------------------------
# Test 6: React via context menu sends reaction (Bug 3 — downstream of Bug 1)
# ---------------------------------------------------------------------------


async def test_react_via_context_menu_sends_reaction() -> None:
    """Open context menu → React → pick emoji → reaction sent to client."""
    from telemente.tui.screens.emoji_picker import EmojiPickerScreen

    my_id = "@alice:matrix.org"
    room_id = "!room:server"
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.set_me(my_id, "Alice")
    fake.rooms_data = [RoomSummary(room_id=room_id, display_name="Room")]
    fake.messages_data[room_id] = [_msg("$ev1", room_id, sender=my_id)]

    app = HostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        view = await _open_room_and_get_view(app, room_id)
        await pilot.pause()

        rows = list(view.query(MessageRow))
        assert rows
        _simulate_right_click(rows[0])
        await pilot.pause()

        react_items = [
            w
            for w in app.screen.query(Static)
            if "menu-item" in (w.classes or set()) and "React" in str(w.render())
        ]
        assert react_items, "React item not found in context menu"
        await pilot.click(react_items[0])
        await pilot.pause()

        # EmojiPickerScreen should now be on the stack.
        assert isinstance(app.screen, EmojiPickerScreen)

        # Click the first button in the emoji picker.
        from textual.widgets import Button as TxtButton

        picker_screen = app.screen
        assert isinstance(picker_screen, EmojiPickerScreen)
        buttons = list(picker_screen.query_one("#emoji-grid").query(TxtButton))
        assert buttons, "no emoji buttons in picker grid"
        first_emoji = str(buttons[0].label)
        await pilot.click(buttons[0])

        # Give the worker time: button click → dismiss → callback → run_worker.
        import asyncio as _asyncio

        for _ in range(20):
            await pilot.pause()
            await _asyncio.sleep(0.05)
            if fake.sent_reactions:
                break

        assert len(fake.sent_reactions) == 1, f"reaction not sent: {fake.sent_reactions}"
        assert fake.sent_reactions[0][2] == first_emoji


# ---------------------------------------------------------------------------
# Test 7: Delete shows confirmation before redacting (Bug 5)
# ---------------------------------------------------------------------------


async def test_delete_via_context_menu_shows_confirmation() -> None:
    """Clicking Delete in context menu pushes ConfirmScreen before redacting."""
    from telemente.tui.widgets.confirm_screen import ConfirmScreen

    my_id = "@alice:matrix.org"
    room_id = "!room:server"
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.set_me(my_id, "Alice")
    fake.rooms_data = [RoomSummary(room_id=room_id, display_name="Room")]
    fake.messages_data[room_id] = [_msg("$ev1", room_id, sender=my_id)]

    app = HostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        view = await _open_room_and_get_view(app, room_id)
        await pilot.pause()

        rows = list(view.query(MessageRow))
        assert rows
        _simulate_right_click(rows[0])
        await pilot.pause()

        delete_items = [
            w
            for w in app.screen.query(Static)
            if "menu-item" in (w.classes or set()) and "Delete" in str(w.render())
        ]
        assert delete_items, "Delete item not found in context menu"
        await pilot.click(delete_items[0])
        await pilot.pause()

        # Redact must NOT have been called yet.
        assert len(fake.redacted_messages) == 0, "redact called before confirmation"

        # ConfirmScreen must be showing.
        assert isinstance(app.screen, ConfirmScreen), (
            f"Expected ConfirmScreen, got {type(app.screen).__name__}"
        )


# ---------------------------------------------------------------------------
# Test 8: Delete confirmed → redact called
# ---------------------------------------------------------------------------


async def test_delete_confirmed_calls_redact() -> None:
    """After confirming the delete dialog, redact_message is called."""
    from textual.widgets import Button as TxtButton

    from telemente.tui.widgets.confirm_screen import ConfirmScreen

    my_id = "@alice:matrix.org"
    room_id = "!room:server"
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.set_me(my_id, "Alice")
    fake.rooms_data = [RoomSummary(room_id=room_id, display_name="Room")]
    fake.messages_data[room_id] = [_msg("$ev1", room_id, sender=my_id)]

    app = HostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        view = await _open_room_and_get_view(app, room_id)
        await pilot.pause()

        rows = list(view.query(MessageRow))
        assert rows
        _simulate_right_click(rows[0])
        await pilot.pause()

        delete_items = [
            w
            for w in app.screen.query(Static)
            if "menu-item" in (w.classes or set()) and "Delete" in str(w.render())
        ]
        assert delete_items
        await pilot.click(delete_items[0])
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen)
        yes_btn = app.screen.query_one("#btn-yes", TxtButton)
        await pilot.click(yes_btn)
        await pilot.pause()
        await pilot.pause()

        assert len(fake.redacted_messages) == 1
        assert fake.redacted_messages[0][1] == "$ev1"


# ---------------------------------------------------------------------------
# Test 9: Delete cancelled → redact NOT called
# ---------------------------------------------------------------------------


async def test_delete_cancelled_does_not_redact() -> None:
    """Cancelling the delete confirmation does NOT call redact_message."""
    from textual.widgets import Button as TxtButton

    from telemente.tui.widgets.confirm_screen import ConfirmScreen

    my_id = "@alice:matrix.org"
    room_id = "!room:server"
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.set_me(my_id, "Alice")
    fake.rooms_data = [RoomSummary(room_id=room_id, display_name="Room")]
    fake.messages_data[room_id] = [_msg("$ev1", room_id, sender=my_id)]

    app = HostApp(fake)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        view = await _open_room_and_get_view(app, room_id)
        await pilot.pause()

        rows = list(view.query(MessageRow))
        assert rows
        _simulate_right_click(rows[0])
        await pilot.pause()

        delete_items = [
            w
            for w in app.screen.query(Static)
            if "menu-item" in (w.classes or set()) and "Delete" in str(w.render())
        ]
        assert delete_items
        await pilot.click(delete_items[0])
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen)
        no_btn = app.screen.query_one("#btn-no", TxtButton)
        await pilot.click(no_btn)
        await pilot.pause()
        await pilot.pause()

        assert len(fake.redacted_messages) == 0
