"""Reusable confirmation modal screen (plan 0020).

Extracted from commands.py so both commands.py and main.py can import it
without circular dependencies.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ConfirmScreen(ModalScreen[bool]):
    """A minimal Yes / No confirmation modal screen."""

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    ConfirmScreen > Vertical {
        width: 50;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }
    ConfirmScreen Label {
        width: 1fr;
        content-align: center middle;
    }
    ConfirmScreen Horizontal {
        height: auto;
        align: center middle;
    }
    ConfirmScreen Button {
        margin: 1 1;
    }
    """

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._prompt)
            with Horizontal():
                yield Button("Yes", id="btn-yes", variant="error")
                yield Button("No", id="btn-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-yes")
