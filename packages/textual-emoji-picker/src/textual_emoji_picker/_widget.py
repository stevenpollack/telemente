"""EmojiPicker widget — searchable, categorised emoji grid for Textual apps."""

from __future__ import annotations

import importlib.resources
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from emoji import EMOJI_DATA, STATUS
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid, Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Select, Tab, Tabs

logger = logging.getLogger(__name__)

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

# Ordered list of display groups (Component contains only Fitzpatrick/hair
# modifier bases; skip it since modifiers are shown in the swatch row).
_GROUP_ORDER: list[str] = [
    "Smileys & Emotion",
    "People & Body",
    "Animals & Nature",
    "Food & Drink",
    "Travel & Places",
    "Activities",
    "Objects",
    "Symbols",
    "Flags",
]
# "Component" is intentionally omitted — those are skin/hair modifier bases,
# not standalone emoji for selection.


def _en_name(meta: _EmojiMeta) -> str:
    """Extract the English CLDR name string from emoji metadata."""
    return str(meta.get("en") or "")


def _emoji_version(meta: _EmojiMeta) -> float:
    """Extract the emoji version as a float from emoji metadata."""
    raw = meta.get("E")
    if raw is None:
        return 0.0
    return float(raw)  # type: ignore[arg-type]  # E is always numeric in practice


def _is_base_emoji(cp: str) -> bool:
    """Return True iff the codepoint string is a base (un-toned) emoji.

    A toned variant contains one of the five Fitzpatrick modifier codepoints
    (U+1F3FB-U+1F3FF) in its sequence. We check the raw string because the
    modifier is always present as a literal character in fully-qualified forms.
    """
    return not any(mod in cp for mod in _FITZPATRICK_MODIFIERS)


# Skin-tone-capable bases: emoji that have Fitzpatrick-toned variants in the
# fully-qualified set.  Computed in O(N) by collecting bases of toned variants.
# An emoji is a toned variant iff its codepoint string contains a Fitzpatrick
# modifier.  The base is the same string with all Fitzpatrick modifiers removed.
def _compute_skin_tone_capable() -> frozenset[str]:
    bases: set[str] = set()
    for cp in _FULLY_QUALIFIED:
        if not _is_base_emoji(cp):
            # Strip all Fitzpatrick modifiers to recover the base codepoint.
            base = cp
            for mod in _FITZPATRICK_MODIFIERS:
                base = base.replace(mod, "")
            if base in _FULLY_QUALIFIED:
                bases.add(base)
    return frozenset(bases)


_SKIN_TONE_CAPABLE: frozenset[str] = _compute_skin_tone_capable()

# Skin tone selector options: (label, modifier_string).
# The label is a hand emoji rendered in that tone; value is the modifier to append.
_SKIN_TONE_OPTIONS: list[tuple[str, str]] = [
    ("✋", ""),  # neutral (raised hand, no modifier)
    ("✋\U0001f3fb", "\U0001f3fb"),
    ("✋\U0001f3fc", "\U0001f3fc"),
    ("✋\U0001f3fd", "\U0001f3fd"),
    ("✋\U0001f3fe", "\U0001f3fe"),
    ("✋\U0001f3ff", "\U0001f3ff"),
]


