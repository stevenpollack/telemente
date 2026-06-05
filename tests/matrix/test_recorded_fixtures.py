"""Smoke tests for locally recorded Matrix fixtures (plan 0016 tier 2).

Requires ``tests/fixtures/nio/recorded/`` from ``scripts/record_nio_fixtures.py``.
Skipped in CI when recordings are absent. Run locally::

    uv run pytest -m recorded -n 0
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import nio
import pytest
from aioresponses import aioresponses

from matrix.helpers import (
    RECORDED_DIR,
    load_fixture,
    load_recorded_meta,
    make_session,
    max_timeline_event_ts_by_room,
    recorded_fixtures_available,
    room_activity_by_id,
    start_sync_with_stubs,
    stub_post,
    stub_sync,
    ts_from_origin_server_ms,
    wait_until,
)
from telemente.matrix.client import MatrixClient

_SKIP_RECORDED = pytest.mark.skipif(
    not recorded_fixtures_available(),
    reason=(
        "No recorded fixtures in tests/fixtures/nio/recorded/ — "
        "run: uv run python scripts/record_nio_fixtures.py"
    ),
)
pytestmark = [pytest.mark.recorded, _SKIP_RECORDED]


def _recorded_homeserver() -> str:
    return str(load_recorded_meta()["homeserver"])


def _recorded_session() -> Any:
    login = load_fixture("login.json", tier="recorded")
    meta = load_recorded_meta()
    return make_session(
        homeserver=str(meta["homeserver"]),
        user_id=str(login["user_id"]),
        device_id=str(login["device_id"]),
        access_token=str(login["access_token"]),
    )


@pytest.fixture
async def recorded_nio_client() -> AsyncGenerator[nio.AsyncClient, None]:
    """nio.AsyncClient pointing at the recorded homeserver/user."""
    meta = load_recorded_meta()
    login = load_fixture("login.json", tier="recorded")
    client = nio.AsyncClient(str(meta["homeserver"]), str(login["user_id"]))
    try:
        yield client
    finally:
        await client.close()


async def test_recorded_login_fixture_parses(recorded_nio_client: nio.AsyncClient) -> None:
    """Recorded login JSON parses through real nio without a live POST."""
    homeserver = _recorded_homeserver()
    login = load_fixture("login.json", tier="recorded")
    login_url = f"{homeserver}/_matrix/client/v3/login"

    with aioresponses() as m:
        stub_post(m, login_url, payload=login)
        client = MatrixClient(homeserver, nio_client=recorded_nio_client)
        session = await client.login(str(login["user_id"]), "unused-password")

    assert session.user_id == login["user_id"]
    assert session.device_id == login["device_id"]
    assert session.access_token == login["access_token"]
    assert session.homeserver == homeserver


async def test_recorded_login_fixture_is_nio_loginresponse(
    recorded_nio_client: nio.AsyncClient,
) -> None:
    """Recorded login fixture round-trips through nio as a LoginResponse, not a dict."""
    homeserver = _recorded_homeserver()
    login = load_fixture("login.json", tier="recorded")
    login_url = f"{homeserver}/_matrix/client/v3/login"

    with aioresponses() as m:
        stub_post(m, login_url, payload=login)
        resp = await recorded_nio_client.login("unused-password", device_name="test")

    assert isinstance(resp, nio.LoginResponse), (
        f"Expected LoginResponse, got {type(resp).__name__}: {resp}"
    )
    assert resp.user_id == login["user_id"]
    assert resp.device_id == login["device_id"]


async def test_recorded_initial_sync_populates_rooms(recorded_nio_client: nio.AsyncClient) -> None:
    """Recorded full-state sync populates at least one joined room."""
    homeserver = _recorded_homeserver()
    sync = load_fixture("sync_initial.json", tier="recorded")

    with aioresponses() as m:
        client = MatrixClient(homeserver, nio_client=recorded_nio_client)
        await client.restore(_recorded_session())
        await start_sync_with_stubs(
            client,
            m,
            initial_sync=sync,
            min_rooms=1,
            homeserver=homeserver,
        )

    assert len(client.rooms()) >= 1


async def test_recorded_sync_fixture_has_known_rooms(recorded_nio_client: nio.AsyncClient) -> None:
    """After replaying the recorded sync, client.rooms matches meta.json room_ids."""
    meta = load_recorded_meta()
    room_ids: list[str] = list(meta.get("room_ids", []))
    if not room_ids:
        pytest.skip("meta.json has no room_ids (re-record with updated script)")

    homeserver = _recorded_homeserver()
    sync = load_fixture("sync_initial.json", tier="recorded")

    with aioresponses() as m:
        client = MatrixClient(homeserver, nio_client=recorded_nio_client)
        await client.restore(_recorded_session())
        await start_sync_with_stubs(
            client,
            m,
            initial_sync=sync,
            min_rooms=1,
            homeserver=homeserver,
        )

    actual_ids = {r.room_id for r in client.rooms()}
    for room_id in room_ids:
        assert room_id in actual_ids, f"Expected room {room_id!r} missing from client.rooms()"


async def test_recorded_initial_sync_sets_last_activity_from_timeline(
    recorded_nio_client: nio.AsyncClient,
) -> None:
    """Rooms with timeline events in the recording get last_activity on rooms()."""
    homeserver = _recorded_homeserver()
    sync = load_fixture("sync_initial.json", tier="recorded")
    expected = max_timeline_event_ts_by_room(sync)
    if not expected:
        pytest.skip("Recorded sync has no timeline events")

    with aioresponses() as m:
        client = MatrixClient(homeserver, nio_client=recorded_nio_client)
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


async def test_recorded_incremental_sync_parses_when_present(
    recorded_nio_client: nio.AsyncClient,
) -> None:
    """Recorded incremental sync replays without error when captured."""
    incremental_path = RECORDED_DIR / "sync_incremental.json"
    if not incremental_path.is_file():
        pytest.skip(
            "No recorded sync_incremental.json — "
            "run: uv run python scripts/record_nio_fixtures.py --incremental"
        )

    homeserver = _recorded_homeserver()
    initial = load_fixture("sync_initial.json", tier="recorded")
    incremental = load_fixture("sync_incremental.json", tier="recorded")
    idle: dict[str, Any] = {"next_batch": "idle", "rooms": {"join": {}, "invite": {}, "leave": {}}}

    with aioresponses() as m:
        stub_sync(m, initial, homeserver=homeserver)
        stub_sync(m, incremental, homeserver=homeserver)
        stub_sync(m, idle, homeserver=homeserver, repeat=True)

        client = MatrixClient(homeserver, nio_client=recorded_nio_client)
        await client.restore(_recorded_session())
        await client.start_sync()
        await wait_until(lambda: len(client.rooms()) >= 1)
        await client.close()

    assert len(client.rooms()) >= 1
