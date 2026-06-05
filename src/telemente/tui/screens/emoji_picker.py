"""Emoji picker screen for reactions (plan 0020).

A searchable grid of ~80 frequently-used reaction emoji. Returns the selected
emoji codepoint via self.dismiss(emoji). Escape dismisses with None.

Names are pre-baked (not from unicodedata) for the curated set.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid, Vertical
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
        width: 50;
        height: auto;
        max-height: 30;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }
    EmojiPickerScreen #emoji-search {
        width: 1fr;
        margin-bottom: 1;
    }
    EmojiPickerScreen #emoji-grid {
        width: 1fr;
        height: auto;
        max-height: 20;
        grid-size: 8;
        grid-rows: auto;
        overflow-y: auto;
    }
    EmojiPickerScreen #emoji-grid Button {
        width: 3;
        min-width: 3;
        height: 2;
        padding: 0;
        border: none;
    }
    EmojiPickerScreen #emoji-hint {
        width: 1fr;
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-container"):
            yield Input(id="emoji-search", placeholder="Search emoji…")
            yield Grid(id="emoji-grid")
            yield Label("Press Enter or click to react", id="emoji-hint")

    def on_mount(self) -> None:
        self._populate_grid(REACTION_EMOJI)
        self.query_one("#emoji-search", Input).focus()

    def _populate_grid(self, emoji_list: list[tuple[str, str]]) -> None:
        grid = self.query_one("#emoji-grid", Grid)
        grid.remove_children()
        for codepoint, name in emoji_list:
            grid.mount(Button(codepoint, tooltip=name))

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
        emoji = str(event.button.label)
        self.dismiss(emoji)

    def action_dismiss_empty(self) -> None:
        self.dismiss("")
