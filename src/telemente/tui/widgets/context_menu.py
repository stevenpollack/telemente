"""Floating context menu widget (plan 0020).

A single-column floating menu that mounts into the active Screen, positions
itself at mouse coordinates, and dismisses on Escape, Enter, or outside click.

Usage:
    menu = ContextMenu(items, screen_x, screen_y)
    await self.app.screen.mount(menu)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from textual.app import ComposeResult
from textual.events import Click, Key
from textual.geometry import Offset
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Static

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Menu entry types
# ---------------------------------------------------------------------------


@dataclass
class MenuItem:
    """A clickable menu entry with an optional enabled state."""

    label: str
    action: Callable[[], object]
    enabled: bool = field(default=True)


@dataclass(frozen=True, slots=True)
class MenuSeparator:
    """A horizontal divider between menu items."""


MenuEntry = MenuItem | MenuSeparator


# ---------------------------------------------------------------------------
# ContextMenu widget
# ---------------------------------------------------------------------------


class ContextMenu(Widget, can_focus=True):
    """Floating single-column menu.

    Mount into Screen, position at mouse coords. Dismisses on Escape, on
    clicking an item, or when the Screen receives an outside click.
    """

    class Dismissed(TextualMessage):
        """Posted just before the menu removes itself from the DOM."""

    def __init__(
        self,
        items: list[MenuEntry],
        screen_x: int,
        screen_y: int,
    ) -> None:
        super().__init__()
        self._items = items
        self._screen_x = screen_x
        self._screen_y = screen_y
        # Index of the currently keyboard-focused item (enabled items only).
        self._enabled_indices: list[int] = [
            i for i, item in enumerate(items) if isinstance(item, MenuItem) and item.enabled
        ]
        self._focus_idx: int = 0  # index into _enabled_indices

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        self.absolute_offset = Offset(self._screen_x, self._screen_y)
        self.focus()

    def compose(self) -> ComposeResult:
        for i, entry in enumerate(self._items):
            if isinstance(entry, MenuSeparator):
                # Rule sets expand=True which overrides CSS width: auto and
                # stretches the menu to full terminal width. Use a Static
                # divider instead so the separator stays within the menu.
                yield Static("─" * 20, classes="menu-separator")
            else:
                classes = "menu-item"
                if not entry.enabled:
                    classes += " -disabled"
                # Tag the first focused item.
                if self._enabled_indices and i == self._enabled_indices[self._focus_idx]:
                    classes += " -focused"
                yield Static(
                    entry.label,
                    classes=classes,
                    id=f"menu-item-{i}",
                )

    # ------------------------------------------------------------------
    # Keyboard navigation
    # ------------------------------------------------------------------

    def on_key(self, event: Key) -> None:
        if event.key == "escape":
            event.stop()
            self._dismiss()
            return
        if event.key == "enter":
            event.stop()
            self._activate_focused()
            return
        if event.key == "down":
            event.stop()
            self._move_focus(1)
            return
        if event.key == "up":
            event.stop()
            self._move_focus(-1)
            return

    def _move_focus(self, delta: int) -> None:
        if not self._enabled_indices:
            return
        new_idx = (self._focus_idx + delta) % len(self._enabled_indices)
        old_item_id = f"menu-item-{self._enabled_indices[self._focus_idx]}"
        new_item_id = f"menu-item-{self._enabled_indices[new_idx]}"
        self._focus_idx = new_idx
        try:
            self.query_one(f"#{old_item_id}", Static).remove_class("-focused")
            self.query_one(f"#{new_item_id}", Static).add_class("-focused")
        except Exception:
            pass

    def _activate_focused(self) -> None:
        if not self._enabled_indices:
            return
        item_idx = self._enabled_indices[self._focus_idx]
        entry = self._items[item_idx]
        if isinstance(entry, MenuItem) and entry.enabled:
            # Bug 1 fix: action fires BEFORE dismiss so the callback always runs
            # even if dismiss scheduling interrupts the call stack.
            entry.action()
            self._dismiss()

    # ------------------------------------------------------------------
    # Mouse handling
    # ------------------------------------------------------------------

    def on_click(self, event: Click) -> None:
        # Stop propagation so the Screen's outside-click handler doesn't
        # also fire when clicking inside the menu.
        event.stop()
        # Find which item was clicked by checking widget under cursor.
        widget = event.widget
        if widget is self:
            return
        # Walk up to find a Static with menu-item class.
        target: Widget | None = widget
        while target is not None:
            if isinstance(target, Static) and "menu-item" in target.classes:
                widget_id = target.id or ""
                if widget_id.startswith("menu-item-"):
                    try:
                        idx = int(widget_id[len("menu-item-") :])
                        entry = self._items[idx]
                        if isinstance(entry, MenuItem) and entry.enabled:
                            # Bug 1 fix: action fires BEFORE dismiss.
                            entry.action()
                            self._dismiss()
                    except (ValueError, IndexError):
                        pass
                return
            target = target.parent  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Dismiss
    # ------------------------------------------------------------------

    def _dismiss(self) -> None:
        self.post_message(self.Dismissed())
        self.remove()
