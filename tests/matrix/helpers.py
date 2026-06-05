"""Shared helpers for matrix unit/integration tests (plan 0017).

Builders and ``restore_client()`` keep tests on the public MatrixClient API.
Typed ``stub_*`` helpers wrap ``aioresponses`` for a clean Pyright LSP.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from re import Pattern
from typing import Any, Literal, Protocol, cast
from unittest.mock import AsyncMock, MagicMock

from telemente.config import Session
from telemente.matrix.client import MatrixClient
from telemente.matrix.models import RoomSummary

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "nio"
SYNTHETIC_DIR = FIXTURES_ROOT / "synthetic"
RECORDED_DIR = FIXTURES_ROOT / "recorded"
FixtureTier = Literal["synthetic", "recorded"]

HOMESERVER = "https://matrix.example.com"
USER = "@alice:example.com"
PASSWORD = "s3cret"
DEVICE_ID = "TESTDEVICE"
TOKEN = "access_token_xyz"


class HttpMocker(Protocol):
    """Narrow HTTP-stub surface used in tests (avoids aioresponses ``Pattern[Unknown]``)."""

    @property
    def requests(self) -> Mapping[object, object]: ...

    def get(
        self,
        url: str | Pattern[str],
        *,
        payload: dict[str, Any] | None = ...,
        status: int = ...,
        body: str = ...,
        repeat: bool = ...,
    ) -> None: ...

    def post(
        self,
        url: str,
        *,
        payload: dict[str, Any] | None = ...,
        status: int = ...,
        body: str = ...,
    ) -> None: ...

    def put(
        self,
        url: str,
        *,
        payload: dict[str, Any] | None = ...,
        status: int = ...,
        body: str = ...,
    ) -> None: ...

    def delete(
        self,
        url: str,
        *,
        payload: dict[str, Any] | None = ...,
        status: int = ...,
        body: str = ...,
    ) -> None: ...


def http_stub_count(m: HttpMocker) -> int:
    """Return how many HTTP requests were recorded on an ``aioresponses`` context."""
    return len(m.requests)


def stub_get(
    m: HttpMocker,
    url: str | Pattern[str],
    *,
    payload: dict[str, Any] | None = None,
    status: int = 200,
    body: str | None = None,
    repeat: bool = False,
) -> None:
    """Register a stubbed GET response on an ``aioresponses`` context."""
    kwargs: dict[str, Any] = {"status": status}
    if repeat:
        kwargs["repeat"] = True
    if body is not None:
        m.get(url, body=body, **kwargs)
    elif payload is not None:
        m.get(url, payload=payload, **kwargs)
    else:
        m.get(url, **kwargs)


def stub_post(
    m: HttpMocker,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    status: int = 200,
    body: str | None = None,
) -> None:
    """Register a stubbed POST response on an ``aioresponses`` context."""
    if body is not None:
        m.post(url, body=body, status=status)
    elif payload is not None:
        m.post(url, payload=payload, status=status)
    else:
        m.post(url, status=status)


def stub_put(
    m: HttpMocker,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    status: int = 200,
    body: str | None = None,
) -> None:
    """Register a stubbed PUT response on an ``aioresponses`` context."""
    if body is not None:
        m.put(url, body=body, status=status)
    elif payload is not None:
        m.put(url, payload=payload, status=status)
    else:
        m.put(url, status=status)


def stub_delete(
    m: HttpMocker,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    status: int = 200,
    body: str | None = None,
) -> None:
    """Register a stubbed DELETE response on an ``aioresponses`` context."""
    if body is not None:
        m.delete(url, body=body, status=status)
    elif payload is not None:
        m.delete(url, payload=payload, status=status)
    else:
        m.delete(url, status=status)


def make_session(
    *,
    homeserver: str = HOMESERVER,
    user_id: str = USER,
    device_id: str = DEVICE_ID,
    access_token: str = TOKEN,
) -> Session:
    return Session(
        homeserver=homeserver,
        user_id=user_id,
        device_id=device_id,
        access_token=access_token,
    )


def make_login_response() -> Any:
    """A minimal fake nio LoginResponse."""
    obj = MagicMock()
    obj.user_id = USER
    obj.device_id = DEVICE_ID
    obj.access_token = TOKEN
    return obj


def make_nio_room(
    room_id: str = "!room1:example.com",
    display_name: str = "Test Room",
    encrypted: bool = False,
    tags: dict[str, dict[str, float] | None] | None = None,
) -> Any:
    """A minimal fake nio MatrixRoom."""
    room = MagicMock()
    room.room_id = room_id
    room.display_name = display_name
    room.encrypted = encrypted
    room.users = {}
    room.tags = tags or {}
    room.power_levels = MagicMock()
    room.power_levels.users = {}
    return room


def make_text_event(
    event_id: str = "$ev1:example.com",
    sender: str = USER,
    body: str = "Hello!",
    server_timestamp: int = 1_700_000_000_000,
    reply_to_event_id: str | None = None,
) -> Any:
    """A minimal fake nio RoomMessageText event."""
    import nio

    ev = MagicMock(spec=nio.RoomMessageText)
    ev.event_id = event_id
    ev.sender = sender
    ev.body = body
    ev.server_timestamp = server_timestamp
    content: dict[str, Any] = {}
    if reply_to_event_id is not None:
        content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to_event_id}}
    ev.source = {"content": content}
    return ev


def make_rooms_response(chunk: list[Any]) -> Any:
    """A fake nio.RoomMessagesResponse wrapping a given event list."""
    import nio

    resp = MagicMock(spec=nio.RoomMessagesResponse)
    resp.chunk = chunk
    return resp


def make_media_event(
    event_id: str = "$m1:example.com",
    sender: str = USER,
    body: str = "photo.jpg",
    url: str = "mxc://example.com/abc123",
    server_timestamp: int = 1_700_000_001_000,
    kind: str = "image",
) -> Any:
    """A minimal fake nio RoomMessageMedia event."""
    import nio

    cls = {
        "image": nio.RoomMessageImage,
        "video": nio.RoomMessageVideo,
        "audio": nio.RoomMessageAudio,
        "file": nio.RoomMessageFile,
    }[kind]
    ev = MagicMock(spec=cls)
    ev.event_id = event_id
    ev.sender = sender
    ev.body = body
    ev.url = url
    ev.server_timestamp = server_timestamp
    return ev


def make_reaction_event(
    event_id: str = "$r1:example.com",
    sender: str = "@bob:example.com",
    reacts_to: str = "$ev1:example.com",
    key: str = "👍",
    server_timestamp: int = 1_700_000_003_000,
) -> Any:
    """A minimal fake nio ReactionEvent."""
    import nio

    ev = MagicMock(spec=nio.ReactionEvent)
    ev.event_id = event_id
    ev.sender = sender
    ev.reacts_to = reacts_to
    ev.key = key
    ev.server_timestamp = server_timestamp
    return ev


def make_megolm_event(
    event_id: str = "$enc1:example.com",
    sender: str = USER,
    server_timestamp: int = 1_700_000_002_000,
) -> Any:
    """A minimal fake nio MegolmEvent."""
    import nio

    ev = MagicMock(spec=nio.MegolmEvent)
    ev.event_id = event_id
    ev.sender = sender
    ev.server_timestamp = server_timestamp
    ev.session_id = "fake_session"
    return ev


def build_nio_mock(
    login_return: Any = None,
    rooms: dict[str, Any] | None = None,
) -> AsyncMock:
    """Build a fully-mocked AsyncMock nio client."""
    import nio

    mock = AsyncMock(spec=nio.AsyncClient)
    mock.access_token = TOKEN
    mock.user_id = USER
    mock.device_id = DEVICE_ID
    mock.rooms = rooms or {}
    mock.should_upload_keys = False
    mock.should_query_keys = False
    if login_return is not None:
        mock.login.return_value = login_return
    return mock


async def restore_client(
    nio_mock: AsyncMock,
    *,
    homeserver: str = HOMESERVER,
    session: Session | None = None,
) -> MatrixClient:
    """Return a MatrixClient restored via the public ``restore()`` path."""
    client = MatrixClient(homeserver, nio_client=nio_mock)
    await client.restore(session or make_session(homeserver=homeserver))
    return client


def event_callback_for(
    nio_mock: AsyncMock,
    event_type: type[Any],
) -> Callable[..., Awaitable[None]]:
    """Return the handler registered via ``add_event_callback`` for ``event_type``."""
    for call in nio_mock.add_event_callback.call_args_list:
        if call.args[1] == event_type:
            return cast(Callable[..., Awaitable[None]], call.args[0])
    raise AssertionError(f"No add_event_callback registration for {event_type!r}")


def response_callback_for(
    nio_mock: AsyncMock,
    response_type: type[Any],
) -> Callable[..., Awaitable[None]]:
    """Return the handler registered via ``add_response_callback``."""
    for call in nio_mock.add_response_callback.call_args_list:
        if call.args[1] == response_type:
            return cast(Callable[..., Awaitable[None]], call.args[0])
    raise AssertionError(f"No add_response_callback registration for {response_type!r}")


def fixture_dir(*, tier: FixtureTier = "synthetic") -> Path:
    """Return the directory for a fixture tier."""
    return SYNTHETIC_DIR if tier == "synthetic" else RECORDED_DIR


def recorded_fixtures_available() -> bool:
    """True when local ``recorded/`` fixtures exist (login + initial sync)."""
    login_ok = (RECORDED_DIR / "login.json").is_file()
    sync_ok = (RECORDED_DIR / "sync_initial.json").is_file()
    return login_ok and sync_ok


def load_fixture(name: str, *, tier: FixtureTier = "synthetic") -> dict[str, Any]:
    """Load a JSON cassette from ``synthetic/`` (default) or ``recorded/``."""
    path = fixture_dir(tier=tier) / name
    return cast(dict[str, Any], json.loads(path.read_text()))


def load_recorded_meta() -> dict[str, Any]:
    """Load ``recorded/meta.json`` written by the recording script."""
    return load_fixture("meta.json", tier="recorded")


def max_timeline_message_ts_by_room(sync: dict[str, Any]) -> dict[str, int]:
    """Map joined room_id → newest ``origin_server_ts`` from timeline text messages."""
    result: dict[str, int] = {}
    join = sync.get("rooms", {}).get("join", {})
    if not isinstance(join, dict):
        return result
    for room_id, info in join.items():
        if not isinstance(info, dict):
            continue
        timeline = info.get("timeline", {})
        events = timeline.get("events", []) if isinstance(timeline, dict) else []
        timestamps: list[int] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("type") != "m.room.message":
                continue
            ts = event.get("origin_server_ts")
            if isinstance(ts, int):
                timestamps.append(ts)
        if timestamps:
            result[str(room_id)] = max(timestamps)
    return result


def sync_url_pattern(homeserver: str = HOMESERVER) -> Pattern[str]:
    """Regex matching Matrix ``/sync`` requests (nio adds query params)."""
    return re.compile(rf"^{re.escape(homeserver)}/_matrix/client/v3/sync(\?.*)?$")


def room_messages_url_pattern(
    room_id: str,
    *,
    homeserver: str = HOMESERVER,
) -> Pattern[str]:
    """Regex matching ``/rooms/{id}/messages`` backfill requests."""
    escaped_room = re.escape(room_id)
    return re.compile(
        rf"^{re.escape(homeserver)}/_matrix/client/v3/rooms/{escaped_room}/messages(\?.*)?$"
    )


def stub_sync(
    m: HttpMocker,
    payload: dict[str, Any],
    *,
    homeserver: str = HOMESERVER,
    repeat: bool = False,
) -> None:
    """Stub a ``GET /sync`` response (matches query-string variants)."""
    stub_get(m, sync_url_pattern(homeserver), payload=payload, repeat=repeat)


def sort_rooms_by_recency(rooms: list[RoomSummary]) -> list[RoomSummary]:
    """Mirror ``RoomList`` recent sort: newest activity first, then A-Z by name."""
    rooms_with_dt = [r for r in rooms if r.last_activity is not None]
    rooms_without_dt = [r for r in rooms if r.last_activity is None]

    def _by_activity(r: RoomSummary) -> datetime:
        assert r.last_activity is not None
        return r.last_activity

    rooms_with_dt.sort(key=_by_activity, reverse=True)
    rooms_without_dt.sort(key=lambda r: r.display_name)
    return rooms_with_dt + rooms_without_dt


async def wait_until(
    predicate: Callable[[], bool],
    *,
    max_wait: float = 2.0,
    interval: float = 0.02,
) -> None:
    """Poll ``predicate`` until it returns True or ``max_wait`` elapses."""
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("wait_until: condition not met before timeout")


def room_activity_by_id(rooms: list[RoomSummary]) -> dict[str, datetime | None]:
    """Map room_id → last_activity from ``rooms()`` summaries."""
    return {r.room_id: r.last_activity for r in rooms}


def ts_from_origin_server_ms(ms: int) -> datetime:
    """Convert Matrix ``origin_server_ts`` milliseconds to UTC ``datetime``."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


async def start_sync_with_stubs(
    client: MatrixClient,
    m: HttpMocker,
    *,
    initial_sync: dict[str, Any],
    follow_up_sync: dict[str, Any] | None = None,
    min_rooms: int = 1,
    close_client: bool = True,
    homeserver: str = HOMESERVER,
) -> None:
    """Drive ``start_sync()`` with stubbed sync responses; optionally ``close()``."""
    stub_sync(m, initial_sync, homeserver=homeserver)
    idle = follow_up_sync or {
        "next_batch": "idle",
        "rooms": {"join": {}, "invite": {}, "leave": {}},
    }
    stub_sync(m, idle, homeserver=homeserver, repeat=True)
    await client.start_sync()
    await wait_until(lambda: len(client.rooms()) >= min_rooms)
    await asyncio.sleep(0)
    if close_client:
        await client.close()
