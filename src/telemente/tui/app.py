"""The root Textual application.

This is a placeholder for the v0.1.0 scaffold. Real behaviour (login screen,
the three-panel main screen, sync integration) is implemented per the documents
in ``plans/``.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.widgets import Footer, Header, Static


class TelementeApp(App[None]):
    """Top-level telemente application."""

    TITLE = "telemente"
    CSS_PATH = "styles/app.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "telemente — not yet implemented.\n\n"
            "This is the v0.1.0 scaffold. Features are implemented per plans/.",
            id="placeholder",
        )
        yield Footer()
