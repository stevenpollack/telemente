"""Deterministic per-sender color assignment.

Colors are assigned by hashing the Matrix user ID (stable, unique) against a
fixed palette.  Both MessageView and MemberList import this to stay in sync.
"""

from __future__ import annotations

# 12-color palette chosen for readability on dark terminals.
# Avoid pure white/black and colors too close to $warning (yellow) or $error (red).
_PALETTE: tuple[str, ...] = (
    "#7ec8e3",  # sky blue
    "#a8d8a8",  # soft green
    "#f4a261",  # warm orange
    "#c77dff",  # lavender
    "#06d6a0",  # teal
    "#f72585",  # hot pink
    "#4cc9f0",  # cyan
    "#b5e48c",  # lime
    "#ffd166",  # amber
    "#e07a5f",  # terracotta
    "#9b5de5",  # purple
    "#00b4d8",  # ocean blue
)


def sender_color(user_id: str) -> str:
    """Return a hex color string for *user_id*, stable across calls."""
    return _PALETTE[hash(user_id) % len(_PALETTE)]
