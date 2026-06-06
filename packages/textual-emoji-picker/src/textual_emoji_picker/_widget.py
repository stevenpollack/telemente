"""EmojiPicker widget — searchable, categorised emoji grid for Textual apps."""

from __future__ import annotations

import contextlib
import importlib.resources
import json
import re
from collections.abc import Sequence
from typing import ClassVar

from emoji import EMOJI_DATA, STATUS
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid, Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Button, Input, Label

# ---------------------------------------------------------------------------
# Module-level data — loaded once at import time
# ---------------------------------------------------------------------------

# Fitzpatrick modifier codepoints U+1F3FB-U+1F3FF (light to dark).
_FITZPATRICK_MODIFIERS: tuple[str, ...] = (
    "\U0001f3fb",  # light
    "\U0001f3fc",  # medium-light
    "\U0001f3fd",  # medium
    "\U0001f3fe",  # medium-dark
    "\U0001f3ff",  # dark
)

_FQ_STATUS: int = STATUS["fully_qualified"]

# Type alias for the per-emoji metadata dict from the emoji library.
# Keys include "en" (CLDR name), "status" (int), "E" (version float).
_EmojiMeta = dict[str, str | int | float | list[str] | bool]

# All fully-qualified emoji from the emoji library, keyed by codepoint string.
_FULLY_QUALIFIED: dict[str, _EmojiMeta] = {
    cp: meta for cp, meta in EMOJI_DATA.items() if meta["status"] == _FQ_STATUS
}

# Load categories from bundled JSON — maps codepoint → {group, subgroup, order}.
_pkg_ref = importlib.resources.files("textual_emoji_picker") / "_data" / "categories.json"
with importlib.resources.as_file(_pkg_ref) as _cat_path, open(_cat_path, encoding="utf-8") as _f:
    _CATEGORIES: dict[str, dict[str, str | int]] = json.load(_f)


def _en_name(meta: _EmojiMeta) -> str:
    """Extract the English CLDR name string from emoji metadata."""
    return str(meta.get("en") or "")


def _emoji_version(meta: _EmojiMeta) -> float:
    """Extract the emoji version as a float from emoji metadata."""
    raw = meta.get("E")
    if raw is None:
        return 0.0
    return float(raw)  # type: ignore[arg-type]  # E is always numeric in practice


# Skin-tone-capable bases: any base whose en name has variants with _skin_tone suffixes.
_SKIN_TONE_CAPABLE: frozenset[str] = frozenset(
    base_cp
    for base_cp, base_meta in _FULLY_QUALIFIED.items()
    if any(
        "_skin_tone" in _en_name(v_meta)
        and re.sub(
            r":(.+?)(?:_(?:light|medium_light|medium|medium_dark|dark)_skin_tone)+:",
            r":\1:",
            _en_name(v_meta),
        )
        == _en_name(base_meta)
        for v_meta in _FULLY_QUALIFIED.values()
    )
)


def _apply_version_filter(
    data: dict[str, _EmojiMeta], max_version: float | None
) -> dict[str, _EmojiMeta]:
    if max_version is None:
        return data
    return {cp: m for cp, m in data.items() if _emoji_version(m) <= max_version}


def _normalise_group(group: str) -> str:
    """Normalise a group name to a slug for comparison with categories kwarg."""
    return group.lower().replace(" ", "-").replace("&", "").replace("--", "-").strip("-")


# ---------------------------------------------------------------------------
# EmojiPicker widget
# ---------------------------------------------------------------------------


