"""Tests for EmojiPickerScreen (plan 0020, Part 5 / plan 0021 Bug 4).

Tier-2 tests: no Matrix client needed for the picker itself.
Integration test for picker + MessageView uses FakeMatrixClient.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, Label

import fakes as fakes_module
from telemente.matrix.models import Message
from telemente.tui.screens.emoji_picker import (
    REACTION_EMOJI,
    SKIN_TONE_CAPABLE,
    EmojiPickerScreen,
)
from telemente.tui.widgets.message_view import MessageView

FakeMatrixClient = fakes_module.FakeMatrixClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(event_id: str, room_id: str, body: str = "hello") -> Message:
    return Message(
        event_id=event_id,
        room_id=room_id,
        sender="@alice:matrix.org",
        sender_display_name="Alice",
        body=body,
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Host apps
# ---------------------------------------------------------------------------


class PickerHostApp(App[str]):
    """App that pushes EmojiPickerScreen immediately on mount."""

    def __init__(self) -> None:
        super().__init__()
        self.result: str | None = None

    def compose(self) -> ComposeResult:
        yield Label("host")

    def on_mount(self) -> None:
        def _cb(emoji: str | None) -> None:
            self.result = emoji

        self.push_screen(EmojiPickerScreen(), _cb)


class MessageViewHostApp(App[None]):
    """Minimal app that mounts MessageView with a FakeMatrixClient."""

    def __init__(self, client: FakeMatrixClient) -> None:
        super().__init__()
        self._client = client

    def compose(self) -> ComposeResult:
        yield MessageView(self._client, id="view")


# ---------------------------------------------------------------------------
# Test 1: emoji grid contains buttons
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emoji_picker_displays_emoji_grid() -> None:
    app = PickerHostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # The EmojiPickerScreen is the active screen; query from it.
        assert isinstance(app.screen, EmojiPickerScreen)
        buttons = list(app.screen.query(Button))
        assert len(buttons) >= len(REACTION_EMOJI)


# ---------------------------------------------------------------------------
# Test 2: search filters the emoji grid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emoji_picker_search_filters_results() -> None:
    app = PickerHostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, EmojiPickerScreen)
        search = app.screen.query_one("#emoji-search", Input)
        await pilot.click(search)
        for ch in "heart":
            await pilot.press(ch)
        await pilot.pause()

        from textual.containers import Grid

        grid = app.screen.query_one("#emoji-grid", Grid)
        buttons = list(grid.query(Button))
        labels = [str(b.label) for b in buttons]
        # All remaining buttons should be heart-related emoji.
        heart_emojis = {cp for cp, name in REACTION_EMOJI if "heart" in name.lower()}
        for label in labels:
            assert label in heart_emojis, f"Non-heart emoji {label!r} remained after 'heart' search"


# ---------------------------------------------------------------------------
# Test 3: clicking an emoji dismisses with that emoji
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emoji_picker_click_dismisses_with_emoji() -> None:
    from textual.containers import Grid

    app = PickerHostApp()
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


# ---------------------------------------------------------------------------
# Test 4: Escape dismisses with empty string
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emoji_picker_escape_dismisses_with_none() -> None:
    app = PickerHostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.result == ""


# ---------------------------------------------------------------------------
# Test 5: integration — "React" via context menu sends reaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emoji_picker_integration_sends_reaction() -> None:
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.set_me("@alice:matrix.org", "Alice")
    room_id = "!room:server"
    event_id = "$ev1:server"
    fake.messages_data[room_id] = [_msg(event_id, room_id)]

    app = MessageViewHostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MessageView)
        await view.load_room(room_id)
        await pilot.pause()

        # Trigger the emoji picker via _open_emoji_picker_for.
        view.open_emoji_picker_for(event_id)
        await pilot.pause()

        # The EmojiPickerScreen should now be on the stack.
        assert isinstance(app.screen, EmojiPickerScreen)
        from textual.containers import Grid

        grid = app.screen.query_one("#emoji-grid", Grid)
        buttons = list(grid.query(Button))
        assert buttons
        first_emoji = str(buttons[0].label)

        await pilot.click(buttons[0])
        await pilot.pause()

        # The reaction should have been sent.
        assert fake.sent_reactions == [(room_id, event_id, first_emoji)]


# ---------------------------------------------------------------------------
# Test 6: no tooltip flicker — Bug 4 fix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emoji_picker_buttons_have_no_tooltip() -> None:
    """Emoji buttons must NOT have tooltip set (prevents hover flicker, Bug 4)."""
    app = PickerHostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, EmojiPickerScreen)
        buttons = list(app.screen.query(Button))
        assert buttons
        for btn in buttons[:5]:
            assert not btn.tooltip, f"button {btn.label!r} has tooltip set (Bug 4)"


# ---------------------------------------------------------------------------
# Test 7: diff-based update reuses buttons — Bug 4 fix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emoji_picker_filter_reuses_existing_buttons() -> None:
    """After filtering, at least one button widget is reused (not recreated)."""
    app = PickerHostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, EmojiPickerScreen)

        before_ids = {id(b) for b in app.screen.query(Button)}

        # Trigger a filter that returns a non-empty subset.
        search = app.screen.query_one("#emoji-search", Input)
        search.value = "heart"
        app.screen.on_input_changed(Input.Changed(search, search.value))
        await pilot.pause()

        after_buttons = list(app.screen.query(Button))
        after_ids = {id(b) for b in after_buttons}
        assert after_buttons, "no buttons after filter"
        shared = before_ids & after_ids
        assert shared, "no button widgets reused after filter (diff-update not working)"


# ---------------------------------------------------------------------------
# Test 8: emoji grid uses fixed row height to prevent layout recalc on hover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emoji_picker_grid_rows_not_auto() -> None:
    """The emoji grid must use a fixed grid-row height, not 'auto'.

    Regression test for Bug 2 (hover flicker): grid-rows: auto forces the Grid
    to recalculate row heights on every hover-induced button repaint, causing
    the whole grid to flicker. A fixed row height (e.g. 2) prevents this.
    """
    from textual.css.scalar import Unit

    app = PickerHostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, EmojiPickerScreen)
        from textual.containers import Grid

        grid = app.screen.query_one("#emoji-grid", Grid)
        grid_rows = grid.styles.grid_rows
        assert grid_rows, "grid-rows not set on #emoji-grid"
        for scalar in grid_rows:
            assert scalar.unit != Unit.AUTO, (
                f"grid-rows uses 'auto' — hover flicker fix not applied: {scalar}"
            )


# ---------------------------------------------------------------------------
# Test 9: skin-tone selector row is present in the picker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skin_tone_selector_present() -> None:
    """The picker must contain a #skin-tone-row with swatch buttons."""
    app = PickerHostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, EmojiPickerScreen)
        from textual.containers import Horizontal

        row = app.screen.query_one("#skin-tone-row", Horizontal)
        swatches = list(row.query(Button))
        # 1 "none/default" button + 5 Fitzpatrick modifier buttons
        assert len(swatches) == 6, f"expected 6 swatch buttons, got {len(swatches)}"
        # Modifier codepoints U+1F3FB-U+1F3FF must each appear
        modifier_labels = {str(b.label) for b in swatches}
        for cp in ("\U0001f3fb", "\U0001f3fc", "\U0001f3fd", "\U0001f3fe", "\U0001f3ff"):
            assert cp in modifier_labels, f"modifier {cp!r} not found in swatch row"


