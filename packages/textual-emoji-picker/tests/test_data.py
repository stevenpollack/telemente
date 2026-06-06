"""Tier-1 data tests for textual-emoji-picker.

These tests exercise emoji data loading and filtering with no Textual dependency.
"""

from __future__ import annotations


def test_emoji_data_loads_without_error() -> None:
    import textual_emoji_picker._widget  # noqa: F401 — import triggers module-level load


def test_fully_qualified_count_is_reasonable() -> None:
    from textual_emoji_picker._widget import _FULLY_QUALIFIED

    assert len(_FULLY_QUALIFIED) >= 3000


def test_grinning_face_in_data() -> None:
    from textual_emoji_picker._widget import _FULLY_QUALIFIED

    assert "😀" in _FULLY_QUALIFIED
    en = str(_FULLY_QUALIFIED["😀"].get("en") or "")
    assert "grinning" in en.lower()


def test_max_version_filter() -> None:
    from textual_emoji_picker._widget import _FULLY_QUALIFIED, _apply_version_filter

    filtered = _apply_version_filter(_FULLY_QUALIFIED, 1.0)
    assert len(filtered) < len(_FULLY_QUALIFIED)
    # 😀 is E1.0 — must be included
    assert "😀" in filtered
    # 🫠 is E14.0 melting face — must be excluded
    assert "🫠" not in filtered


def test_skin_tone_base_detected() -> None:
    from textual_emoji_picker._widget import _SKIN_TONE_CAPABLE

    assert "👍" in _SKIN_TONE_CAPABLE, "thumbs up should be skin-tone-capable"


def test_skin_tone_heart_not_capable() -> None:
    from textual_emoji_picker._widget import _SKIN_TONE_CAPABLE

    assert "❤️" not in _SKIN_TONE_CAPABLE, "red heart should NOT be skin-tone-capable"


def test_categories_json_loads() -> None:
    from textual_emoji_picker._widget import _CATEGORIES

    assert isinstance(_CATEGORIES, dict)
    assert len(_CATEGORIES) > 0
    assert "😀" in _CATEGORIES
    assert _CATEGORIES["😀"]["group"] == "Smileys & Emotion"


def test_categories_json_all_have_required_keys() -> None:
    from textual_emoji_picker._widget import _CATEGORIES

    for cp, entry in _CATEGORIES.items():
        assert "group" in entry, f"{cp!r} missing 'group'"
        assert "subgroup" in entry, f"{cp!r} missing 'subgroup'"
        assert "order" in entry, f"{cp!r} missing 'order'"
