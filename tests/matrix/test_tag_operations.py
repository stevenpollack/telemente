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
    stub_delete,
    stub_put,
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
        stub_put(m, tag_url, payload={})

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
        stub_delete(m, tag_url, payload={})

        await client.remove_room_tag(room_id, tag)

    assert len(received) == 1
    emitted_room = next((r for r in received[0].rooms if r.room_id == room_id), None)
    assert emitted_room is not None, "room not in RoomsChanged payload"
    assert tag not in emitted_room.tags, f"tag {tag!r} still in room tags after remove"


async def test_on_sync_clears_tag_overrides_on_m_tag_account_data() -> None:
    """_on_sync clears _tag_overrides when an m.tag account_data event arrives.

    Bug 2 fix: optimistic overrides must be discarded once the server's
    authoritative m.tag event is delivered via sync, so we don't
    permanently shadow server state.
    """
    from types import SimpleNamespace
    from typing import cast

    import nio

    from matrix.helpers import response_callback_for

    room_id = "!room2:example.com"
    tag = "m.favourite"

    nio_mock = build_nio_mock(rooms={room_id: make_nio_room(room_id=room_id, tags={})})
    nio_mock.user_id = USER
    nio_mock.access_token = "tok"
    nio_mock.should_upload_keys = False
    nio_mock.should_query_keys = False

    client = await restore_client(nio_mock)

    # Inject an optimistic override (simulates what set_room_tag does before the HTTP call)
    client._tag_overrides[room_id] = {tag: 0.5}  # pyright: ignore[reportPrivateUsage]

    # Verify the override is visible before sync
    assert tag in next(r for r in client.rooms() if r.room_id == room_id).tags

    # Simulate an _on_sync response that includes an m.tag account_data event for this room.
    account_data_event = SimpleNamespace(type="m.tag")
    fake_sync = cast(
        nio.SyncResponse,
        SimpleNamespace(
            rooms=SimpleNamespace(
                join={
                    room_id: SimpleNamespace(
                        timeline=SimpleNamespace(events=[]),
                        account_data=[account_data_event],
                    )
                }
            )
        ),
    )
    on_sync = response_callback_for(nio_mock, nio.SyncResponse)
    await on_sync(fake_sync)

    # After sync, the optimistic override must be cleared.
    assert not client._tag_overrides.get(room_id)  # pyright: ignore[reportPrivateUsage]
