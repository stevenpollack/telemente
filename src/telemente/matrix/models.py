"""Plain dataclasses for the matrix layer (plan 0003).

These are the ONLY types that cross the matrix/ boundary — no nio types leak out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RoomSummary:
    """Summary of a Matrix room for display in the room list."""

    room_id: str
    display_name: str
    unread_count: int = 0
    last_activity: datetime | None = None
    encrypted: bool = False
    tags: dict[str, float | None] = field(default_factory=lambda: {})
    # e.g. {"m.favourite": 0.5, "m.lowpriority": None}


@dataclass(frozen=True, slots=True)
class Message:
    """A single text message in a Matrix room."""

    event_id: str
    room_id: str
    sender: str
    sender_display_name: str
    body: str
    timestamp: datetime
    # Set for media messages (image/video/audio/file) — an HTTPS URL.
    media_url: str | None = None
    # Human-readable media type label, e.g. "image", "video", "audio", "file"
    media_type: str | None = None
    # Aggregated reactions: emoji -> [sender_user_id, ...]
    reactions: dict[str, list[str]] = field(default_factory=lambda: {})
    # Set when this message is a reply — the event_id of the parent message.
    reply_to_event_id: str | None = None
    # True when this message was redacted (deleted). Body is the tombstone string.
    redacted: bool = False


@dataclass(frozen=True, slots=True)
class Member:
    """A member of a Matrix room."""

    user_id: str
    display_name: str
    power_level: int = 0
