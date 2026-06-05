"""Smoke tests for locally recorded Matrix fixtures (plan 0016 tier 2).

Requires ``tests/fixtures/nio/recorded/`` from ``scripts/record_nio_fixtures.py``.
Skipped in CI when recordings are absent. Run locally::

    uv run pytest -m recorded -n 0
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import nio
import pytest
from aioresponses import aioresponses

from matrix.helpers import (
    RECORDED_DIR,
    load_fixture,
    load_recorded_meta,
    make_session,
    max_timeline_message_ts_by_room,
    recorded_fixtures_available,
    room_activity_by_id,
    start_sync_with_stubs,
    stub_post,
    stub_sync,
    ts_from_origin_server_ms,
    wait_until,
)
from telemente.matrix.client import MatrixClient

pytestmark = pytest.mark.recorded


def _require_recorded() -> None:
    if not recorded_fixtures_available():
        pytest.skip("No recorded fixtures in tests/fixtures/nio/recorded/")


def _recorded_homeserver() -> str:
    return str(load_recorded_meta()["homeserver"])


def _recorded_user_id() -> str:
    return str(load_fixture("login.json", tier="recorded")["user_id"])


def _recorded_session() -> Any:
    login = load_fixture("login.json", tier="recorded")
    meta = load_recorded_meta()
    return make_session(
        homeserver=str(meta["homeserver"]),
        user_id=str(login["user_id"]),
        device_id=str(login["device_id"]),
        access_token=str(login["access_token"]),
    )


@asynccontextmanager
async def _recorded_nio_client() -> AsyncIterator[nio.AsyncClient]:
    """nio client whose homeserver matches the recorded ``meta.json``."""
    homeserver = _recorded_homeserver()
    user_id = _recorded_user_id()
    client = nio.AsyncClient(homeserver, user_id)
    try:
        yield client
    finally:
        await client.close()


async def test_recorded_login_fixture_parses() -> None:
    """Recorded login JSON parses through real nio without a live POST."""
    _require_recorded()
    homeserver = _recorded_homeserver()
    login = load_fixture("login.json", tier="recorded")
    login_url = f"{homeserver}/_matrix/client/v3/login"

    async with _recorded_nio_client() as nio_client:
        with aioresponses() as m:
            stub_post(m, login_url, payload=login)
            client = MatrixClient(homeserver, nio_client=nio_client)
            session = await client.login(str(login["user_id"]), "unused-password")

        assert session.user_id == login["user_id"]
        assert session.device_id == login["device_id"]
        assert session.access_token == login["access_token"]
        assert session.homeserver == homeserver


async def test_recorded_initial_sync_populates_rooms() -> None:
    """Recorded full-state sync populates at least one joined room."""
    _require_recorded()
    homeserver = _recorded_homeserver()
    sync = load_fixture("sync_initial.json", tier="recorded")

    async with _recorded_nio_client() as nio_client:
        with aioresponses() as m:
            client = MatrixClient(homeserver, nio_client=nio_client)
            await client.restore(_recorded_session())
            await start_sync_with_stubs(
                client,
                m,
                initial_sync=sync,
                min_rooms=1,
                homeserver=homeserver,
            )

        assert len(client.rooms()) >= 1


async def test_recorded_initial_sync_sets_last_activity_from_timeline() -> None:
    """Rooms with timeline messages in the recording get last_activity on rooms()."""
    _require_recorded()
    homeserver = _recorded_homeserver()
    sync = load_fixture("sync_initial.json", tier="recorded")
    expected = max_timeline_message_ts_by_room(sync)
    if not expected:
        pytest.skip("Recorded sync has no timeline m.room.message events")

    async with _recorded_nio_client() as nio_client:
        with aioresponses() as m:
            client = MatrixClient(homeserver, nio_client=nio_client)
            await client.restore(_recorded_session())
            await start_sync_with_stubs(
                client,
                m,
                initial_sync=sync,
                min_rooms=1,
                homeserver=homeserver,
            )

        activity = room_activity_by_id(client.rooms())
        matched = 0
        for room_id, ts_ms in expected.items():
            actual = activity.get(room_id)
            if actual is None:
                continue
            assert actual == ts_from_origin_server_ms(ts_ms)
            matched += 1
        assert matched >= 1


async def test_recorded_incremental_sync_parses_when_present() -> None:
    """Recorded incremental sync replays without error when captured."""
    _require_recorded()
    incremental_path = RECORDED_DIR / "sync_incremental.json"
    if not incremental_path.is_file():
        pytest.skip("No recorded sync_incremental.json — run record script without --full-sync")

    homeserver = _recorded_homeserver()
    initial = load_fixture("sync_initial.json", tier="recorded")
    incremental = load_fixture("sync_incremental.json", tier="recorded")
    idle = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}

    async with _recorded_nio_client() as nio_client:
        with aioresponses() as m:
            stub_sync(m, initial, homeserver=homeserver)
            stub_sync(m, incremental, homeserver=homeserver)
            stub_sync(m, idle, homeserver=homeserver, repeat=True)

            client = MatrixClient(homeserver, nio_client=nio_client)
            await client.restore(_recorded_session())
            await client.start_sync()
            await wait_until(lambda: len(client.rooms()) >= 1)
            await client.close()

        assert len(client.rooms()) >= 1
