"""Tests for the MessageView widget (plan 0007).

All tests inject FakeMatrixClient — no real network.
A minimal host App mounts MessageView with the fake client.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

import fakes as fakes_module
from telemente.matrix.models import Message
from telemente.tui.widgets.message_view import MessageView, _MessageRow

FakeMatrixClient = fakes_module.FakeMatrixClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(
    event_id: str,
    room_id: str,
    body: str,
    sender_display_name: str = "Alice",
    timestamp: datetime | None = None,
) -> Message:
    return Message(
        event_id=event_id,
        room_id=room_id,
        sender="@alice:matrix.org",
        sender_display_name=sender_display_name,
        body=body,
        timestamp=timestamp or datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rendered_text(view: MessageView) -> str:
    """Return all rendered _MessageRow text joined together."""
    rows = view.query(_MessageRow)
    return "\n".join(str(row.render()) for row in rows)


# ---------------------------------------------------------------------------
# Host app
# ---------------------------------------------------------------------------


class HostApp(App[None]):
    """Minimal app that mounts MessageView with a FakeMatrixClient."""

    def __init__(self, client: FakeMatrixClient) -> None:
        super().__init__()
        self._client = client

    def compose(self) -> ComposeResult:
        yield MessageView(self._client, id="message-panel")


# ---------------------------------------------------------------------------
# Test 1: load_room renders messages in order (oldest first)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_room_renders_messages_in_order() -> None:
    fake = FakeMatrixClient()
    fake._logged_in = True
    fake._messages["!r:s"] = [
        _msg("$e1", "!r:s", "first"),
        _msg("$e2", "!r:s", "second"),
        _msg("$e3", "!r:s", "third"),
    ]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)
        await view.load_room("!r:s")
        await pilot.pause()

        rendered = _rendered_text(view)
        # All three bodies present
        assert "first" in rendered
        assert "second" in rendered
        assert "third" in rendered
        # Order: first before second before third
        assert rendered.index("first") < rendered.index("second") < rendered.index("third")


# ---------------------------------------------------------------------------
# Test 2: switching rooms replaces content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switching_rooms_replaces_content() -> None:
    fake = FakeMatrixClient()
    fake._logged_in = True
    fake._messages["!a:s"] = [
        _msg("$a1", "!a:s", "alpha one"),
        _msg("$a2", "!a:s", "alpha two"),
    ]
    fake._messages["!b:s"] = [
        _msg("$b1", "!b:s", "beta one"),
    ]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)

        await view.load_room("!a:s")
        await pilot.pause()

        await view.load_room("!b:s")
        await pilot.pause()

        assert view.current_room_id == "!b:s"
        rendered = _rendered_text(view)
        assert "beta one" in rendered
        assert "alpha one" not in rendered
        assert "alpha two" not in rendered


# ---------------------------------------------------------------------------
# Test 3: send on Enter calls send_text and clears composer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_on_enter_calls_send_text_and_clears() -> None:
    fake = FakeMatrixClient()
    fake._logged_in = True
    fake._messages["!r:s"] = []

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)
        await view.load_room("!r:s")
        await pilot.pause()

        composer = view.query_one("#composer", Input)
        composer.focus()
        await pilot.pause()

        # Type a message
        await pilot.press("h", "e", "l", "l", "o")
        await pilot.pause()
        assert composer.value == "hello"

        # Submit
        await pilot.press("enter")
        await pilot.pause()

        assert len(fake.sent_messages) == 1
        assert fake.sent_messages[0] == ("!r:s", "hello")
        assert composer.value == ""


# ---------------------------------------------------------------------------
# Test 4: empty composer submit is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_composer_submit_noop() -> None:
    fake = FakeMatrixClient()
    fake._logged_in = True
    fake._messages["!r:s"] = []

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)
        await view.load_room("!r:s")
        await pilot.pause()

        composer = view.query_one("#composer", Input)
        composer.focus()
        await pilot.pause()

        # Submit empty composer
        await pilot.press("enter")
        await pilot.pause()

        assert len(fake.sent_messages) == 0


# ---------------------------------------------------------------------------
# Test 5: append_message for current room appears at the end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_message_for_current_room() -> None:
    fake = FakeMatrixClient()
    fake._logged_in = True
    fake._messages["!a:s"] = [
        _msg("$e1", "!a:s", "existing body"),
    ]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)
        await view.load_room("!a:s")
        await pilot.pause()

        new_msg = _msg("$e2", "!a:s", "live body")
        view.append_message(new_msg)
        await pilot.pause()

        rendered = _rendered_text(view)
        assert "existing body" in rendered
        assert "live body" in rendered
        assert rendered.index("existing body") < rendered.index("live body")


# ---------------------------------------------------------------------------
# Test 6: append_message for other room is ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_message_other_room_ignored() -> None:
    fake = FakeMatrixClient()
    fake._logged_in = True
    fake._messages["!a:s"] = [
        _msg("$e1", "!a:s", "room A body"),
    ]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)
        await view.load_room("!a:s")
        await pilot.pause()

        other_msg = _msg("$e2", "!b:s", "room B body")
        view.append_message(other_msg)
        await pilot.pause()

        rendered = _rendered_text(view)
        assert "room A body" in rendered
        assert "room B body" not in rendered


# ---------------------------------------------------------------------------
# Test 7: all-encrypted room shows encryption notice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_encrypted_room_shows_notice() -> None:
    """When every message is undecryptable, an encryption notice appears."""
    fake = FakeMatrixClient()
    fake._logged_in = True
    fake._messages["!enc:s"] = [
        _msg("$e1", "!enc:s", "\U0001f512 Unable to decrypt"),
        _msg("$e2", "!enc:s", "\U0001f512 Unable to decrypt"),
    ]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)
        await view.load_room("!enc:s")
        await pilot.pause()

        from textual.widgets import Static

        notice = view.query_one("#encryption-notice", Static)
        assert notice.display is True
        assert "keys" in str(notice.render()).lower()


# ---------------------------------------------------------------------------
# Test 8: mixed room (some decryptable) hides encryption notice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_room_hides_encryption_notice() -> None:
    """When some messages are readable, no encryption notice is shown."""
    fake = FakeMatrixClient()
    fake._logged_in = True
    fake._messages["!mix:s"] = [
        _msg("$e1", "!mix:s", "\U0001f512 Unable to decrypt"),
        _msg("$e2", "!mix:s", "Hello, world!"),
    ]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)
        await view.load_room("!mix:s")
        await pilot.pause()

        from textual.widgets import Static

        notice = view.query_one("#encryption-notice", Static)
        assert notice.display is False


# ---------------------------------------------------------------------------
# Test 9: switching from encrypted to readable room hides notice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switching_from_encrypted_to_readable_hides_notice() -> None:
    """The notice disappears when switching to a room with readable messages."""
    fake = FakeMatrixClient()
    fake._logged_in = True
    fake._messages["!enc:s"] = [
        _msg("$e1", "!enc:s", "\U0001f512 Unable to decrypt"),
    ]
    fake._messages["!clear:s"] = [
        _msg("$e2", "!clear:s", "Readable message"),
    ]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)

        await view.load_room("!enc:s")
        await pilot.pause()

        from textual.widgets import Static

        notice = view.query_one("#encryption-notice", Static)
        assert notice.display is True

        await view.load_room("!clear:s")
        await pilot.pause()
        assert notice.display is False
