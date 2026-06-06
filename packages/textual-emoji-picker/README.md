# textual-emoji-picker

A searchable, categorised emoji picker widget for [Textual](https://github.com/Textualize/textual) TUI applications.

Drop it into any Textual app as an embedded widget or push it as a modal screen. Covers the full Unicode 15 emoji set (3 600+ emoji) with skin-tone support and real-time search.

```
┌─────────────────────────────────────────┐
│ Search emoji…                           │
│ 🏳  🏻  🏼  🏽  🏾  🏿                   │
│                                         │
│ 😀 😃 😄 😁 😆 😅 🤣 😂                │
│ 🙂 🙃 🫠 😉 😊 😇 🥰 😍                │
│ 😘 😗 ☺  😚 😙 🥲 😋 😛                │
│ 😜 🤪 😝 🤑 🤗 🤭 🫢 🫣                │
│ 🤫 🤔 🫡 🤐 🤨 😐 😑 😶                │
│                                         │
│ Press Enter or click to select          │
└─────────────────────────────────────────┘
```

## Install

```sh
pip install textual-emoji-picker
```

Requires Python 3.11+ and Textual 1.0+.

## Usage

### As an embedded widget

```python
from textual.app import App, ComposeResult
from textual_emoji_picker import EmojiPicker

class MyApp(App[None]):
    def compose(self) -> ComposeResult:
        yield EmojiPicker()

    def on_emoji_picker_emoji_selected(self, event: EmojiPicker.EmojiSelected) -> None:
        self.notify(f"You picked: {event.emoji}")

    def on_emoji_picker_cancelled(self, event: EmojiPicker.Cancelled) -> None:
        self.notify("Cancelled")
```

### As a modal screen

```python
from textual.app import App
from textual_emoji_picker import EmojiPickerScreen

class MyApp(App[None]):
    async def action_pick_emoji(self) -> None:
        emoji = await self.push_screen_wait(EmojiPickerScreen())
        if emoji:
            self.notify(f"You picked: {emoji}")
```

### Constructor options

```python
EmojiPicker(
    # Restrict to specific Unicode groups. None = all groups.
    # Available: "smileys-emotion", "people-body", "animals-nature",
    #            "food-drink", "travel-places", "activities",
    #            "objects", "symbols", "flags"
    categories=["smileys-emotion", "people-body"],

    # Default Fitzpatrick skin tone. 1 = neutral (no modifier), 2–6 = light to dark.
    default_skin_tone=1,

    # Exclude emoji introduced after this version (avoids rendering boxes in
    # terminals with older font coverage). None = no filter.
    max_emoji_version=14.0,

    # Show a "recently used" category (in-memory only in v0.1).
    show_recent=False,
)
```

`EmojiPickerScreen` accepts the same keyword arguments and forwards them to the inner `EmojiPicker`.

## Keyboard navigation

| Key | Action |
|-----|--------|
| Type anything | Filter emoji by name |
| Arrow keys | Navigate the emoji grid |
| Enter | Select the focused emoji |
| Tab / Shift+Tab | Move between search bar, skin-tone row, and grid |
| Escape | Cancel (posts `Cancelled` / dismisses modal with `""`) |

## Skin tones

Select a skin tone swatch to apply a Fitzpatrick modifier to any compatible emoji (hands, gestures, people). Face emoji, hearts, and objects are not modified. Selecting the neutral swatch returns to the unmodified base emoji.

## Emoji data

Emoji are sourced from the [`emoji`](https://pypi.org/project/emoji/) library (Unicode 15 / Emoji 15.1, ~3 600 fully-qualified emoji). Category and display order follow the Unicode CLDR `emoji-test.txt` specification.

## Feature comparison with emoji-mart

[emoji-mart](https://github.com/missive/emoji-mart) is the reference web emoji picker. This package is a TUI analogue — the goals and constraints differ, but the feature roadmap is informed by emoji-mart.

| Feature | emoji-mart (web) | textual-emoji-picker (TUI) |
|---|---|---|
| Full Unicode emoji set | Yes (14/15) | Yes (15, ~3 600) |
| Search by name | Yes | Yes |
| Category navigation | Yes (tabs) | v0.2 |
| Skin-tone selector | Yes (per emoji + global) | Yes (global selector) |
| Recently used | Yes | v0.2 (in-memory), v0.3 (persisted) |
| Custom emoji | Yes (image/GIF/SVG) | v0.4 (text/string only) |
| i18n / locale names | Yes (22 locales) | v0.3 |
| Emoji version filter | UI toggle | `max_emoji_version` kwarg |
| Preview on hover | Yes | Status-bar label (v0.2) |
| Per-line count | `perLine` option | v0.2 (reactive to widget width) |
| Image spritesheets | Yes | Out of scope (terminal renders native glyphs) |
| GIF/pixel rendering | Yes | Out of scope |
| Hover positioning | Pixel-accurate | Out of scope |

## Changelog

### v0.1.0

- `EmojiPicker(Widget)` and `EmojiPickerScreen(ModalScreen[str])`.
- Full Unicode 15 emoji set (filtered to `max_emoji_version=14.0` by default).
- Real-time search by CLDR name (English).
- Skin-tone selector (Fitzpatrick modifiers, global).
- Keyboard-navigable grid.
- `EmojiSelected` and `Cancelled` message API.

## License

MIT. Emoji data sourced from the `emoji` library (New BSD) and Unicode CLDR (`emoji-test.txt`, Unicode Data Files License).
