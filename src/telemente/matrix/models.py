"""Plain dataclasses for the matrix layer (plan 0003).

These are the ONLY types that cross the matrix/ boundary — no nio types leak out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RoomSummary:
    """Summary of a Matrix room for display in the room list."""

    room_id: str
    display_name: str
    unread_count: int = 0
    last_activity: datetime | None = None
    encrypted: bool = False


@dataclass(frozen=True, slots=True)
class Message:
    """A single text message in a Matrix room."""

    event_id: str
    room_id: str
    sender: str
    sender_display_name: str
    body: str
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class Member:
    """A member of a Matrix room."""

    user_id: str
    display_name: str
    power_level: int = 0
