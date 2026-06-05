"""ThreadPanel widget — shows messages in a Matrix thread (plan 0023).

Reuses MessageRow from message_view.py. The panel is hidden by default and
toggled by MainScreen.thread_visible.
"""

from __future__ import annotations

import logging
from typing import ClassVar, Protocol

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Button, Static

from telemente.matrix.models import Message

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Client protocol
# ---------------------------------------------------------------------------


class _ThreadPanelClient(Protocol):
    """Structural protocol for the subset of MatrixClient used by ThreadPanel."""

    async def get_thread_messages(
        self, room_id: str, root_event_id: str, limit: int = 50
    ) -> tuple[list[Message], bool]: ...

    async def send_text(
        self,
        room_id: str,
        body: str,
        reply_to_event_id: str | None = None,
        thread_root_event_id: str | None = None,
    ) -> str: ...

    def me(self) -> tuple[str, str]: ...


# ---------------------------------------------------------------------------
# ThreadPanel
# ---------------------------------------------------------------------------


class ThreadPanel(Widget):
    """Scrollable panel showing the messages in one Matrix thread."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close_thread", "Close thread"),
    ]

    class CloseRequested(TextualMessage):
        """Posted when the user presses Escape or clicks the close button."""

    class ThreadReply(TextualMessage):
        """Posted when the user sends a reply from the thread composer."""

        def __init__(self, room_id: str, body: str, reply_to_event_id: str) -> None:
            super().__init__()
            self.room_id = room_id
            self.body = body
            self.reply_to_event_id = reply_to_event_id

    def __init__(
        self,
        client: _ThreadPanelClient,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._client = client
        self._room_id: str = ""
        self._root_event_id: str = ""
        self._has_more: bool = False
        self._event_ids_rendered: set[str] = set()

    def compose(self) -> ComposeResult:
        with Widget(id="thread-header"):
            yield Static("Thread", id="thread-title")
            yield Button("✕", id="thread-close")
        yield VerticalScroll(id="thread-messages")
        yield Static("Load more", id="thread-load-more")

    def on_mount(self) -> None:
        self.query_one("#thread-load-more", Static).display = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_thread(self, room_id: str, root_event_id: str) -> None:
        """Start loading the thread; replaces any previously loaded thread."""
        self._room_id = room_id
        self._root_event_id = root_event_id
        self._event_ids_rendered.clear()
        # Clear existing messages
        scroll = self.query_one("#thread-messages", VerticalScroll)
        for child in list(scroll.children):
            child.remove()
        self.run_worker(
            self._do_load(room_id, root_event_id),
            exclusive=True,
        )

    async def _do_load(self, room_id: str, root_event_id: str) -> None:
        """Async worker: fetch thread messages and populate the panel."""
        # Lazy import to avoid circular dependency
        from telemente.tui.widgets.message_view import MessageRow

        messages, has_more = await self._client.get_thread_messages(room_id, root_event_id)
        self._has_more = has_more
        scroll = self.query_one("#thread-messages", VerticalScroll)
        for msg in messages:
            if msg.event_id not in self._event_ids_rendered:
                self._event_ids_rendered.add(msg.event_id)
                await scroll.mount(MessageRow(msg))
        self.query_one("#thread-load-more", Static).display = has_more

    def append_message(self, msg: Message) -> None:
        """Append a live-arriving thread message (deduplicates by event_id)."""
        if msg.event_id in self._event_ids_rendered:
            return
        self._event_ids_rendered.add(msg.event_id)
        self.run_worker(self._do_append(msg), exclusive=False)

    async def _do_append(self, msg: Message) -> None:
        from telemente.tui.widgets.message_view import MessageRow

        scroll = self.query_one("#thread-messages", VerticalScroll)
        await scroll.mount(MessageRow(msg))

    def close(self) -> None:
        """Post CloseRequested to tell the parent to hide this panel."""
        self.post_message(self.CloseRequested())

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_close_thread(self) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "thread-close":
            event.stop()
            self.close()
