"""Emoji picker screen for reactions (plan 0020).

A searchable grid of ~80 frequently-used reaction emoji. Returns the selected
emoji codepoint via self.dismiss(emoji). Escape dismisses with None.

Names are pre-baked (not from unicodedata) for the curated set.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

# ---------------------------------------------------------------------------
# Curated reaction emoji set (~80 entries from Element Web default reactions)
# (codepoint, search-name) — names are used for filtering only
# ---------------------------------------------------------------------------

REACTION_EMOJI: list[tuple[str, str]] = [
    # Faces / expressions
    ("😀", "grinning face"),
    ("😁", "beaming face with smiling eyes"),
    ("😂", "face with tears of joy"),
    ("🤣", "rolling on the floor laughing"),
    ("😃", "grinning face with big eyes"),
    ("😄", "grinning face with smiling eyes"),
    ("😅", "grinning face with sweat"),
    ("😆", "grinning squinting face"),
    ("😉", "winking face"),
    ("😊", "smiling face with smiling eyes"),
    ("😋", "face savoring food"),
    ("😎", "smiling face with sunglasses"),
    ("😍", "smiling face with heart-eyes"),
    ("🥰", "smiling face with hearts"),
    ("😘", "face blowing a kiss"),
    ("😗", "kissing face"),
    ("🙂", "slightly smiling face"),
    ("🙃", "upside down face"),
    ("😐", "neutral face"),
    ("😑", "expressionless face"),
    ("😶", "face without mouth"),
    ("🤔", "thinking face"),
    ("🤨", "face with raised eyebrow"),
    ("😏", "smirking face"),
    ("😒", "unamused face"),
    ("🙄", "face with rolling eyes"),
    ("😬", "grimacing face"),
    ("🤥", "lying face"),
    ("😔", "pensive face"),
    ("😪", "sleepy face"),
    ("🤤", "drooling face"),
    ("😴", "sleeping face"),
    ("😷", "face with medical mask"),
    ("🤒", "face with thermometer"),
    ("🤕", "face with head-bandage"),
    ("🤢", "nauseated face"),
    ("🤮", "face vomiting"),
    ("🤧", "sneezing face"),
    ("😵", "dizzy face"),
    ("🤯", "exploding head"),
    ("🤠", "cowboy hat face"),
    ("🥳", "partying face"),
    ("😈", "smiling face with horns"),
    ("👿", "angry face with horns"),
    ("👻", "ghost"),
    ("💀", "skull"),
    ("🤡", "clown face"),
    ("👾", "alien monster"),
    ("🤖", "robot"),
    ("😺", "grinning cat"),
    ("😸", "grinning cat with smiling eyes"),
    ("😹", "cat with tears of joy"),
    ("😻", "smiling cat with heart-eyes"),
    ("😿", "crying cat"),
    # Gestures / hands
    ("👍", "thumbs up"),
    ("👎", "thumbs down"),
    ("👌", "ok hand"),
    ("🤌", "pinched fingers"),
    ("✌️", "victory hand"),
    ("🤞", "crossed fingers"),
    ("🤟", "love-you gesture"),
    ("🤘", "sign of the horns"),
    ("👏", "clapping hands"),
    ("🙌", "raising hands"),
    ("🤲", "palms up together"),
    ("🙏", "folded hands"),
    ("🤝", "handshake"),
    ("💪", "flexed biceps"),
    ("✍️", "writing hand"),
    ("👀", "eyes"),
    ("🫶", "heart hands"),
    ("🫱", "rightwards hand"),
    ("🫲", "leftwards hand"),
    ("🫳", "palm down hand"),
    ("🫴", "palm up hand"),
    ("🫵", "index pointing at viewer"),
    ("👆", "backhand index pointing up"),
    ("👇", "backhand index pointing down"),
    ("👈", "backhand index pointing left"),
    ("👉", "backhand index pointing right"),
    ("☝️", "index pointing up"),
    ("✋", "raised hand"),
    ("🤚", "raised back of hand"),
    ("🖐️", "hand with fingers splayed"),
    ("🖖", "vulcan salute"),
    ("🤙", "call me hand"),
    ("🦾", "mechanical arm"),
    ("🦿", "mechanical leg"),
    # Hearts / symbols
    ("❤️", "red heart"),
    ("🧡", "orange heart"),
    ("💛", "yellow heart"),
    ("💚", "green heart"),
    ("💙", "blue heart"),
    ("💜", "purple heart"),
    ("🖤", "black heart"),
    ("🤍", "white heart"),
    ("💔", "broken heart"),
    ("❣️", "heart exclamation"),
    ("💕", "two hearts"),
    ("💞", "revolving hearts"),
    ("💓", "beating heart"),
    ("💗", "growing heart"),
    ("💖", "sparkling heart"),
    ("💘", "heart with arrow"),
    ("💝", "heart with ribbon"),
    # Misc reactions
    ("🔥", "fire"),
    ("💯", "hundred points"),
    ("💥", "collision"),
    ("⭐", "star"),
    ("✨", "sparkles"),
    ("🎉", "party popper"),
    ("🎊", "confetti ball"),
    ("🤦", "person facepalming"),
    ("🤷", "person shrugging"),
    ("👋", "waving hand"),
    ("🫡", "saluting face"),
    ("😮", "face with open mouth"),
    ("😲", "astonished face"),
    ("😯", "hushed face"),
]


# ---------------------------------------------------------------------------
# Skin-tone support
# ---------------------------------------------------------------------------

# Fitzpatrick modifier codepoints (U+1F3FB-U+1F3FF), light to dark.
_FITZPATRICK_MODIFIERS: tuple[str, ...] = (
    "\U0001f3fb",  # light
    "\U0001f3fc",  # medium-light
    "\U0001f3fd",  # medium
    "\U0001f3fe",  # medium-dark
    "\U0001f3ff",  # dark
)

# Base codepoints (stripped of any trailing U+FE0F) that accept a Fitzpatrick
# modifier.  Conservative whitelist — only hands, gestures, and people emoji.
# Faces, hearts, objects, and any base ending with U+FE0F are excluded because
# appending a modifier after FE0F is invalid, and most such sequences garble.
SKIN_TONE_CAPABLE: frozenset[str] = frozenset(
    {
        # core hand gestures already in the original list
        "👍",
        "👎",
        "👌",
        "🤌",
        "🤞",
        "🤟",
        "🤘",
        "👏",
        "🙌",
        "🤲",
        "🙏",
        "💪",
        # newly added hand/gesture bases
        "🫶",
        "🫱",
        "🫲",
        "🫳",
        "🫴",
        "🫵",
        "👆",
        "👇",
        "👈",
        "👉",
        "✋",
        "🤚",
        "🖖",
        "🤙",
        # person emoji
        "🤦",
        "🤷",
        "👋",
        # fist / index
        "✊",
        "👊",
        "🤛",
        "🤜",
    }
)


class EmojiPickerScreen(ModalScreen[str]):
    """A searchable emoji grid for reactions.

    Dismisses with the selected emoji string, or with empty string on Escape.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss_empty", "Cancel"),
    ]

    DEFAULT_CSS = """
    EmojiPickerScreen {
        align: center middle;
    }
    EmojiPickerScreen #picker-container {
        width: 52;
        height: auto;
        max-height: 32;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }
    EmojiPickerScreen #emoji-search {
        width: 1fr;
        margin-bottom: 1;
    }
    EmojiPickerScreen #skin-tone-row {
        height: 2;
        width: 1fr;
        margin-bottom: 1;
    }
    EmojiPickerScreen #skin-tone-row Button {
        width: 4;
        min-width: 4;
        height: 2;
        padding: 0;
        border: none;
    }
    EmojiPickerScreen #skin-tone-row Button:hover {
        border: none;
        background: $accent 30%;
    }
    EmojiPickerScreen #skin-tone-row Button.-selected-swatch {
        border: tall $accent;
    }
    EmojiPickerScreen #emoji-grid {
        width: 1fr;
        height: auto;
        max-height: 20;
        grid-size: 8;
        grid-rows: 2;
        overflow-y: auto;
    }
    EmojiPickerScreen #emoji-grid Button {
        width: 3;
        min-width: 3;
        height: 2;
        padding: 0;
        border: none;
    }
    EmojiPickerScreen #emoji-grid Button:hover {
        border: none;
        background: $accent 30%;
    }
    EmojiPickerScreen #emoji-hint {
        width: 1fr;
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        # Currently selected Fitzpatrick modifier; empty string means none.
        self._skin_modifier: str = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-container"):
            yield Input(id="emoji-search", placeholder="Search emoji…")
            with Horizontal(id="skin-tone-row"):
                # "none" swatch — represented by a neutral circle
                yield Button("\U0001f3f3", id="swatch-none")  # white flag as neutral glyph
                for mod in _FITZPATRICK_MODIFIERS:
                    yield Button(mod, id=f"swatch-{ord(mod):x}")
            yield Grid(id="emoji-grid")
            yield Label("Press Enter or click to react", id="emoji-hint")

    def on_mount(self) -> None:
        self._populate_grid(REACTION_EMOJI)
        self.query_one("#emoji-search", Input).focus()

    def _populate_grid(self, emoji_list: list[tuple[str, str]]) -> None:
        """Update the emoji grid with a diff so existing buttons are reused.

        Bug 4: avoid full destroy/remount which causes flicker on hover, and
        never pass tooltip= (also causes hover flicker via Tooltip DOM mutations).
        """
        grid = self.query_one("#emoji-grid", Grid)
        existing = list(grid.query(Button))
        target_count = len(emoji_list)

        # Update existing buttons in-place.
        for i, (cp, _name) in enumerate(emoji_list):
            if i < len(existing):
                if str(existing[i].label) != cp:
                    existing[i].label = cp
            else:
                # Bug 4: no tooltip= parameter.
                grid.mount(Button(cp))

        # Remove surplus buttons.
        for btn in existing[target_count:]:
            btn.remove()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "emoji-search":
            return
        query = event.value.strip().lower()
        if query:
            filtered = [(cp, nm) for cp, nm in REACTION_EMOJI if query in nm.lower()]
        else:
            filtered = REACTION_EMOJI
        self._populate_grid(filtered)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        # Swatch buttons — update selected modifier, do not dismiss.
        if btn_id == "swatch-none" or btn_id.startswith("swatch-"):
            # Clear the visual selection on all swatches first.
            from textual.containers import Horizontal as _H

            for swatch in self.query_one("#skin-tone-row", _H).query(Button):
                swatch.remove_class("-selected-swatch")
            if btn_id == "swatch-none":
                self._skin_modifier = ""
            else:
                self._skin_modifier = str(event.button.label)
                event.button.add_class("-selected-swatch")
            event.stop()
            return

        # Emoji grid button — apply modifier if capable, then dismiss.
        base = str(event.button.label)
        if self._skin_modifier and base in SKIN_TONE_CAPABLE:
            # Strip a trailing U+FE0F variation selector before appending the
            # modifier; appending after FE0F produces an invalid sequence.
            clean_base = base.rstrip("️")
            self.dismiss(clean_base + self._skin_modifier)
        else:
            self.dismiss(base)

    def action_dismiss_empty(self) -> None:
        self.dismiss("")