# ---------------------------------------------------------------------------
# Test 10: skin-tone modifier applied to a capable emoji
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skin_tone_applied_to_capable_emoji() -> None:
    """Selecting light-skin swatch then clicking a skin-tone-capable emoji
    must dismiss with base + U+1F3FB modifier."""
    # Find a base that is skin-tone capable
    capable_base = next(cp for cp, _name in REACTION_EMOJI if cp in SKIN_TONE_CAPABLE)

    app = PickerHostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, EmojiPickerScreen)

        # Click the light-skin swatch (U+1F3FB)
        from textual.containers import Grid, Horizontal

        row = app.screen.query_one("#skin-tone-row", Horizontal)
        swatches = list(row.query(Button))
        light_swatch = next(b for b in swatches if str(b.label) == "\U0001f3fb")
        await pilot.click(light_swatch)
        await pilot.pause()

        # Find the capable emoji button in the grid and click it
        grid = app.screen.query_one("#emoji-grid", Grid)
        capable_btn = next(b for b in grid.query(Button) if str(b.label) == capable_base)
        await pilot.click(capable_btn)
        await pilot.pause()

    assert app.result is not None
    assert app.result.startswith(capable_base.rstrip("️")), (
        f"result {app.result!r} does not start with base {capable_base!r}"
    )
    assert "\U0001f3fb" in app.result, f"modifier U+1F3FB not found in result {app.result!r}"


# ---------------------------------------------------------------------------
# Test 11: skin-tone modifier NOT applied to an incapable emoji
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skin_tone_ignored_for_incapable_emoji() -> None:
    """Selecting a swatch then clicking a non-skin-tone-capable emoji
    must dismiss with the bare base (no modifier appended)."""
    incapable_base = next(cp for cp, _name in REACTION_EMOJI if cp not in SKIN_TONE_CAPABLE)

    app = PickerHostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, EmojiPickerScreen)

        # Select medium-skin swatch (U+1F3FD)
        from textual.containers import Grid, Horizontal

        row = app.screen.query_one("#skin-tone-row", Horizontal)
        swatches = list(row.query(Button))
        medium_swatch = next(b for b in swatches if str(b.label) == "\U0001f3fd")
        await pilot.click(medium_swatch)
        await pilot.pause()

        # Click an incapable emoji (e.g. a face or heart)
        grid = app.screen.query_one("#emoji-grid", Grid)
        incapable_btn = next(b for b in grid.query(Button) if str(b.label) == incapable_base)
        await pilot.click(incapable_btn)
        await pilot.pause()

    assert app.result == incapable_base, (
        f"expected bare base {incapable_base!r}, got {app.result!r}"
    )


# ---------------------------------------------------------------------------
# Test 12: no swatch selected → bare base returned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_no_modifier_returns_base() -> None:
    """With no swatch selected (default), pressing any emoji returns the bare base."""
    app = PickerHostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, EmojiPickerScreen)

        from textual.containers import Grid

        grid = app.screen.query_one("#emoji-grid", Grid)
        buttons = list(grid.query(Button))
        assert buttons
        first_base = str(buttons[0].label)
        await pilot.click(buttons[0])
        await pilot.pause()

    assert app.result == first_base, f"expected bare base {first_base!r}, got {app.result!r}"
