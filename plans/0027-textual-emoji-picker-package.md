# Plan 0027 — textual-emoji-picker: extract and publish as a standalone package

## Goal

Extract `src/telemente/tui/screens/emoji_picker.py` into a standalone,
publishable Python package at `packages/textual-emoji-picker/`. The package
must be importable by any Textual application and installable from PyPI. It
is a TUI analogue of [emoji-mart](https://github.com/missive/emoji-mart):
a searchable, categorised emoji picker with skin-tone support.

This plan covers the full extraction, public API design, emoji data strategy,
packaging, telemente integration, and test specification. No implementation
is included here.

---

## Dependencies

- Plans 0001–0026 complete (this plan does not depend on any specific prior
  feature, but assumes the codebase is in the state left by plan 0025, which
  is when `EmojiPickerScreen` reached its current form).
- No new system dependencies. The chosen emoji data library (`emoji` 2.x) is
  a pure-Python package with zero runtime dependencies.

---

## Research summary

### 1. emoji-mart feature audit

emoji-mart (v5, the current major version as of 2025) is a web component for
browsers. It ships a React wrapper, a Preact/vanilla web component, and a
headless search API. Its feature set, mapped to TUI feasibility:

| emoji-mart feature | Current picker | Gap | TUI feasible? |
|---|---|---|---|
| Full Unicode 14 set (~3 658 fully-qualified emoji) | No — ~125 curated bases | Yes, large gap | Yes |
| Category navigation (Smileys, People, Animals…) | No | Yes | Yes — keyboard tabs |
| Search by CLDR name | Yes (baked names) | Partial — curated only | Yes |
| Skin-tone selector (Fitzpatrick modifiers) | Yes | None | Yes |
| Multiple skin tones per emoji (e.g. couple combos) | No | Yes | Out of scope (ZWJ sequences; no TUI value) |
| Recently used / frequently used category | No | Yes | Yes |
| Custom emoji (user-supplied images/strings) | No | Yes | Text/string only |
| i18n / locale-aware names | No (EN only) | Yes | Yes, via `emoji` library |
| Preview panel (hover shows name + large glyph) | No | Yes | Partial — status bar label |
| Per-line count / dynamic width | Fixed 8-column grid | Yes | Yes — reactive grid-size |
| Emoji version filter (exclude emoji the terminal can't render) | No | Yes | Yes — filter by E version |
| `exceptEmojis` exclusion list | No | Yes | Yes |
| Spritesheet / image rendering | N/A | N/A | Out of scope (terminal renders native glyphs) |
| Pixel-perfect hover positioning | N/A | N/A | Out of scope |
| GIF / SVG custom emoji | N/A | N/A | Out of scope |
| Country flags | No | Yes | Conditional (Windows terminal support varies) |

**TUI-specific features with no emoji-mart equivalent:**
- Keyboard-only navigation of the grid (arrow keys, Enter)
- Screen-size-aware column count
- `ModalScreen` vs. embeddable widget variants
- Escape-to-cancel contract

**Summary:** the main gaps for a comparable standalone widget are (1) the full
Unicode emoji set (3 658 vs. ~125), (2) category tabs for navigation, and
(3) a recently-used category. Everything else either maps cleanly to a TUI
equivalent or is out of scope for terminal rendering.

### 2. Python emoji data ecosystem

Four options were evaluated:

**Option A — `unicodedata` (stdlib)**
- Python 3.11/3.12 ships Unicode 15.0.
- `unicodedata.name(chr(cp))` returns UPPERCASE names (e.g. `'GRINNING FACE'`).
- Coverage: ~2 252 named `So`-category codepoints in emoji blocks.
- Does NOT cover ZWJ sequences (family emoji, profession+gender combos),
  multi-codepoint flags, or sequences like `👨‍💻`. These make up roughly
  half of the fully-qualified set.
- Does NOT provide: categories/subgroups, skin-tone capability flags,
  emoji version.
- Verdict: insufficient for a full picker. Useful as a zero-dependency
  fallback for name lookup of single-codepoint emoji only.

**Option B — `unicodedata2` (PyPI, v17.0.1)**
- A C-extension backport of `unicodedata` updated to Unicode 17.
- Same API as stdlib; same gap: no categories, no ZWJ sequences.
- Adds a C compilation step; no advantage over stdlib for our use case.
- Verdict: reject.

**Option C — Bundle the Unicode `emoji-test.txt` file**
- The Unicode Consortium publishes `emoji-test.txt` for each emoji version
  (e.g. `https://unicode.org/Public/emoji/15.0/emoji-test.txt`).
- Format: `1F600 ; fully-qualified # 😀 E1.0 grinning face`
- Provides: codepoint sequence, status (fully-qualified/minimally-qualified),
  emoji version, CLDR name, and group/subgroup structure.
- Unicode 15.0 has 3 658 fully-qualified entries across 10 groups and 101
  subgroups.
- File size: ~220 KB raw; parses to ~300 KB as JSON.
- License: Unicode Data Files licence (permissive, same as Python stdlib uses).
- Verdict: viable zero-dependency approach. Ship a pre-parsed JSON derived
  from `emoji-test.txt`. Regenerate on major emoji version bumps via a
  `scripts/generate_emoji_data.py` script checked into the package.

**Option D — `emoji` library (PyPI, v2.15.0)**
- `emoji` by carpedm20 (New BSD licence). Zero runtime dependencies.
- Ships a complete `EMOJI_DATA` dict: `{codepoint_str: {"en": ":name:", "status": int, "E": float}}`.
- 5 225 total entries (fully-qualified + variants). Filtered to
  `status == fully_qualified` (= `2`) gives the canonical set.
- Supports 22 locales (AR, BE, CS, DE, ES, FA, FI, FR, HI, IT, JA, KO, NL,
  PL, PT, RU, SA, TR, UK, VI, ZH) via separate JSON files shipped in the
  wheel.
- API: `emoji.EMOJI_DATA`, `emoji.is_emoji()`, `emoji.version()`.
- Does NOT directly expose categories/subgroups — these must be inferred from
  Unicode ordering or bundled separately.
- Wheel size: ~1.5 MB (includes all locale JSONs).
- Verdict: the richest pure-Python source. The locale data, skin-tone
  capability (derivable from codepoint), and `emoji.version()` filter make
  this the best single dependency.

**Recommendation: Option D (`emoji` 2.x)**

Use `emoji.EMOJI_DATA` filtered to `status == 2` (fully qualified) as the
authoritative source for codepoints and CLDR names. Derive categories from
the Unicode `emoji-test.txt` order (shipped as a pre-built lookup table in
`_data/categories.json`). This gives:

- Full Unicode 15/16 set with accurate names (EN default, other locales
  available via `emoji` locale keys).
- Skin-tone capability: any codepoint whose name ends with `_tone1` through
  `_tone5` is a variant; the base is the entry without a tone suffix.
- Emoji version via `emoji.version()` for rendering-safety filtering.
- Zero additional C extensions; pure Python.

The `categories.json` file (~50 KB) is generated once from `emoji-test.txt`
and shipped as package data. It maps each fully-qualified codepoint string to
`{group, subgroup, order}`. The generator script lives in
`packages/textual-emoji-picker/scripts/generate_categories.py`.

### 3. Packaging conventions for Textual widgets

Studied `textual-slider` (TomJGooding), `textual-autocomplete` (Textualize),
`textual-datepicker`, and `textual-colorpicker`. Findings:

**Build backend:** the ecosystem uses both hatchling and setuptools. Either
works; `hatchling` is consistent with telemente's own `pyproject.toml` and is
the most straightforward for `src/` layouts.

**Source layout:** all surveyed packages use a `src/<package_name>/` layout.
Consistent with telemente.

**`__init__.py` exports:** a flat top-level `__init__.py` re-exports the
primary public classes so callers can do `from textual_emoji_picker import
EmojiPicker, EmojiPickerScreen`. `textual-slider` follows this exactly:
`from textual_slider._slider import Slider; __all__ = ["Slider"]`.

**CSS:** widgets use inline `DEFAULT_CSS` class attribute (not a `.tcss` file).
This is the Textual convention for reusable widgets. Shipping a `.tcss` file
as package data is an anti-pattern for library code because it requires
consumers to mount it manually; `DEFAULT_CSS` is automatically registered.

**Textual version pin:** `textual-slider` pins `textual >= 7.4.0`.
`textual-autocomplete` pins `textual >= 2.0.0`. The safe strategy is to pin
to the minimum Textual version that introduced the APIs the widget uses (see
Done-when). `textual-emoji-picker` should pin `textual >= 1.0.0` (the version
telemente already requires).

**`py.typed` marker:** all well-maintained packages ship an empty `py.typed`
file to signal PEP 561 compliance. Include it.

**`Typing :: Typed` classifier:** mark the package as typed in PyPI classifiers.

### 4. Current widget audit — telemente-specific concerns

Reading `emoji_picker.py`:

**What must be removed or abstracted:**

- `ModalScreen[str]` as the base class is correct for the modal variant but is
  too opinionated for a general library. The library should offer two classes:
  - `EmojiPicker(Widget)` — embeddable, fires a `EmojiPicker.EmojiSelected`
    message when an emoji is chosen. No dismiss; no Escape handling baked in.
  - `EmojiPickerScreen(ModalScreen[str])` — thin wrapper that composes
    `EmojiPicker`, handles `EmojiSelected` → `self.dismiss(emoji)`, and maps
    Escape → `self.dismiss("")`. This is what telemente uses today.

- `REACTION_EMOJI` is a curated list of ~125 emoji from Element Web defaults.
  It is telemente-specific business logic. The library ships with the full
  Unicode set (filtered by a configurable `categories` param); telemente can
  pass its own `custom_set` or use the default.

- `SKIN_TONE_CAPABLE` frozenset is correct domain logic but should live in the
  widget, not in the calling app. It moves into `EmojiPicker._skin_tone_capable`.

- `_FITZPATRICK_MODIFIERS` is pure Unicode logic — keep it in the widget.

- The `dismiss("")` on Escape is `ModalScreen`-specific. The embeddable
  `EmojiPicker` should instead fire no message and leave Escape for the parent
  to handle, or expose an `on_emoji_picker_cancelled` message.

**The right return API:**

Web emoji-mart uses a callback: `onEmojiSelect`. The Textual idiom is a
posted `Message` subclass. The recommended design:

```python
class EmojiPicker(Widget):
    class EmojiSelected(Message):
        """Posted when the user picks an emoji."""
        emoji: str  # the selected codepoint string (possibly with Fitzpatrick modifier)

    class Cancelled(Message):
        """Posted when Escape is pressed inside the picker."""
```

Callers handle `on_emoji_picker_emoji_selected` and `on_emoji_picker_cancelled`.
The `ModalScreen` wrapper handles both by dismissing with the emoji or `""`.

**Widget vs. ModalScreen:**

Both variants should ship. The library is more useful as a plain `Widget`
because:
1. It can be embedded in a sidebar or inline panel.
2. It composes with `ModalScreen`, `TabbedContent`, or any other container.
3. It does not impose a modal interaction model on callers who don't want one.

The `ModalScreen` wrapper costs ~10 lines and gives back the familiar
"open and wait for result" pattern that telemente currently uses.

---

## Architecture

### Package directory tree

```
packages/textual-emoji-picker/
├── pyproject.toml
├── README.md
├── py.typed           (empty — PEP 561)
├── scripts/
│   └── generate_categories.py   # run once to regenerate _data/categories.json
└── src/
    └── textual_emoji_picker/
        ├── __init__.py           # re-exports: EmojiPicker, EmojiPickerScreen
        ├── _widget.py            # EmojiPicker(Widget)  — the core widget
        ├── _screen.py            # EmojiPickerScreen(ModalScreen[str])
        ├── _data/
        │   └── categories.json   # Unicode 15 group/subgroup/order map
        └── py.typed
```

`py.typed` appears both at the package root (for the sdist) and inside
`src/textual_emoji_picker/` (for the wheel).

### `pyproject.toml` structure

```toml
[project]
name = "textual-emoji-picker"
version = "0.1.0"
description = "A searchable, categorised emoji picker widget for Textual TUI apps"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
keywords = ["textual", "tui", "emoji", "picker", "widget"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Typing :: Typed",
]
dependencies = [
    "textual>=1.0.0",
    "emoji>=2.0.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/textual_emoji_picker"]

[tool.hatch.build.targets.wheel.shared-data]
"src/textual_emoji_picker/py.typed" = "textual_emoji_picker/py.typed"
```

The `_data/categories.json` file is inside the package directory and is
therefore included in the wheel automatically by hatchling. No explicit
`package-data` entry is needed.

### Public API

```python
# textual_emoji_picker/__init__.py
from textual_emoji_picker._widget import EmojiPicker
from textual_emoji_picker._screen import EmojiPickerScreen

__version__ = "0.1.0"
__all__ = ["EmojiPicker", "EmojiPickerScreen"]
```

**`EmojiPicker(Widget)`** — `_widget.py`

```python
class EmojiPicker(Widget):
    """Embeddable searchable emoji grid.

    Posts EmojiPicker.EmojiSelected when the user picks an emoji.
    Posts EmojiPicker.Cancelled when Escape is pressed.
    """

    class EmojiSelected(Message):
        def __init__(self, emoji: str) -> None: ...
        emoji: str

    class Cancelled(Message):
        pass

    def __init__(
        self,
        *,
        # Restrict to specific categories; None = all 10 Unicode groups.
        categories: Sequence[str] | None = None,
        # Default skin tone (1 = neutral/none, 2–6 = Fitzpatrick light–dark).
        default_skin_tone: int = 1,
        # Minimum emoji version to show (filter out newer emoji that
        # many terminals cannot render). None = no filter.
        max_emoji_version: float | None = 14.0,
        # Whether to show a "Recently used" category (persisted to memory
        # only in v0.1; disk persistence is v0.2+).
        show_recent: bool = False,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None: ...
```

**`EmojiPickerScreen(ModalScreen[str])`** — `_screen.py`

```python
class EmojiPickerScreen(ModalScreen[str]):
    """Modal wrapper around EmojiPicker.

    Dismisses with the selected emoji string.
    Dismisses with empty string on Escape or Cancelled.
    Accepts all EmojiPicker constructor kwargs (forwarded).
    """

    def __init__(
        self,
        *,
        categories: Sequence[str] | None = None,
        default_skin_tone: int = 1,
        max_emoji_version: float | None = 14.0,
        show_recent: bool = False,
    ) -> None: ...
```

**Caller patterns:**

```python
# Embedded widget
def compose(self) -> ComposeResult:
    yield EmojiPicker(categories=["people", "nature"])

def on_emoji_picker_emoji_selected(self, event: EmojiPicker.EmojiSelected) -> None:
    self.notify(f"Picked: {event.emoji}")

# Modal screen
async def action_open_emoji_picker(self) -> None:
    emoji = await self.app.push_screen_wait(EmojiPickerScreen())
    if emoji:
        await self._send_reaction(emoji)
```

### `_data/categories.json` format

Generated from `emoji-test.txt` by `scripts/generate_categories.py`. Structure:

```json
{
  "😀": {"group": "Smileys & Emotion", "subgroup": "face-smiling", "order": 0},
  "😃": {"group": "Smileys & Emotion", "subgroup": "face-smiling", "order": 1},
  ...
}
```

~3 600 entries (fully-qualified only). File size estimate: ~350 KB uncompressed
(acceptable; wheels are zipped). If size becomes a concern in v0.2, switch to
a compact array format indexed by order.

---

## Emoji data strategy (detailed)

**Source of truth:** `emoji.EMOJI_DATA` from the `emoji` library (v2.15.0+,
which covers Unicode 15.0 / Emoji 15.1).

At import time, `_widget.py` runs once:

```python
from emoji import EMOJI_DATA, STATUS
import json, importlib.resources

# 1. Filter to fully-qualified emoji only.
_FULLY_QUALIFIED = {
    cp: meta
    for cp, meta in EMOJI_DATA.items()
    if meta["status"] == STATUS["fully_qualified"]
}  # ~3 600 entries

# 2. Load category/order data from bundled JSON.
_pkg = importlib.resources.files("textual_emoji_picker") / "_data" / "categories.json"
with _pkg.open() as f:
    _CATEGORIES: dict[str, dict[str, str | int]] = json.load(f)
```

**Skin-tone capability:** derived programmatically. An emoji base supports
Fitzpatrick modifiers if the `emoji` library contains variants with the same
base name plus `_tone1` through `_tone5` suffixes. This avoids the hand-coded
whitelist in the current `SKIN_TONE_CAPABLE` frozenset:

```python
def _is_skin_tone_capable(base_cp: str, en_name: str) -> bool:
    """Return True if base_cp has Fitzpatrick variants in EMOJI_DATA."""
    base_name = en_name.rstrip(":").lstrip(":")  # e.g. "thumbs_up"
    return any(
        f":{base_name}_tone1:" in EMOJI_DATA
        for _ in [None]  # short-circuit after first hit
    )
```

In practice this is computed once at module load and cached in a frozenset.

**Emoji version filtering:** `emoji.version(cp)` returns the Emoji version
float. The default `max_emoji_version=14.0` excludes Emoji 15+ glyphs that
many terminals (particularly older versions of iTerm2, Windows Terminal before
22H2, and any terminal using a font without Noto Emoji 15+ coverage) render as
boxes. Callers can pass `max_emoji_version=None` to show all, or `15.0` to
include the Unicode 15 set.

**Why not bundle the data ourselves (Option C)?** The `emoji` library is
already a zero-dep pure-Python package and covers exactly the fields we need.
Bundling `emoji-test.txt` ourselves adds a maintenance burden (regenerate on
each Unicode bump) with no benefit. The `emoji` library's maintainer already
handles that. The locale data in `emoji` is a bonus for future i18n.

---

## Feature roadmap

### v0.1 — MVP parity with current telemente picker, plus full Unicode set

- `EmojiPicker(Widget)` with `EmojiSelected` and `Cancelled` messages.
- `EmojiPickerScreen(ModalScreen[str])` wrapper.
- Full Unicode emoji set via `emoji.EMOJI_DATA` (filtered to
  `max_emoji_version=14.0` by default).
- Search by CLDR name (English), case-insensitive substring match.
- Skin-tone selector row (Fitzpatrick modifiers, none/1–5).
- Skin-tone applied only to capable bases (auto-detected from `emoji` data).
- Keyboard navigation: Tab to move between search/skin-tone/grid sections;
  arrow keys within grid; Enter to select; Escape to cancel.
- `DEFAULT_CSS` inline (no external `.tcss` file).
- `py.typed`, fully typed, `mypy --strict` clean.
- Packaged with hatchling, installable via `pip install textual-emoji-picker`.

**Not in v0.1:** category tabs, recently-used, i18n, custom emoji, emoji
version selector UI, country flag toggle.

### v0.2 — Category navigation

- Category tabs above the grid (one per Unicode group: Smileys & Emotion,
  People & Body, …). Clicking a tab scrolls to that group.
- "Recently used" in-memory category (top of the list; last N picked, held in
  a module-level deque — no disk persistence yet).
- Column count reactive to widget width (replaces fixed `grid-size: 8`).

### v0.3 — Recents persistence + i18n

- Disk persistence for recently-used via `platformdirs` user-data path.
- Locale names via the `emoji` library's locale data (pass `locale="de"` etc.).
- `max_emoji_version` exposed as a UI toggle for power users.

### v0.4 — Custom emoji + advanced search

- `custom_emoji: list[tuple[str, str, str]]` — list of
  `(codepoint_or_text, display_name, category)` for app-provided custom entries.
- Multi-keyword search (AND of terms).
- Country flag category toggle (`show_flags: bool = True`).

---

## telemente integration

### Dependency declaration

In `/home/steven/repos/telemundo/pyproject.toml`, replace the implicit
dependency on the internal file with a local path dependency:

```toml
dependencies = [
    "textual>=1.0",
    "matrix-nio[e2e]>=0.25",
    "keyring>=25",
    "platformdirs>=4",
    "aiosqlite>=0.19",
    "textual-emoji-picker",   # added
]

[tool.uv.workspace]
members = ["packages/textual-emoji-picker"]

[tool.uv.sources]
textual-emoji-picker = { workspace = true }
```

The `uv` workspace mechanism installs the local package in editable mode
without needing `pip install -e`. `uv sync` handles it automatically.

### Changes to `src/telemente/tui/screens/emoji_picker.py`

The file becomes a thin re-export shim:

```python
# src/telemente/tui/screens/emoji_picker.py
"""Emoji picker — re-exports from the standalone textual-emoji-picker package.

telemente uses the curated REACTION_EMOJI set as the default for reactions;
pass categories=None to EmojiPickerScreen to get the full Unicode set.
"""
from __future__ import annotations

from textual_emoji_picker import EmojiPickerScreen as EmojiPickerScreen

# Keep REACTION_EMOJI and SKIN_TONE_CAPABLE exported for any internal code
# that referenced them directly; they will be removed in the next plan once
# callers are updated.
from telemente.tui.screens._emoji_data_legacy import (
    REACTION_EMOJI as REACTION_EMOJI,
    SKIN_TONE_CAPABLE as SKIN_TONE_CAPABLE,
)
```

`_emoji_data_legacy.py` is a temporary holding file for the curated lists,
deleted once all telemente callers have been updated to use the library's full
set (tracked as a follow-up sub-task in this plan's Done-when).

No other `telemente` files change for the integration step. The callers
(`MainScreen`, tests) import `EmojiPickerScreen` from
`telemente.tui.screens.emoji_picker` — the shim preserves that import path.

---

## Tier-1 tests — `packages/textual-emoji-picker/tests/test_data.py`

These tests run in the package's own test suite (not in `tests/` of telemente).
They exercise the emoji data loading and filtering logic with no Textual
dependency.

**`test_emoji_data_loads_without_error`**
- Import `textual_emoji_picker._widget`; assert no exception raised at module level.

**`test_fully_qualified_count_is_reasonable`**
- Access the internal `_FULLY_QUALIFIED` dict (exposed via a module-level
  constant for testing); assert `len(_FULLY_QUALIFIED) >= 3000`.

**`test_grinning_face_in_data`**
- Assert `"😀"` is in `_FULLY_QUALIFIED` and its `"en"` name contains `"grinning"`.

**`test_max_version_filter`**
- Build the filtered set with `max_emoji_version=1.0`; assert it contains
  fewer entries than the full set and that `"😀"` (E1.0) is included but
  `"🫠"` (E14.0 melting face) is excluded.

**`test_skin_tone_base_detected`**
- Assert `"👍"` (thumbs up) is detected as skin-tone-capable.

**`test_skin_tone_heart_not_capable`**
- Assert `"❤️"` is NOT skin-tone-capable.

**`test_categories_json_loads`**
- Load `_data/categories.json`; assert it is a non-empty dict; assert `"😀"`
  has `"group": "Smileys & Emotion"`.

**`test_categories_json_all_have_required_keys`**
- For every entry in `_CATEGORIES`, assert it has `group`, `subgroup`, `order`.

---

## Tier-2 tests — `packages/textual-emoji-picker/tests/test_widget.py`

These are Textual pilot tests for the widget behaviour.

**`test_emoji_picker_mounts`**
- Run a bare `App` that composes an `EmojiPicker`; `await pilot.pause()`;
  assert the widget is in the DOM.

**`test_search_input_present`**
- Assert `app.query_one("Input")` resolves without error after mount.

**`test_search_filters_grid`**
- Type `"grinning"` into the search input; `await pilot.pause()`;
  assert at least one Button in the grid has label `"😀"`.

**`test_search_no_match_clears_grid`**
- Type `"zzzzzzznomatch"` into search; assert no `Button` exists in the grid.

**`test_emoji_selected_message_posted`**
- Mount `EmojiPicker`; capture posted messages; press the first emoji button;
  assert one `EmojiPicker.EmojiSelected` was posted with a non-empty `.emoji`.

**`test_cancelled_message_on_escape`**
- Mount `EmojiPicker` in a host app that handles `Cancelled`; `pilot.press("escape")`;
  assert `Cancelled` was received.

**`test_skin_tone_applied`**
- Select a Fitzpatrick swatch; press a skin-tone-capable emoji button;
  assert `EmojiSelected.emoji` ends with one of the Fitzpatrick modifier codepoints.

**`test_skin_tone_not_applied_to_incapable`**
- Select a Fitzpatrick swatch; press a face emoji (e.g. `"😀"`);
  assert `EmojiSelected.emoji == "😀"` (no modifier appended).

**`test_modal_screen_dismisses_with_emoji`**
- Push `EmojiPickerScreen`; press the first emoji button;
  assert `push_screen_wait` returns the expected non-empty string.

**`test_modal_screen_dismisses_with_empty_on_escape`**
- Push `EmojiPickerScreen`; `pilot.press("escape")`;
  assert `push_screen_wait` returns `""`.

**`test_category_filter_kwarg`**
- Create `EmojiPicker(categories=["smileys-emotion"])`;
  assert only emoji from the smileys group appear in the grid
  (check that `"🍕"` is absent).

**`test_max_emoji_version_filter`**
- Create `EmojiPicker(max_emoji_version=1.0)`;
  assert `"😀"` is present and `"🫠"` (E14.0) is absent.

---

## Tier-2 tests — telemente integration — `tests/tui/test_emoji_picker_integration.py`

Confirms that the shim import still works and telemente's callers are
unaffected.

**`test_shim_import_works`**
- `from telemente.tui.screens.emoji_picker import EmojiPickerScreen`; assert
  it is the class from `textual_emoji_picker`.

**`test_reaction_flow_unchanged`**
- Use the existing `EmojiPickerHostApp` pattern from `tests/tui/test_emoji_picker.py`;
  push `EmojiPickerScreen()`; pick an emoji; assert the `on_emoji_result`
  handler receives the expected emoji string.
- This test is structurally identical to the existing skin-tone tests in
  `tests/tui/test_emoji_picker.py`; it lives in a new file to isolate
  integration concerns.

---

## Implementation steps

1. Create `packages/textual-emoji-picker/` directory tree as specified above.

2. Write `scripts/generate_categories.py` — fetches
   `https://unicode.org/Public/emoji/15.0/emoji-test.txt`, parses groups,
   subgroups, and CLDR names, and writes `src/textual_emoji_picker/_data/categories.json`.
   Run once; commit the generated file.

3. Write all Tier-1 data tests (failing).

4. Implement `_widget.py`:
   a. Module-level data loading (`_FULLY_QUALIFIED`, `_CATEGORIES`,
      `_SKIN_TONE_CAPABLE` frozenset).
   b. `compose()` — Input, skin-tone row, Grid (same structure as current
      `EmojiPickerScreen`).
   c. `_populate_grid()` — diff-based update (preserved from current impl).
   d. `on_input_changed()` — debounced 150 ms.
   e. `on_button_pressed()` — swatch vs. emoji, modifier application.
   f. `DEFAULT_CSS` — extracted from current `EmojiPickerScreen.DEFAULT_CSS`,
      adjusted to reference `EmojiPicker` instead of `EmojiPickerScreen`.
   g. Message classes: `EmojiSelected`, `Cancelled`.

5. Implement `_screen.py`:
   a. `EmojiPickerScreen(ModalScreen[str])` composing `EmojiPicker`.
   b. `on_emoji_picker_emoji_selected` → `self.dismiss(event.emoji)`.
   c. `action_dismiss_empty` → `self.dismiss("")`.
   d. Bindings: `Binding("escape", "dismiss_empty", "Cancel")`.

6. Write `__init__.py` and `py.typed`.

7. Write `pyproject.toml`.

8. Run Tier-1 data tests → make green.

9. Write Tier-2 widget tests (failing).

10. Iterate on widget implementation until Tier-2 tests green.

11. Write `pyproject.toml` for the package (with `[tool.uv.workspace]` etc.).

12. Update telemente's `pyproject.toml` with the workspace dependency.

13. Write the shim `src/telemente/tui/screens/emoji_picker.py`.

14. Write `_emoji_data_legacy.py` to hold `REACTION_EMOJI` and `SKIN_TONE_CAPABLE`.

15. Write `tests/tui/test_emoji_picker_integration.py`.

16. Run `uv run ruff check . && uv run ruff format . && uv run mypy && pyright src/ && uv run pytest`.

---

## Files to create / modify

| Path | Action | Notes |
|---|---|---|
| `packages/textual-emoji-picker/pyproject.toml` | Create | Package build config |
| `packages/textual-emoji-picker/README.md` | Create | User-facing docs (see below) |
| `packages/textual-emoji-picker/py.typed` | Create | Empty PEP 561 marker |
| `packages/textual-emoji-picker/scripts/generate_categories.py` | Create | One-time data generator |
| `packages/textual-emoji-picker/src/textual_emoji_picker/__init__.py` | Create | Re-exports |
| `packages/textual-emoji-picker/src/textual_emoji_picker/_widget.py` | Create | `EmojiPicker(Widget)` |
| `packages/textual-emoji-picker/src/textual_emoji_picker/_screen.py` | Create | `EmojiPickerScreen(ModalScreen[str])` |
| `packages/textual-emoji-picker/src/textual_emoji_picker/_data/categories.json` | Create | Generated Unicode data |
| `packages/textual-emoji-picker/src/textual_emoji_picker/py.typed` | Create | Empty PEP 561 marker (wheel) |
| `packages/textual-emoji-picker/tests/test_data.py` | Create | 8 Tier-1 data tests |
| `packages/textual-emoji-picker/tests/test_widget.py` | Create | 12 Tier-2 widget tests |
| `pyproject.toml` | Modify | Add workspace + `textual-emoji-picker` dependency |
| `src/telemente/tui/screens/emoji_picker.py` | Modify | Replace with shim re-export |
| `src/telemente/tui/screens/_emoji_data_legacy.py` | Create | Temporary REACTION_EMOJI / SKIN_TONE_CAPABLE holding file |
| `tests/tui/test_emoji_picker_integration.py` | Create | 2 integration tests |

---

## Done-when checklist

- [ ] `packages/textual-emoji-picker/` directory exists with the tree above.
- [ ] `scripts/generate_categories.py` runs cleanly and produces
  `_data/categories.json` with >= 3 000 entries.
- [ ] `_data/categories.json` is committed and included in the wheel.
- [ ] `emoji.EMOJI_DATA` filtered to `status == fully_qualified` is used as
  the emoji source; no hand-coded curated list in the library.
- [ ] Skin-tone capability is auto-detected from `emoji` library data; no
  hand-coded `SKIN_TONE_CAPABLE` whitelist in the library.
- [ ] `EmojiPicker(Widget)` posts `EmojiSelected` and `Cancelled` messages.
- [ ] `EmojiPickerScreen(ModalScreen[str])` dismisses with emoji or `""`.
- [ ] `EmojiPicker` accepts `categories`, `default_skin_tone`,
  `max_emoji_version`, `show_recent` constructor kwargs.
- [ ] `DEFAULT_CSS` is inline; no `.tcss` file shipped.
- [ ] `py.typed` present; `mypy --strict` passes on the package source.
- [ ] All 8 Tier-1 data tests green.
- [ ] All 12 Tier-2 widget tests green.
- [ ] `pyproject.toml` of the package has `textual>=1.0.0` and `emoji>=2.0.0`
  as runtime deps.
- [ ] hatchling builds a wheel: `uv build packages/textual-emoji-picker/`
  completes without error.
- [ ] telemente `pyproject.toml` declares the workspace dependency.
- [ ] `from telemente.tui.screens.emoji_picker import EmojiPickerScreen` still
  works; existing `tests/tui/test_emoji_picker.py` all pass unchanged.
- [ ] `tests/tui/test_emoji_picker_integration.py` green.
- [ ] `REACTION_EMOJI` and `SKIN_TONE_CAPABLE` still importable from
  `_emoji_data_legacy.py` (no caller breakage in telemente).
- [ ] Sub-task tracked: remove `_emoji_data_legacy.py` in plan 0028 or a
  follow-up once all callers use the library's full set.
- [ ] `uv run ruff check .` clean.
- [ ] `uv run ruff format .` clean.
- [ ] `uv run mypy` clean.
- [ ] `pyright src/` clean.
- [ ] `uv run pytest` all green (including new integration tests).
