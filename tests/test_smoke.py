"""Smoke tests proving the scaffold imports and the Textual harness works."""

from __future__ import annotations

from textual.widgets import Static

import telemente
from telemente.tui.app import TelementeApp


def test_version() -> None:
    assert telemente.__version__ == "0.1.0"


async def test_app_boots() -> None:
    """The placeholder app mounts and shows its placeholder text."""
    app = TelementeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        placeholder = app.query_one("#placeholder", Static)
        assert "not yet implemented" in str(placeholder.render())
