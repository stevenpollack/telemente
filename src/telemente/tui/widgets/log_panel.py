"""LogPanel widget — real-time log viewer (plan 0014).

Tails the telemente log file and renders new lines into a RichLog.
Closeable via the ✕ button, ESC key, or command palette.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import ClassVar

from textual import work
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.events import Key
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Button, RichLog, Static

logger = logging.getLogger(__name__)

_TAIL_CHUNK = 16384  # bytes to read from end of file on first open
_POLL_INTERVAL = 0.25  # seconds between tail polls


class LogPanel(Widget):
    """Bottom panel that tails the telemente log file in real time.

    Posts ``LogPanel.CloseRequested`` when the user dismisses it (✕ button
    or ESC).  The parent screen is responsible for hiding the panel.
    """

    BINDINGS: ClassVar[list[BindingType]] = []

    DEFAULT_CSS = """
    LogPanel {
        height: 12;
        border: solid $primary;
        layout: vertical;
    }
    LogPanel #log-header {
        height: 1;
        background: $surface;
    }
    LogPanel #log-close {
        width: 3;
        min-width: 3;
        height: 1;
        border: none;
        padding: 0;
        background: $surface;
        color: $text-muted;
    }
    LogPanel #log-close:hover {
        background: $error;
        color: $text;
    }
    LogPanel #log-title {
        width: 1fr;
        content-align: left middle;
        padding: 0 1;
        color: $text-muted;
        text-style: bold;
    }
    LogPanel #log-output {
        height: 1fr;
    }
    """

    # ------------------------------------------------------------------
    # Message
    # ------------------------------------------------------------------

    class CloseRequested(TextualMessage):
        """Posted when the user requests to close this panel."""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(
        self,
        log_file: Path,
        *,
        max_lines: int = 500,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._log_file = log_file
        self._max_lines = max_lines

    def compose(self) -> ComposeResult:
        with Horizontal(id="log-header"):
            yield Button("✕", id="log-close")
            yield Static("Log Viewer", id="log-title")
        yield RichLog(
            id="log-output",
            highlight=True,
            markup=False,
            max_lines=self._max_lines,
            wrap=True,
        )

    def on_mount(self) -> None:
        self._start_tail()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "log-close":
            event.stop()
            self.post_message(LogPanel.CloseRequested())

    def on_key(self, event: Key) -> None:
        if event.key == "escape":
            event.stop()
            self.post_message(LogPanel.CloseRequested())

    # ------------------------------------------------------------------
    # Tail worker
    # ------------------------------------------------------------------

    @work(exclusive=True, exit_on_error=False)
    async def _start_tail(self) -> None:
        """Open the log file, emit the last chunk, then poll for new lines."""
        if not self._log_file.exists():
            logger.debug("LogPanel: log file not found: %s", self._log_file)
            return

        rich_log = self.query_one("#log-output", RichLog)

        try:
            with self._log_file.open("r", errors="replace") as f:
                # Seek near the end so we show recent content without reading the whole file.
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - _TAIL_CHUNK))
                chunk = f.read()
                for line in chunk.splitlines():
                    if line:
                        rich_log.write(line)

                # Tail: poll for new lines until the worker is cancelled.
                while True:
                    line = f.readline()
                    if line:
                        rich_log.write(line.rstrip("\n"))
                    else:
                        await asyncio.sleep(_POLL_INTERVAL)
        except Exception as exc:
            logger.warning("LogPanel tail error: %s", exc)
