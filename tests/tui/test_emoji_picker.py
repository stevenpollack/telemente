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
        # Wait longer than the 150 ms search debounce.
        await pilot.pause(delay=0.2)

        from textual.containers import Grid

        grid = app.screen.query_one("#emoji-grid", Grid)
        buttons = list(grid.query(Button))
        # After searching "heart" the full Unicode set still yields results, and
        # the count must be non-zero but smaller than the unfiltered set.
        assert buttons, "no buttons after 'heart' search"
        # Every curated heart emoji should be present in the filtered results
        # (the full set is a superset of the curated set).  Strip Fitzpatrick
        # modifiers before comparing — a persisted skin tone makes capable
        # emoji show their toned variant (e.g. 🫶🏻 instead of 🫶).
        _MODS = {"\U0001f3fb", "\U0001f3fc", "\U0001f3fd", "\U0001f3fe", "\U0001f3ff"}

        def _strip_tone(s: str) -> str:
            for m in _MODS:
                s = s.replace(m, "")
            return s

        curated_hearts = {cp for cp, name in REACTION_EMOJI if "heart" in name.lower()}
        result_bases = {_strip_tone(str(b.label)) for b in buttons}
        for cp in curated_hearts:
            assert cp in result_bases, (
                f"Curated heart emoji {cp!r} missing from 'heart' search results"
            )


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
# Test 4b: Escape does NOT cause a double-dismiss (regression for ScreenStackError)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emoji_picker_escape_no_double_dismiss() -> None:
    """Pressing Escape must dismiss exactly once.

    Before the fix, EmojiPickerScreen had its own Escape binding that called
    self.dismiss("") *and* EmojiPicker.Cancelled bubbled up to
    on_emoji_picker_cancelled which called self.dismiss("") again, causing
    a ScreenStackError because the screen was already gone (double-dismiss).
    """
    from textual.app import ScreenStackError

    app = PickerHostApp()
    raised: list[Exception] = []

    original_handler = app._handle_exception  # private but stable across Textual versions

    def _capture(exc: Exception) -> None:
        raised.append(exc)
        original_handler(exc)

    app._handle_exception = _capture  # type: ignore[assignment]  # monkey-patch for test

    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, EmojiPickerScreen)
        await pilot.press("escape")
        await pilot.pause()
        # After Escape, EmojiPickerScreen must have been popped — base screen is active.
        assert not isinstance(app.screen, EmojiPickerScreen), (
            "EmojiPickerScreen still on stack after Escape"
        )

    screen_stack_errors = [e for e in raised if isinstance(e, ScreenStackError)]
    assert not screen_stack_errors, (
        f"ScreenStackError raised on Escape (double-dismiss): {screen_stack_errors}"
    )
    assert app.result == "", f"expected empty string result, got {app.result!r}"


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

        # Trigger a filter by routing the event through the EmojiPicker widget,
        # which is the actual owner of on_input_changed after the refactor.
        from textual_emoji_picker import EmojiPicker as _EmojiPicker

        picker = app.screen.query_one(_EmojiPicker)
        search = picker.query_one("#emoji-search", Input)
        search.value = "heart"
        picker.on_input_changed(Input.Changed(search, search.value))
        # Wait longer than the 150 ms search debounce.
        await pilot.pause(delay=0.2)

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
