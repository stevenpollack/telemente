"""Tests for the ContextMenu widget (plan 0020, Part 1).

Tier-2 tests: no real client needed — ContextMenu is a standalone widget.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from textual.app import App, ComposeResult
from textual.geometry import Offset
from textual.widgets import Static

from telemente.tui.widgets.context_menu import ContextMenu, MenuEntry, MenuItem

# ---------------------------------------------------------------------------
# Host apps
# ---------------------------------------------------------------------------


class MenuHostApp(App[None]):
    """Minimal app that mounts a ContextMenu directly."""

    def __init__(
        self,
        items: Sequence[MenuEntry],
        x: int = 10,
        y: int = 5,
    ) -> None:
        super().__init__()
        self._items = list(items)
        self._x = x
        self._y = y
        self.dismissed: bool = False
        self._active_menu: ContextMenu | None = None

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        menu = ContextMenu(self._items, self._x, self._y)
        self._active_menu = menu
        self.screen.mount(menu)

    def on_context_menu_dismissed(self, _: ContextMenu.Dismissed) -> None:
        self.dismissed = True
        self._active_menu = None

    def on_click(self, event: object) -> None:
        """Dismiss menu on outside click (mirrors MainScreen behaviour)."""
        if self._active_menu is not None:
            self._active_menu._dismiss()  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# Test 1: context menu appears at the given mouse position
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_menu_appears_at_mouse_position() -> None:
    called: list[str] = []
    items = [MenuItem("Action A", lambda: called.append("A"))]
    app = MenuHostApp(items, x=10, y=5)

    async with app.run_test() as pilot:
        await pilot.pause()
        menu = app.query_one(ContextMenu)
        assert menu.absolute_offset == Offset(10, 5)


# ---------------------------------------------------------------------------
# Test 2: Escape dismisses the menu
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_menu_escape_dismisses() -> None:
    items = [MenuItem("Action", lambda: None)]
    app = MenuHostApp(items)

    async with app.run_test() as pilot:
        await pilot.pause()
        # Menu should be in the DOM.
        assert len(list(app.query(ContextMenu))) == 1

        await pilot.press("escape")
        await pilot.pause()

        assert len(list(app.query(ContextMenu))) == 0
        assert app.dismissed is True


# ---------------------------------------------------------------------------
# Test 3: Enter activates the focused item
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_menu_enter_activates_item() -> None:
    called: list[str] = []
    items = [MenuItem("Item 1", lambda: called.append("1"))]
    app = MenuHostApp(items)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert called == ["1"]
        # Menu should dismiss after activation.
        assert len(list(app.query(ContextMenu))) == 0


# ---------------------------------------------------------------------------
# Test 4: disabled item is not activatable via Enter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_menu_disabled_item_not_activatable() -> None:
    called: list[str] = []
    items = [
        MenuItem("Enabled", lambda: called.append("enabled")),
        MenuItem("Disabled", lambda: called.append("disabled"), enabled=False),
    ]
    app = MenuHostApp(items)

    async with app.run_test() as pilot:
        await pilot.pause()
        # Navigate down to second item (disabled).
        await pilot.press("down")
        await pilot.pause()
        # Press enter — should not call the disabled callback.
        await pilot.press("enter")
        await pilot.pause()

        # The first enabled item is still focused after arrow key, so the
        # previous down moved back to the only enabled item (modulo cycling).
        # After down from index 0 (enabled[0]), _enabled_indices cycles; since
        # there's only one enabled item, focus stays at idx 0.
        # Enter activates the enabled item.
        assert "disabled" not in called


# ---------------------------------------------------------------------------
# Test 5: clicking outside the menu dismisses it via App.on_click
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_menu_outside_click_dismisses() -> None:
    items = [MenuItem("Action", lambda: None)]
    app = MenuHostApp(items, x=10, y=5)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(list(app.query(ContextMenu))) == 1

        # Post Click to the app — the App.on_click in MenuHostApp will dismiss.
        from textual.events import Click

        app.post_message(Click(None, 0, 0, 0, 0, 1, False, False, False))
        await pilot.pause()

        assert len(list(app.query(ContextMenu))) == 0