class EmojiPicker(Widget):
    """Embeddable searchable emoji grid.

    Posts EmojiPicker.EmojiSelected when the user picks an emoji.
    Posts EmojiPicker.Cancelled when Escape is pressed.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    DEFAULT_CSS = """
    EmojiPicker {
        width: 52;
        height: auto;
        max-height: 32;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }
    EmojiPicker #emoji-search {
        width: 1fr;
        margin-bottom: 1;
    }
    EmojiPicker #skin-tone-row {
        height: 2;
        width: 1fr;
        margin-bottom: 1;
    }
    EmojiPicker #skin-tone-row Button {
        width: 4;
        min-width: 4;
        height: 2;
        padding: 0;
        border: none;
    }
    EmojiPicker #skin-tone-row Button:hover {
        border: none;
        background: $accent 30%;
    }
    EmojiPicker #skin-tone-row Button.-selected-swatch {
        border: tall $accent;
    }
    EmojiPicker #emoji-grid {
        width: 1fr;
        height: auto;
        max-height: 20;
        grid-size: 8;
        grid-rows: 2;
        overflow-y: auto;
    }
    EmojiPicker #emoji-grid Button {
        width: 3;
        min-width: 3;
        height: 2;
        padding: 0;
        border: none;
    }
    EmojiPicker #emoji-grid Button:hover {
        border: none;
        background: $accent 30%;
    }
    EmojiPicker #emoji-hint {
        width: 1fr;
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    # ---------------------------------------------------------------------------
    # Messages
    # ---------------------------------------------------------------------------

    class EmojiSelected(Message):
        """Posted when the user selects an emoji."""

        def __init__(self, emoji: str) -> None:
            super().__init__()
            self.emoji = emoji

    class Cancelled(Message):
        """Posted when Escape is pressed inside the picker."""

    # ---------------------------------------------------------------------------
    # Reactive state
    # ---------------------------------------------------------------------------

    _search_query: reactive[str] = reactive("", init=False)

    # ---------------------------------------------------------------------------
    # Construction
    # ---------------------------------------------------------------------------

    def __init__(
        self,
        *,
        categories: Sequence[str] | None = None,
        default_skin_tone: int = 1,
        max_emoji_version: float | None = 14.0,
        show_recent: bool = False,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._categories = categories
        self._default_skin_tone = default_skin_tone
        self._max_emoji_version = max_emoji_version
        self._show_recent = show_recent  # reserved for v0.2
        # Currently selected Fitzpatrick modifier; empty means none.
        self._skin_modifier: str = (
            _FITZPATRICK_MODIFIERS[default_skin_tone - 2] if default_skin_tone >= 2 else ""
        )
        # Build the display list once at construction time.
        self._emoji_list: list[tuple[str, str]] = self._build_emoji_list()

    # ---------------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------------

    def _build_emoji_list(self) -> list[tuple[str, str]]:
        """Return the sorted list of (codepoint, CLDR_name) for display."""
        pool = _apply_version_filter(_FULLY_QUALIFIED, self._max_emoji_version)

        # Filter to requested categories (slugified group names).
        if self._categories is not None:
            wanted = {c.lower() for c in self._categories}
            pool = {
                cp: meta
                for cp, meta in pool.items()
                if cp in _CATEGORIES and _normalise_group(str(_CATEGORIES[cp]["group"])) in wanted
            }

        # Sort by (order in categories.json, codepoint) for stable ordering.
        def _sort_key(cp: str) -> tuple[int, str]:
            cat = _CATEGORIES.get(cp)
            return (int(cat["order"]) if cat else 999999, cp)

        result: list[tuple[str, str]] = []
        for cp in sorted(pool, key=_sort_key):
            meta = pool[cp]
            en_raw = _en_name(meta)
            # Strip surrounding colons and convert underscores to spaces.
            name = en_raw.strip(":").replace("_", " ")
            result.append((cp, name))

        return result

    def _filtered_list(self, query: str) -> list[tuple[str, str]]:
        if not query:
            return self._emoji_list
        q = query.lower()
        return [(cp, nm) for cp, nm in self._emoji_list if q in nm.lower()]

    def _populate_grid(self, emoji_list: list[tuple[str, str]]) -> None:
        """Update the emoji grid with a diff so existing buttons are reused."""
        grid = self.query_one("#emoji-grid", Grid)
        existing = list(grid.query(Button))

        for i, (cp, _name) in enumerate(emoji_list):
            if i < len(existing):
                if str(existing[i].label) != cp:
                    existing[i].label = cp
            else:
                grid.mount(Button(cp))

        for btn in existing[len(emoji_list) :]:
            btn.remove()

    # ---------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Input(id="emoji-search", placeholder="Search emoji…")
            with Horizontal(id="skin-tone-row"):
                # Neutral swatch — white flag glyph as a "no modifier" indicator.
                yield Button("\U0001f3f3", id="swatch-none")
                for mod in _FITZPATRICK_MODIFIERS:
                    yield Button(mod, id=f"swatch-{ord(mod):x}")
            yield Grid(id="emoji-grid")
            yield Label("Press Enter or click to select", id="emoji-hint")

    def on_mount(self) -> None:
        self._populate_grid(self._emoji_list)
        # Apply default skin tone selection visually.
        if self._skin_modifier:
            btn_id = f"swatch-{ord(self._skin_modifier):x}"
            with contextlib.suppress(Exception):
                self.query_one(f"#{btn_id}", Button).add_class("-selected-swatch")
        self.query_one("#emoji-search", Input).focus()

    # ---------------------------------------------------------------------------
    # Event handlers
    # ---------------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "emoji-search":
            return
        query = event.value.strip().lower()
        self._populate_grid(self._filtered_list(query))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""

        if btn_id == "swatch-none" or btn_id.startswith("swatch-"):
            # Clear selection on all swatches.
            for swatch in self.query_one("#skin-tone-row", Horizontal).query(Button):
                swatch.remove_class("-selected-swatch")
            if btn_id == "swatch-none":
                self._skin_modifier = ""
            else:
                self._skin_modifier = str(event.button.label)
                event.button.add_class("-selected-swatch")
            event.stop()
            return

        # Emoji grid button — apply modifier if capable, then post message.
        base = str(event.button.label)
        if self._skin_modifier and base in _SKIN_TONE_CAPABLE:
            # Strip a trailing U+FE0F variation selector before appending the
            # modifier; appending after FE0F produces an invalid sequence.
            clean_base = base.rstrip("️")
            self.post_message(EmojiPicker.EmojiSelected(clean_base + self._skin_modifier))
        else:
            self.post_message(EmojiPicker.EmojiSelected(base))

    def action_cancel(self) -> None:
        self.post_message(EmojiPicker.Cancelled())
