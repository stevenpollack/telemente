"""Emoji picker screen for reactions (plan 0020/0027).

Thin ModalScreen wrapper around the EmojiPicker widget from the
textual-emoji-picker package. Returns the selected emoji via self.dismiss(emoji);
Escape dismisses with empty string.

The legacy REACTION_EMOJI / SKIN_TONE_CAPABLE lists remain importable from this
module for backwards compatibility (see _emoji_data_legacy.py).
"""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_config_dir
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual_emoji_picker import EmojiPicker as EmojiPicker

_PERSIST_PATH = Path(user_config_dir("telemente")) / "emoji_picker.json"

from telemente.tui.screens._emoji_data_legacy import (
    REACTION_EMOJI as REACTION_EMOJI,
)
from telemente.tui.screens._emoji_data_legacy import (
    SKIN_TONE_CAPABLE as SKIN_TONE_CAPABLE,
)

__all__ = ["REACTION_EMOJI", "SKIN_TONE_CAPABLE", "EmojiPicker", "EmojiPickerScreen"]


class EmojiPickerScreen(ModalScreen[str]):
    """Modal wrapper around EmojiPicker.

    Dismisses with the selected emoji string, or with empty string on Escape.
    Escape is handled exclusively by the EmojiPicker widget (posts Cancelled),
    which bubbles to on_emoji_picker_cancelled — no duplicate binding here.
    """

    DEFAULT_CSS = """
    EmojiPickerScreen {
        align: center middle;
    }
    """

    def compose(self) -> ComposeResult:
        yield EmojiPicker(persist_path=_PERSIST_PATH)

    def on_emoji_picker_emoji_selected(self, event: EmojiPicker.EmojiSelected) -> None:
        self.dismiss(event.emoji)

    def on_emoji_picker_cancelled(self, event: EmojiPicker.Cancelled) -> None:
        self.dismiss("")
