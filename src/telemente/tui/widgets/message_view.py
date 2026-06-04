"""MessageView widget — scrollable message timeline with composer (plan 0007).

Displays the message history for a Matrix room and provides a text input
for composing and sending messages.  The widget never imports nio directly —
all protocol access goes through the injected client.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from typing import ClassVar, Protocol

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Input, Link, Static

from telemente.matrix.models import Message
from telemente.tui.colors import sender_color

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Client protocol
# ---------------------------------------------------------------------------


class _MessageViewClient(Protocol):
    """Structural protocol for the subset of MatrixClient used by MessageView."""

    async def messages(self, room_id: str, limit: int = 50) -> list[Message]: ...

    async def send_text(self, room_id: str, body: str) -> str: ...

    def me(self) -> tuple[str, str]: ...


# ---------------------------------------------------------------------------
# Per-message widget
# ---------------------------------------------------------------------------


_URL_RE = re.compile(r"(https?://\S+)")


def _linkify(text: str) -> str:
    """Wrap bare https?:// URLs in Textual link markup for OSC 8 hyperlinks."""
    return _URL_RE.sub(lambda m: f'[link="{m.group(1)}"]{m.group(1)}[/link]', text)


class _DateSeparator(Static):
    """A date separator rendered between messages from different days."""

    DEFAULT_CSS = """
    _DateSeparator {
        height: 1;
        padding: 0 1;
        text-align: center;
        color: $text-muted;
        text-style: bold;
    }
    """


class _MessageRow(Widget):
    """A single rendered message in the timeline.

    Renders header (sender + time) and body as a Static, then appends a
    focusable Link widget when the message has a media attachment.
    """

    DEFAULT_CSS = """
    _MessageRow {
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
    }
    _MessageRow Static {
        height: auto;
        text-wrap: wrap;
    }
    _MessageRow Link {
        height: 1;
        padding: 0;
        margin: 0;
    }
    """

    def __init__(self, message: Message) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        msg = self._message
        local_ts: datetime = msg.timestamp.astimezone()
        time_str = local_ts.strftime("%H:%M")
        color = sender_color(msg.sender)
        sender = msg.sender_display_name
        body = _linkify(msg.body)
        header_body = f"[bold {color}]{sender}[/bold {color}] [dim]{time_str}[/dim]\n{body}"
        yield Static(header_body, markup=True)
        if msg.media_url:
            icon = {"image": "🖼", "video": "🎬", "audio": "🎵"}.get(msg.media_type or "", "📎")
            label = f"{icon} {msg.body or msg.media_type or 'attachment'}"
            yield Link(label, url=msg.media_url)


# ---------------------------------------------------------------------------
# MessageView
# ---------------------------------------------------------------------------


class MessageView(Widget):
    """Center panel: scrollable message timeline + composer input.

    Public API
    ----------
    load_room(room_id)       Fetch history and render it (replaces current content).
    append_message(message)  Add a live-arriving message (filtered by current room).
    clear()                  Remove all rendered messages.
    current_room_id          The currently loaded room, or None.
    """

    BINDINGS: ClassVar[list[BindingType]] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(
        self,
        client: _MessageViewClient,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._client = client
        self._current_room_id: str | None = None
        self._rendered_event_ids: set[str] = set()

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="message-timeline"):
            pass
        yield Static(
            "",
            id="encryption-notice",
            classes="encryption-notice",
        )
        yield Input(id="composer", placeholder="Message…")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def current_room_id(self) -> str | None:
        """The currently loaded room ID, or None if no room is loaded."""
        return self._current_room_id

    async def load_room(self, room_id: str) -> None:
        """Fetch message history for *room_id* and render it.

        Replaces any previously loaded content.  Auto-scrolls to the bottom.
        Shows an encryption notice if all messages are undecryptable.
        """
        self._current_room_id = room_id
        self.clear()
        messages = await self._client.messages(room_id)
        timeline = self.query_one("#message-timeline", VerticalScroll)
        self._render_messages(timeline, messages)
        self._scroll_to_bottom()
        self._update_encryption_notice(messages)

    def append_message(self, message: Message) -> None:
        """Append a live message if it belongs to the current room.

        Deduplicates by event_id so optimistic local echo and the subsequent
        sync echo of the same event don't both appear.
        """
        if message.room_id != self._current_room_id:
            return
        if message.event_id in self._rendered_event_ids:
            return
        self._rendered_event_ids.add(message.event_id)
        timeline = self.query_one("#message-timeline", VerticalScroll)
        timeline.mount(_MessageRow(message))
        self._scroll_to_bottom()

    def clear(self) -> None:
        """Remove all rendered message rows and date separators."""
        timeline = self.query_one("#message-timeline", VerticalScroll)
        for widget in list(timeline.query("_MessageRow, _DateSeparator")):
            widget.remove()
        self._rendered_event_ids.clear()
        self.query_one("#encryption-notice", Static).display = False

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Send message on Enter in #composer (non-empty, room loaded)."""
        if event.input.id != "composer":
            return
        text = event.value.strip()
        if not text or self._current_room_id is None:
            return
        room_id = self._current_room_id
        event.input.clear()
        self.run_worker(self._do_send(room_id, text), exclusive=False)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _do_send(self, room_id: str, body: str) -> None:
        """Send a message and echo it immediately into the local timeline."""
        logger.debug("Sending to %s: %s", room_id, body)
        user_id, display_name = self._client.me()
        event_id = await self._client.send_text(room_id, body)
        if not event_id:
            event_id = f"$local:{room_id}:{body[:16]}"
        local_msg = Message(
            event_id=event_id,
            room_id=room_id,
            sender=user_id,
            sender_display_name=display_name,
            body=body,
            timestamp=datetime.now(tz=UTC),
        )
        self.append_message(local_msg)

    def _render_messages(self, timeline: VerticalScroll, messages: list[Message]) -> None:
        """Render messages with date separators between different days."""
        last_date: date | None = None
        for msg in messages:
            self._rendered_event_ids.add(msg.event_id)
            msg_date = msg.timestamp.astimezone().date()
            if msg_date != last_date:
                timeline.mount(_DateSeparator(self._format_date(msg_date)))
                last_date = msg_date
            timeline.mount(_MessageRow(msg))

    @staticmethod
    def _format_date(d: date) -> str:
        """Format a date for the separator. Shows relative labels for recent dates."""
        today = date.today()
        delta = (today - d).days
        if delta == 0:
            return "— Today —"
        if delta == 1:
            return "— Yesterday —"
        if delta < 7:
            return f"— {d.strftime('%A')} —"
        return f"— {d.strftime('%d %b %Y')} —"

    def _update_encryption_notice(self, messages: list[Message]) -> None:
        """Show a notice if all messages are undecryptable."""
        notice = self.query_one("#encryption-notice", Static)
        if not messages:
            notice.display = False
            return
        all_encrypted = all("\U0001f512 Unable to decrypt" in m.body for m in messages)
        if all_encrypted:
            notice.update(
                "\U0001f512 All messages in this room are encrypted. "
                "Session keys from other devices have not been shared "
                "with telemente yet — messages will appear once keys arrive."
            )
            notice.display = True
        else:
            notice.display = False

    def _scroll_to_bottom(self) -> None:
        """Scroll the timeline to the bottom."""
        timeline = self.query_one("#message-timeline", VerticalScroll)
        timeline.scroll_end(animate=False)
