"""Tests for telemente.matrix.models (plan 0003)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from telemente.matrix.models import Member, Message, RoomSummary


def test_room_summary_defaults_and_frozen() -> None:
    now = datetime(2024, 1, 1, 12, 0, 0)
    room = RoomSummary(room_id="!abc:matrix.org", display_name="Test Room")
    assert room.room_id == "!abc:matrix.org"
    assert room.display_name == "Test Room"
    assert room.unread_count == 0
    assert room.last_activity is None
    assert room.encrypted is False

    with pytest.raises(FrozenInstanceError):
        room.room_id = "!other:matrix.org"  # type: ignore[misc]

    room2 = RoomSummary(
        room_id="!xyz:example.com",
        display_name="Encrypted Room",
        unread_count=5,
        last_activity=now,
        encrypted=True,
    )
    assert room2.encrypted is True
    assert room2.unread_count == 5
    assert room2.last_activity == now


def test_message_frozen() -> None:
    now = datetime(2024, 6, 1, 9, 30, 0)
    msg = Message(
        event_id="$event1:matrix.org",
        room_id="!room:matrix.org",
        sender="@alice:matrix.org",
        sender_display_name="Alice",
        body="Hello, world!",
        timestamp=now,
    )
    assert msg.body == "Hello, world!"
    assert msg.sender == "@alice:matrix.org"
    assert msg.timestamp == now

    with pytest.raises(FrozenInstanceError):
        msg.body = "changed"  # type: ignore[misc]


def test_member_defaults_and_frozen() -> None:
    member = Member(user_id="@bob:matrix.org", display_name="Bob")
    assert member.user_id == "@bob:matrix.org"
    assert member.display_name == "Bob"
    assert member.power_level == 0

    with pytest.raises(FrozenInstanceError):
        member.user_id = "other"  # type: ignore[misc]

    admin = Member(user_id="@admin:matrix.org", display_name="Admin", power_level=100)
    assert admin.power_level == 100
