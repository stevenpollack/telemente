"""Tests for the ContextMenu widget (plan 0020, Part 1 / plan 0021).

Tier-2 tests: no real client needed — ContextMenu is a standalone widget.
"""

from __future__ import annotations

import pathlib
from collections.abc import Sequence

import pytest
from textual.app import App, ComposeResult
from textual.geometry import Offset
from textual.widgets import Label, Static

import fakes as fakes_module
from telemente.matrix.models import RoomSummary
from telemente.tui.screens.main import MainScreen
from telemente.tui.widgets.context_menu import ContextMenu, MenuEntry, MenuItem

FakeMatrixClient = fakes_module.FakeMatrixClient

_APP_TCSS = str(
    pathlib.Path(__file__).parent.parent.parent
    / "src"
    / "telemente"
    / "tui"
    / "styles"
    / "app.tcss"
)

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


# ---------------------------------------------------------------------------
# Test 6: click on menu item fires action BEFORE dismiss (Bug 1)
# ---------------------------------------------------------------------------


async def test_click_item_fires_action_before_dismiss() -> None:
    """Clicking a menu item invokes the callback before the menu is dismissed."""
    call_log: list[str] = []

    def _action() -> None:
        call_log.append("action")

    items = [MenuItem("Click me", _action)]
    # Use a mid-screen position so the Static is within the visible viewport.
    app = MenuHostApp(items, x=5, y=5)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        menu = app.query_one(ContextMenu)

        # Intercept _dismiss to record when it fires relative to the action.
        _real_dismiss = menu._dismiss  # pyright: ignore[reportPrivateUsage]

        def _tracked() -> None:
            call_log.append("dismiss")
            _real_dismiss()

        menu._dismiss = _tracked  # type: ignore[method-assign]

        # Find the first menu-item Static.
        items_statics = [w for w in app.screen.query(Static) if "menu-item" in (w.classes or set())]
        assert items_statics, "no menu-item Static found"
        await pilot.click(items_statics[0])
        await pilot.pause()

    assert "action" in call_log, f"action not called: {call_log}"
    assert "dismiss" in call_log, f"dismiss not called: {call_log}"
    action_idx = call_log.index("action")
    dismiss_idx = call_log.index("dismiss")
    assert action_idx < dismiss_idx, f"action did not fire before dismiss: {call_log}"


# ---------------------------------------------------------------------------
# Test 7: menu width does not expand to terminal width (Bug 7)
# ---------------------------------------------------------------------------


async def test_context_menu_menu_item_not_using_1fr() -> None:
    """ContextMenu .menu-item must not use width: 1fr (Bug 7 fix verification).

    width: 1fr inside width: auto causes circular expansion in Textual's CSS
    engine. The fix replaces it with width: auto so items size to their content.
    """
    items = [MenuItem("Short", lambda: None)]
    app = MenuHostApp(items, x=0, y=0)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        menu = app.query_one(ContextMenu)
        item_statics = [w for w in menu.query(Static) if "menu-item" in (w.classes or set())]
        assert item_statics, "no .menu-item Static found in ContextMenu"
        for item_static in item_statics:
            # Verify the CSS computed styles do not use a fractional unit.
            # In Textual, styles.width is a Scalar; a fractional unit would have
            # unit == Unit.FRACTION. We assert it is NOT fractional.
            from textual.css.scalar import Unit

            w = item_static.styles.width
            if w is not None:
                assert w.unit != Unit.FRACTION, (
                    f".menu-item width uses 1fr — Bug 7 fix not applied: {w}"
                )


# ---------------------------------------------------------------------------
# Test 8: ContextMenu layer + screen layers declaration (Bug 1 regression)
# ---------------------------------------------------------------------------


class MainScreenHostApp(App[None]):
    """Host app that loads app.tcss via CSS_PATH so layer declarations apply."""

    CSS_PATH = _APP_TCSS

    def __init__(self, client: FakeMatrixClient) -> None:
        super().__init__()
        self.client = client

    def compose(self) -> ComposeResult:
        yield Label("host")

    def on_mount(self) -> None:
        self.push_screen(MainScreen(self.client))


async def test_context_menu_uses_context_menu_layer() -> None:
    """ContextMenu must be in the 'context-menu' layer so it renders above panels.

    Regression test for Bug 1: without 'layers: context-menu' declared in the
    screen's CSS, the ContextMenu falls back to the default layer and is rendered
    behind other widgets (last-mounted widget has lowest rendering priority).
    """
    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.rooms_data = [RoomSummary(room_id="!r:s", display_name="R")]
    app = MainScreenHostApp(fake)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MainScreen)

        # Show a context menu via the internal API.
        items: list[MenuEntry] = [MenuItem("Test", lambda: None)]
        screen._show_context_menu(items, 5, 5)  # pyright: ignore[reportPrivateUsage]
        await pilot.pause()

        menus = list(screen.query(ContextMenu))
        assert menus, "ContextMenu not mounted"
        menu = menus[0]

        # The ContextMenu widget's CSS 'layer' property must be 'context-menu'.
        assert menu.styles.layer == "context-menu", (
            f"ContextMenu layer is {menu.styles.layer!r}, expected 'context-menu'. "
            "app.tcss must set 'layer: context-menu' on ContextMenu."
        )

        # The screen must declare 'context-menu' in its layers so the layer
        # assignment takes effect in the compositor.
        assert "context-menu" in screen.layers, (
            f"'context-menu' not in screen.layers: {screen.layers}. "
            "app.tcss must set 'layers: context-menu' on MainScreen."
        )


# ---------------------------------------------------------------------------
# Test 9: MenuSeparator must not expand to terminal width (Bug 2)
# ---------------------------------------------------------------------------


async def test_context_menu_with_separator_fits_content() -> None:
    """ContextMenu containing a MenuSeparator must not stretch to terminal width.

    Bug 2: Rule sets expand=True and its default CSS uses width: 1fr, which
    inside a width: auto ContextMenu forces the menu to full terminal width.
    The fix constrains the separator to width: auto so it stays within the menu.

    Uses CSS_PATH=app.tcss so ContextMenu { width: auto } and related rules
    apply — the same environment the real app uses.
    """
    from telemente.tui.widgets.context_menu import MenuSeparator

    class SeparatorHostApp(App[None]):
        CSS_PATH = _APP_TCSS

        def __init__(self) -> None:
            super().__init__()

        def compose(self) -> ComposeResult:
            yield Static("background")

        def on_mount(self) -> None:
            sep_items: list[MenuEntry] = [
                MenuItem("Favourite", lambda: None),
                MenuSeparator(),
                MenuItem("Leave room", lambda: None),
            ]
            menu = ContextMenu(sep_items, 0, 0)
            self.screen.mount(menu)

    app = SeparatorHostApp()

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        menu = app.query_one(ContextMenu)
        assert menu.size.width <= 30, (
            f"ContextMenu width is {menu.size.width}, expected <= 30. "
            "MenuSeparator is expanding the menu to terminal width."
        )
