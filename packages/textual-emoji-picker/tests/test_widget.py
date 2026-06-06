"""Tier-2 widget tests for EmojiPicker.

These are Textual pilot tests for widget behaviour.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.containers import Grid
from textual.widgets import Button, Input
from textual_emoji_picker import EmojiPicker

# ---------------------------------------------------------------------------
# Host apps
# ---------------------------------------------------------------------------


class PickerApp(App[None]):
    """Minimal app composing a bare EmojiPicker."""

    def __init__(self, **picker_kwargs: object) -> None:
        super().__init__()
        self._picker_kwargs = picker_kwargs
        self.selected: str | None = None
        self.cancelled = False

    def compose(self) -> ComposeResult:
        yield EmojiPicker(**self._picker_kwargs)  # type: ignore[arg-type]

    def on_emoji_picker_emoji_selected(self, event: EmojiPicker.EmojiSelected) -> None:
        self.selected = event.emoji

    def on_emoji_picker_cancelled(self, event: EmojiPicker.Cancelled) -> None:
        self.cancelled = True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_emoji_picker_mounts() -> None:
    app = PickerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(EmojiPicker) is not None


async def test_search_input_present() -> None:
    app = PickerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("Input") is not None


async def test_search_filters_grid() -> None:
    app = PickerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        search = app.query_one("#emoji-search", Input)
        await pilot.click(search)
        for ch in "grinning":
            await pilot.press(ch)
        await pilot.pause()
        grid = app.query_one("#emoji-grid", Grid)
        buttons = list(grid.query(Button))
        labels = {str(b.label) for b in buttons}
        assert "😀" in labels


async def test_search_no_match_clears_grid() -> None:
    app = PickerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        search = app.query_one("#emoji-search", Input)
        await pilot.click(search)
        for ch in "zzzzzzznomatch":
            await pilot.press(ch)
        await pilot.pause()
        grid = app.query_one("#emoji-grid", Grid)
        buttons = list(grid.query(Button))
        assert len(buttons) == 0


async def test_emoji_selected_message_posted() -> None:
    app = PickerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        grid = app.query_one("#emoji-grid", Grid)
        buttons = list(grid.query(Button))
        assert buttons
        await pilot.click(buttons[0])
        await pilot.pause()
    assert app.selected is not None
    assert len(app.selected) > 0


async def test_cancelled_message_on_escape() -> None:
    app = PickerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert app.cancelled is True


async def test_skin_tone_applied() -> None:
    """Selecting a Fitzpatrick swatch then clicking a capable emoji appends the modifier."""
    from textual.containers import Horizontal

    # Use max_emoji_version=1.0 + search to get a tiny visible grid containing 👍.
    app = PickerApp(max_emoji_version=14.0)
    async with app.run_test() as pilot:
        await pilot.pause()

        # Narrow the grid to hand emoji so 👍 is near the top and on-screen.
        search = app.query_one("#emoji-search", Input)
        await pilot.click(search)
        for ch in "thumbs up":
            await pilot.press(ch)
        await pilot.pause()

        # Select the light-skin swatch (U+1F3FB).
        row = app.query_one("#skin-tone-row", Horizontal)
        light_swatch = next(b for b in row.query(Button) if str(b.label) == "\U0001f3fb")
        await pilot.click(light_swatch)
        await pilot.pause()

        # Find 👍 (thumbs up — skin-tone-capable) in the filtered grid.
        from textual_emoji_picker._widget import _SKIN_TONE_CAPABLE

        grid = app.query_one("#emoji-grid", Grid)
        capable_btn = next(
            (b for b in grid.query(Button) if str(b.label) in _SKIN_TONE_CAPABLE), None
        )
        assert capable_btn is not None, "no skin-tone-capable emoji in filtered grid"
        base = str(capable_btn.label)
        await pilot.click(capable_btn)
        await pilot.pause()

    assert app.selected is not None
    assert "\U0001f3fb" in app.selected, f"modifier not in result {app.selected!r}"
    # The result must be based on the original codepoint (modulo stripped FE0F).
    assert app.selected.startswith(base.rstrip("️")), (
        f"result {app.selected!r} does not start with base {base!r}"
    )


async def test_skin_tone_not_applied_to_incapable() -> None:
    """Selecting a swatch then clicking 😀 (face, incapable) returns bare base."""
    from textual.containers import Horizontal

    app = PickerApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        # Select the medium swatch.
        row = app.query_one("#skin-tone-row", Horizontal)
        medium_swatch = next(b for b in row.query(Button) if str(b.label) == "\U0001f3fd")
        await pilot.click(medium_swatch)
        await pilot.pause()

        # Find 😀 (grinning face — not skin-tone-capable) in grid.
        grid = app.query_one("#emoji-grid", Grid)
        face_btn = next((b for b in grid.query(Button) if str(b.label) == "😀"), None)
        if face_btn is None:
            pytest.skip("😀 not visible in default grid (filtered out by max_emoji_version?)")
        await pilot.click(face_btn)
        await pilot.pause()

    assert app.selected == "😀", f"expected bare '😀', got {app.selected!r}"


async def test_category_filter_kwarg() -> None:
    """EmojiPicker(categories=...) restricts grid to that group."""
    app = PickerApp(categories=["smileys-emotion"])
    async with app.run_test() as pilot:
        await pilot.pause()
        grid = app.query_one("#emoji-grid", Grid)
        buttons = list(grid.query(Button))
        # Pizza 🍕 is in food-drink; it must not appear.
        labels = {str(b.label) for b in buttons}
        assert "🍕" not in labels, "food emoji should not appear when category is smileys-emotion"
        # There should be at least some emoji present.
        assert len(buttons) > 0


async def test_max_emoji_version_filter() -> None:
    """EmojiPicker(max_emoji_version=1.0) shows only very early emoji."""
    app = PickerApp(max_emoji_version=1.0)
    async with app.run_test() as pilot:
        await pilot.pause()
        grid = app.query_one("#emoji-grid", Grid)
        buttons = list(grid.query(Button))
        labels = {str(b.label) for b in buttons}
        assert "😀" in labels, "😀 (E1.0) should appear with max_emoji_version=1.0"
        assert "🫠" not in labels, "🫠 (E14.0) should be excluded with max_emoji_version=1.0"
