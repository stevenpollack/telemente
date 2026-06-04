"""Tests for the MessageView widget (plan 0007).

All tests inject FakeMatrixClient — no real network.
A minimal host App mounts MessageView with the fake client.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Link, Static

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
    """Return rendered text from all Static children inside _MessageRow widgets."""
    rows = view.query(_MessageRow)
    parts: list[str] = []
    for row in rows:
        for static in row.query(Static):
            parts.append(str(static.render()))
    return "\n".join(parts)


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
        assert fake.sent_messages[0][:2] == ("!r:s", "hello")
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

        notice = view.query_one("#encryption-notice", Static)
        assert notice.display is True

        await view.load_room("!clear:s")
        await pilot.pause()
        assert notice.display is False


# ---------------------------------------------------------------------------
# Test 10: sent message appears immediately in timeline (regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sent_message_appears_immediately_in_timeline() -> None:
    """After pressing Enter, the composed message must appear in the timeline
    immediately — without switching rooms or waiting for a sync event.

    Regression: previously _do_send() only called send_text() and relied on
    the next NewMessage event from the sync loop to echo the message back.
    On a slow/absent server that echo never arrived, leaving the composer
    apparently broken.
    """
    fake = FakeMatrixClient()
    fake._logged_in = True
    fake._messages["!r:s"] = [_msg("$e1", "!r:s", "earlier message")]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)
        await view.load_room("!r:s")
        await pilot.pause()

        composer = view.query_one("#composer", Input)
        composer.focus()
        await pilot.pause()

        await pilot.press("h", "e", "l", "l", "o")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        rendered = _rendered_text(view)
        # The sent message must appear without any room switch
        assert "hello" in rendered, "sent message not visible in timeline immediately after send"
        # And it must appear after the pre-existing message
        assert rendered.index("earlier message") < rendered.index("hello")


# ---------------------------------------------------------------------------
# Test 11: media message renders a Link widget with filename label
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_media_message_renders_link_widget() -> None:
    """A message with media_url mounts a Link widget showing the filename."""
    fake = FakeMatrixClient()
    fake._logged_in = True
    fake._messages["!r:s"] = [
        Message(
            event_id="$m1",
            room_id="!r:s",
            sender="@alice:matrix.org",
            sender_display_name="Alice",
            body="photo.jpg",
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            media_url="https://example.com/media/photo.jpg",
            media_type="image",
        )
    ]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)
        await view.load_room("!r:s")
        await pilot.pause()

        links = view.query(Link)
        assert len(links) == 1
        link = links.first(Link)
        assert "photo.jpg" in link.text
        assert link.url == "https://example.com/media/photo.jpg"


# ---------------------------------------------------------------------------
# Test 12: message with reactions renders emoji + count chip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_with_reactions_renders_chips() -> None:
    """A Message with reactions shows a Static containing emoji and count."""
    fake = FakeMatrixClient()
    fake._logged_in = True
    fake._messages["!r:s"] = [
        Message(
            event_id="$e1",
            room_id="!r:s",
            sender="@alice:matrix.org",
            sender_display_name="Alice",
            body="hello",
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            reactions={"👍": ["@bob:matrix.org", "@carol:matrix.org"], "❤️": ["@dave:matrix.org"]},
        )
    ]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)
        await view.load_room("!r:s")
        await pilot.pause()

        rows = list(view.query(_MessageRow))
        assert len(rows) == 1
        row = rows[0]
        statics = list(row.query(Static))
        # At least 2 statics: body + reactions
        assert len(statics) >= 2
        reaction_texts = [str(s.render()) for s in statics]
        combined = " ".join(reaction_texts)
        assert "👍" in combined
        assert "2" in combined
        assert "❤️" in combined
        assert "1" in combined


# ---------------------------------------------------------------------------
# Test 13: press 'e' on a _MessageRow → emoji-input appears; submit sends reaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_react_binding_sends_reaction() -> None:
    """Focus a _MessageRow, press 'e', type an emoji, press Enter → reaction sent."""
    fake = FakeMatrixClient()
    fake._logged_in = True
    fake._messages["!r:s"] = [_msg("$e1", "!r:s", "hello")]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)
        await view.load_room("!r:s")
        await pilot.pause()

        # Focus the message row
        row = view.query_one(_MessageRow)
        row.focus()
        await pilot.pause()

        # Press 'e' to open emoji input
        await pilot.press("e")
        await pilot.pause()

        # emoji-input should now be visible
        emoji_input = view.query_one("#emoji-input", Input)
        assert emoji_input.display is True

        # Type emoji and submit
        await pilot.press("up", "down")  # clear any pending
        emoji_input.clear()
        await pilot.pause()
        await pilot.press("thumbs_up")  # or just type characters
        # Actually type via direct value set and submit
        emoji_input.value = "👍"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        # Reaction should be sent
        assert len(fake.sent_reactions) == 1
        assert fake.sent_reactions[0] == ("!r:s", "$e1", "👍")

        # emoji-input should be hidden
        assert emoji_input.display is False


# ---------------------------------------------------------------------------
# Test 14: press 'r' on a _MessageRow → reply-indicator shows; submit sends reply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_binding_sends_reply() -> None:
    """Focus a _MessageRow, press 'r' → reply-indicator visible; submit sends reply."""
    fake = FakeMatrixClient()
    fake._logged_in = True
    fake._messages["!r:s"] = [_msg("$parent", "!r:s", "original", sender_display_name="Bob")]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)
        await view.load_room("!r:s")
        await pilot.pause()

        # Focus the message row and press 'r'
        row = view.query_one(_MessageRow)
        row.focus()
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()

        # reply-indicator should be visible
        indicator = view.query_one("#reply-indicator", Static)
        assert indicator.display is True
        assert "Bob" in str(indicator.render())

        # Type reply in composer and submit
        composer = view.query_one("#composer", Input)
        composer.focus()
        await pilot.pause()
        await pilot.press("r", "e", "p", "l", "y")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        # sent_messages should have the reply
        assert len(fake.sent_messages) == 1
        room_id, body, reply_to = fake.sent_messages[0]
        assert room_id == "!r:s"
        assert body == "reply"
        assert reply_to == "$parent"

        # indicator should be hidden after send
        assert indicator.display is False
