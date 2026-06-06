"""Integration tests for the emoji picker after plan 0027 extraction.

Confirms that:
1. The shim import still works (EmojiPickerScreen importable from telemente).
2. The reaction flow (push screen, pick emoji, handler receives result) is unchanged.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.containers import Grid
from textual.widgets import Button, Label

from telemente.tui.screens.emoji_picker import EmojiPickerScreen

# ---------------------------------------------------------------------------
# Test 1: shim import works
# ---------------------------------------------------------------------------


def test_shim_import_works() -> None:
    """EmojiPickerScreen must be importable from telemente.tui.screens.emoji_picker."""
    assert EmojiPickerScreen is not None
    assert issubclass(EmojiPickerScreen, object)


# ---------------------------------------------------------------------------
# Test 2: reaction flow unchanged
# ---------------------------------------------------------------------------


class EmojiPickerHostApp(App[str]):
    """Host app that pushes EmojiPickerScreen and captures the result."""

    def __init__(self) -> None:
        super().__init__()
        self.result: str | None = None

    def compose(self) -> ComposeResult:
        yield Label("host")

    def on_mount(self) -> None:
        def _cb(emoji: str | None) -> None:
            self.result = emoji

        self.push_screen(EmojiPickerScreen(), _cb)


@pytest.mark.asyncio
async def test_reaction_flow_unchanged() -> None:
    """Push EmojiPickerScreen, pick an emoji, assert the result is received."""
    app = EmojiPickerHostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, EmojiPickerScreen)
        grid = app.screen.query_one("#emoji-grid", Grid)
        buttons = list(grid.query(Button))
        assert buttons
        first_emoji = str(buttons[0].label)
        await pilot.click(buttons[0])
        await pilot.pause()

    assert app.result == first_emoji
    assert len(app.result) > 0
