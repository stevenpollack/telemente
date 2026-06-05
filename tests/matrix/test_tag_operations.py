"""Tier-1 tests for tag operations emitting RoomsChanged (plan 0021 Bug 2).

Tests:
  test_set_room_tag_emits_rooms_changed
  test_remove_room_tag_emits_rooms_changed
"""

from __future__ import annotations

import re

from aioresponses import aioresponses

from matrix.helpers import (
    HOMESERVER,
    USER,
    build_nio_mock,
    make_nio_room,
    restore_client,
)
from telemente.matrix.client import RoomsChanged


async def test_set_room_tag_emits_rooms_changed() -> None:
    """set_room_tag succeeds → RoomsChanged is emitted immediately with the new tag."""
    room_id = "!room1:example.com"
    tag = "m.mute"

    # Room starts with no tags.
    nio_mock = build_nio_mock(rooms={room_id: make_nio_room(room_id=room_id, tags={})})
    client = await restore_client(nio_mock)

    received: list[RoomsChanged] = []

    def _handler(event: object) -> None:
        if isinstance(event, RoomsChanged):
            received.append(event)

    client.subscribe(_handler)

    with aioresponses() as m:
        tag_url = re.compile(
            rf"^{re.escape(HOMESERVER)}/_matrix/client/v3/user/{re.escape(USER)}"
            rf"/rooms/{re.escape(room_id)}/tags/{re.escape(tag)}"
        )
        m.put(tag_url, payload={}, status=200)

        await client.set_room_tag(room_id, tag)

    assert len(received) == 1
    emitted_room = next((r for r in received[0].rooms if r.room_id == room_id), None)
    assert emitted_room is not None, "room not in RoomsChanged payload"
    assert tag in emitted_room.tags, f"tag {tag!r} not in room tags after set"


async def test_remove_room_tag_emits_rooms_changed() -> None:
    """remove_room_tag succeeds → RoomsChanged is emitted with the tag gone."""
    room_id = "!room1:example.com"
    tag = "m.mute"

    # Room starts with the tag already present.
    nio_mock = build_nio_mock(
        rooms={
            room_id: make_nio_room(room_id=room_id, tags={tag: {"order": 0.5}}),
        }
    )
    client = await restore_client(nio_mock)

    received: list[RoomsChanged] = []

    def _handler(event: object) -> None:
        if isinstance(event, RoomsChanged):
            received.append(event)

    client.subscribe(_handler)

    with aioresponses() as m:
        tag_url = re.compile(
            rf"^{re.escape(HOMESERVER)}/_matrix/client/v3/user/{re.escape(USER)}"
            rf"/rooms/{re.escape(room_id)}/tags/{re.escape(tag)}"
        )
        m.delete(tag_url, payload={}, status=200)

        await client.remove_room_tag(room_id, tag)

    assert len(received) == 1
    emitted_room = next((r for r in received[0].rooms if r.room_id == room_id), None)
    assert emitted_room is not None, "room not in RoomsChanged payload"
    assert tag not in emitted_room.tags, f"tag {tag!r} still in room tags after remove"