def _apply_skin_tone(cp: str, modifier: str) -> str:
    """Apply a Fitzpatrick modifier to a base emoji if it's skin-tone-capable."""
    if not modifier or cp not in _SKIN_TONE_CAPABLE:
        return cp
    return cp.rstrip("️") + modifier


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
        Binding("ctrl+1", "skin_tone(0)", "Neutral", show=False),
        Binding("ctrl+2", "skin_tone(1)", "Light", show=False),
        Binding("ctrl+3", "skin_tone(2)", "Medium Light", show=False),
        Binding("ctrl+4", "skin_tone(3)", "Medium", show=False),
        Binding("ctrl+5", "skin_tone(4)", "Medium Dark", show=False),
        Binding("ctrl+6", "skin_tone(5)", "Dark", show=False),
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
    EmojiPicker #skin-tone-bar {
        height: 1;
        width: 1fr;
        align: right middle;
        margin-top: 1;
    }
    EmojiPicker #skin-tone-bar Label {
        width: auto;
        height: 1;
        color: $text-muted;
        margin-right: 1;
    }
    EmojiPicker #skin-tone-bar Select {
        width: 5;
        height: 1;
    }
    EmojiPicker #skin-tone-bar SelectOverlay {
        width: 5;
    }
    EmojiPicker #category-tabs {
        width: 1fr;
        margin-bottom: 1;
    }
    EmojiPicker #emoji-grid {
        width: 1fr;
        height: auto;
        max-height: 18;
        grid-size: 8;
        grid-columns: 1fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr;
        grid-rows: 2;
        overflow-y: auto;
    }
    EmojiPicker #emoji-grid Button {
        width: 1fr;
        min-width: 3;
        height: 2;
        padding: 0;
        border: none;
        content-align: center middle;
        text-align: center;
    }
    EmojiPicker #emoji-grid Button:hover {
        border: none;
        background: $accent 30%;
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
        persist_path: str | Path | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._categories = categories
        self._default_skin_tone = default_skin_tone
        self._max_emoji_version = max_emoji_version
        self._show_recent = show_recent  # reserved for v0.2
        self._persist_path: Path | None = Path(persist_path) if persist_path else None
        # Currently selected Fitzpatrick modifier; empty means none.
        self._skin_modifier: str = self._load_skin_tone(default_skin_tone)
        # Build the display list once at construction time.
        # Only base emoji (no Fitzpatrick-toned variants) appear in the grid.
        self._emoji_list: list[tuple[str, str]] = self._build_emoji_list()
        # Active category tab; empty string means "show all".
        self._active_group: str = ""
        # Debounce timer — replaced on each keystroke.
        self._search_timer: Timer | None = None

    # ---------------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------------

    def _load_skin_tone(self, default_skin_tone: int) -> str:
        """Load persisted skin tone or fall back to default_skin_tone."""
        if self._persist_path and self._persist_path.exists():
            try:
                data = json.loads(self._persist_path.read_text(encoding="utf-8"))
                modifier = data.get("skin_tone", "")
                if modifier == "" or modifier in _FITZPATRICK_MODIFIERS:
                    return str(modifier)
            except (json.JSONDecodeError, OSError):
                pass
        return _FITZPATRICK_MODIFIERS[default_skin_tone - 2] if default_skin_tone >= 2 else ""

    def _save_skin_tone(self) -> None:
        """Persist current skin tone selection."""
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(
                json.dumps({"skin_tone": self._skin_modifier}), encoding="utf-8"
            )
        except OSError:
            logger.debug("EmojiPicker: failed to persist skin tone to %s", self._persist_path)

    def _build_emoji_list(self) -> list[tuple[str, str]]:
        """Return sorted (codepoint, CLDR_name) for base emoji only."""
        pool = _apply_version_filter(_FULLY_QUALIFIED, self._max_emoji_version)

        # Remove toned variants — only base emoji appear in the grid.
        pool = {cp: meta for cp, meta in pool.items() if _is_base_emoji(cp)}

        # Exclude the "Component" group (Fitzpatrick + hair modifier bases).
        pool = {
            cp: meta
            for cp, meta in pool.items()
            if cp not in _CATEGORIES or _CATEGORIES[cp].get("group") != "Component"
        }

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

    def _emoji_for_display(self, query: str, group: str) -> list[tuple[str, str]]:
        """Return the emoji list to display given current search query and group."""
        if query:
            # Search spans all categories; ignore group filter.
            q = query.lower()
            return [(cp, nm) for cp, nm in self._emoji_list if q in nm.lower()]
        if not group:
            return self._emoji_list
        return [
            (cp, nm)
            for cp, nm in self._emoji_list
            if cp in _CATEGORIES and _CATEGORIES[cp].get("group") == group
        ]

    def _filtered_list(self, query: str) -> list[tuple[str, str]]:
        """Kept for backwards-compat with direct callers in tests."""
        return self._emoji_for_display(query, self._active_group)

    def _populate_grid(self, emoji_list: list[tuple[str, str]]) -> None:
        """Update the emoji grid with a diff so existing buttons are reused."""
        grid = self.query_one("#emoji-grid", Grid)
        existing = list(grid.query(Button))

        for i, (cp, _name) in enumerate(emoji_list):
            display = _apply_skin_tone(cp, self._skin_modifier)
            if i < len(existing) and str(existing[i].label) != display:
                existing[i].label = display

        # Batch-mount new buttons if the list grew.
        if len(emoji_list) > len(existing):
            new_buttons = [
                Button(_apply_skin_tone(cp, self._skin_modifier))
                for cp, _name in emoji_list[len(existing) :]
            ]
            grid.mount_all(new_buttons)

        # Remove excess buttons if the list shrank.
        for btn in existing[len(emoji_list) :]:
            btn.remove()

    def _refresh_grid(self) -> None:
        """Re-populate the grid with current filters and skin tone."""
        query = self.query_one("#emoji-search", Input).value.strip().lower()
        self._populate_grid(self._emoji_for_display(query, self._active_group))

    def _available_groups(self) -> list[str]:
        """Return the ordered list of groups present in _emoji_list."""
        groups_in_list: set[str] = set()
        for cp, _ in self._emoji_list:
            cat = _CATEGORIES.get(cp)
            if cat:
                groups_in_list.add(str(cat["group"]))
        return [g for g in _GROUP_ORDER if g in groups_in_list]

    def _make_category_tabs(self) -> list[Tab]:
        """Build the initial Tab objects for compose time.

        Category tabs are listed first (so the first category is auto-activated
        on mount, loading only ~170 emoji instead of 1800+). "All" is appended
        at the end for users who want to browse the full set.
        """
        tabs: list[Tab] = []
        for group in self._available_groups():
            slug = _normalise_group(group)
            tabs.append(Tab(group, id=f"tab-{slug}"))
        # "All" tab is last; first category tab gets auto-activated on mount.
        tabs.append(Tab("All", id="tab-all"))
        return tabs

    # ---------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        # Pre-compute the initial category's emoji so we can yield buttons in
        # compose() — this avoids a post-mount mount_all and renders instantly.
        groups = self._available_groups()
        initial_group = groups[0] if groups else ""
        initial_emoji = self._emoji_for_display("", initial_group)

        with Vertical():
            yield Input(id="emoji-search", placeholder="Search emoji…")
            # Tabs built at compose time with static Tab children — avoids the
            # async add_tab() path and ensures the first tab is activated on
            # mount via Tabs' own on_mount, which fires TabActivated.
            yield Tabs(*self._make_category_tabs(), id="category-tabs")
            with Grid(id="emoji-grid"):
                for cp, _name in initial_emoji:
                    yield Button(_apply_skin_tone(cp, self._skin_modifier))
            with Horizontal(id="skin-tone-bar"):
                yield Label("Skin tone:", id="skin-tone-label")
                yield Select(
                    _SKIN_TONE_OPTIONS,
                    value=self._skin_modifier,
                    allow_blank=False,
                    id="skin-tone-select",
                    compact=True,
                )

    def on_mount(self) -> None:
        # Set the initial active group to match what compose() pre-populated.
        groups = self._available_groups()
        self._active_group = groups[0] if groups else ""
        self.query_one("#emoji-search", Input).focus()
        logger.debug("EmojiPicker mounted (%d emoji loaded)", len(self._emoji_list))

    # ---------------------------------------------------------------------------
    # Event handlers
    # ---------------------------------------------------------------------------

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tabs.id != "category-tabs":
            return
        tab = event.tab
        if tab is None:
            return
        tab_id = tab.id or ""
        if tab_id == "tab-all":
            self._active_group = ""
        else:
            # Recover group name from slug by matching against _GROUP_ORDER.
            slug = tab_id.removeprefix("tab-")
            self._active_group = next(
                (g for g in _GROUP_ORDER if _normalise_group(g) == slug),
                "",
            )
        query = self.query_one("#emoji-search", Input).value.strip().lower()
        self._populate_grid(self._emoji_for_display(query, self._active_group))
        event.stop()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "emoji-search":
            return
        # Cancel any pending debounce timer.
        if self._search_timer is not None:
            self._search_timer.stop()
        query = event.value.strip().lower()
        # Debounce: wait 150 ms before rebuilding the grid.
        self._search_timer = self.set_timer(
            0.15, lambda: self._populate_grid(self._emoji_for_display(query, self._active_group))
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "skin-tone-select":
            return
        self._skin_modifier = str(event.value) if event.value != Select.BLANK else ""
        self._save_skin_tone()
        self._refresh_grid()
        event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        # Emoji grid button — the displayed label already has the tone applied,
        # so post it directly.
        emoji = str(event.button.label)
        logger.debug("EmojiPicker: selected %r", emoji)
        self.post_message(EmojiPicker.EmojiSelected(emoji))

    def action_skin_tone(self, index: int) -> None:
        """Switch skin tone via keyboard shortcut (Ctrl+1 through Ctrl+6)."""
        if 0 <= index < len(_SKIN_TONE_OPTIONS):
            _, modifier = _SKIN_TONE_OPTIONS[index]
            self._skin_modifier = modifier
            self._save_skin_tone()
            self.query_one("#skin-tone-select", Select).value = modifier
            self._refresh_grid()

    def action_cancel(self) -> None:
        logger.debug("EmojiPicker: dismissed")
        self.post_message(EmojiPicker.Cancelled())
