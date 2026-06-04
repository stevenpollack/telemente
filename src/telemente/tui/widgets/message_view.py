"""MessageView widget — scrollable message timeline with composer (plan 0007).

Displays the message history for a Matrix room and provides a text input
for composing and sending messages.  The widget never imports nio directly —
all protocol access goes through the injected client.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import ClassVar, Protocol

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Input, Static

from telemente.matrix.models import Message

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Client protocol
# ---------------------------------------------------------------------------


class _MessageViewClient(Protocol):
    """Structural protocol for the subset of MatrixClient used by MessageView."""

    async def messages(self, room_id: str, limit: int = 50) -> list[Message]: ...

    async def send_text(self, room_id: str, body: str) -> None: ...


# ---------------------------------------------------------------------------
# Per-message widget
# ---------------------------------------------------------------------------


class _MessageRow(Static):
    """A single rendered message row: ``HH:MM  sender: body``."""

    DEFAULT_CSS = """
    _MessageRow {
        height: auto;
        padding: 0 1;
    }
    """

    def __init__(self, message: Message) -> None:
        local_ts: datetime = message.timestamp.astimezone()
        time_str = local_ts.strftime("%H:%M")
        text = f"{time_str}  {message.sender_display_name}: {message.body}"
        super().__init__(text)


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

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="message-timeline"):
            pass
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
        """
        logger.info("load_room: room_id=%s", room_id)
        self._current_room_id = room_id
        self.clear()
        messages = await self._client.messages(room_id)
        logger.info("load_room: fetched %d messages room_id=%s", len(messages), room_id)
        timeline = self.query_one("#message-timeline", VerticalScroll)
        for msg in messages:
            timeline.mount(_MessageRow(msg))
        self._scroll_to_bottom()

    def append_message(self, message: Message) -> None:
        """Append a live message if it belongs to the current room."""
        if message.room_id != self._current_room_id:
            return
        timeline = self.query_one("#message-timeline", VerticalScroll)
        timeline.mount(_MessageRow(message))
        self._scroll_to_bottom()

    def clear(self) -> None:
        """Remove all rendered message rows from the timeline."""
        timeline = self.query_one("#message-timeline", VerticalScroll)
        for row in timeline.query(_MessageRow):
            row.remove()

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
        """Coroutine that performs the actual send_text call."""
        logger.info("send_text: room=%s", room_id)
        logger.debug("send_text: body preview room=%s body=%.60r", room_id, body)
        await self._client.send_text(room_id, body)

    def _scroll_to_bottom(self) -> None:
        """Scroll the timeline to the bottom."""
        timeline = self.query_one("#message-timeline", VerticalScroll)
        timeline.scroll_end(animate=False)
