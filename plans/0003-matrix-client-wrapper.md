# 0003 — Matrix Client Wrapper

## Goal

Provide `MatrixClient`, the **single** async boundary between telemente and
matrix-nio. It exposes login/session restore, a background sync loop, room /
message / member accessors, message sending, and an event-subscription hook —
all in terms of plain dataclasses (`matrix/models.py`), never raw nio types.
This is what makes the whole UI testable without a homeserver.

## Dependencies

- 0002 (config & credentials: `Paths`, `Session`, `CredentialStore`).

## Files to create / modify

- `src/telemente/matrix/models.py` — new (dataclasses).
- `src/telemente/matrix/client.py` — new (`MatrixClient`, errors).
- `tests/matrix/test_models.py` — new.
- `tests/matrix/test_client.py` — new (unit + aioresponses integration).
- `tests/fakes.py` — new (`FakeMatrixClient` + sample payload builders) — used
  here and by all UI plans.

## Public interface

```python
# src/telemente/matrix/models.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime

@dataclass(frozen=True, slots=True)
class RoomSummary:
    room_id: str
    display_name: str
    unread_count: int = 0
    last_activity: datetime | None = None
    encrypted: bool = False

@dataclass(frozen=True, slots=True)
class Message:
    event_id: str
    room_id: str
    sender: str
    sender_display_name: str
    body: str
    timestamp: datetime

@dataclass(frozen=True, slots=True)
class Member:
    user_id: str
    display_name: str
    power_level: int = 0
```

```python
# src/telemente/matrix/client.py
from __future__ import annotations
from collections.abc import Awaitable, Callable

class MatrixError(Exception): ...
class LoginError(MatrixError): ...        # bad credentials / homeserver
class NotLoggedInError(MatrixError): ...

# Events handed to subscribers (UI converts these to Textual messages in 0009).
# Use a small union of dataclasses, e.g.:
@dataclass(frozen=True, slots=True)
class RoomsChanged: rooms: list[RoomSummary]
@dataclass(frozen=True, slots=True)
class NewMessage: message: Message
@dataclass(frozen=True, slots=True)
class MembersChanged: room_id: str; members: list[Member]

ClientEvent = RoomsChanged | NewMessage | MembersChanged
EventHandler = Callable[[ClientEvent], Awaitable[None] | None]

class MatrixClient:
    def __init__(
        self,
        homeserver: str,
        *,
        store_path: str | None = None,   # 0010 passes Paths.store_dir
        device_name: str = "telemente",
        nio_client: "AsyncClient | None" = None,  # DI seam for tests
    ) -> None: ...

    # --- auth ---
    async def login(self, user: str, password: str) -> Session: ...
    async def restore(self, session: Session) -> None: ...
    async def logout(self) -> None: ...

    # --- lifecycle ---
    async def start_sync(self) -> None: ...   # launches sync_forever as a task
    async def close(self) -> None: ...        # cancel sync + close client

    # --- queries (from current sync state) ---
    def rooms(self) -> list[RoomSummary]: ...
    def members(self, room_id: str) -> list[Member]: ...
    async def messages(self, room_id: str, limit: int = 50) -> list[Message]: ...

    # --- actions ---
    async def send_text(self, room_id: str, body: str) -> None: ...

    # --- events ---
    def subscribe(self, handler: EventHandler) -> Callable[[], None]: ...
    # returns an unsubscribe callable
```

## Behavior

- **Construction**: if `nio_client` is provided, use it (tests inject a
  mock/fake); otherwise build `nio.AsyncClient(homeserver, store_path=...,
  config=AsyncClientConfig(store_sync_tokens=True))`. Do **not** enable e2e
  store here unless `store_path` is set — keep 0003 working without libolm; the
  encryption wiring is layered in 0010.
- **login**: call `nio` `login(user, password, device_name=...)`. On
  `LoginResponse`, build and return a `Session`; on `LoginError`/non-200 raise
  `LoginError`. Register nio callbacks (see below) after a successful login.
- **restore**: set `client.access_token/user_id/device_id` from the `Session`
  (and `restore_login`); register callbacks.
- **start_sync**: `self._task = asyncio.create_task(self.client.sync_forever(
  timeout=30000, full_state=True))`. Idempotent (no-op if already running).
  Requires being logged in else raise `NotLoggedInError`.
- **close**: cancel the sync task (await its cancellation), then
  `await client.close()`.
- **rooms()/members()**: read from `client.rooms` (nio in-memory state) and map
  to dataclasses. `display_name` via `room.display_name` (fallback to
  `room_id`). `encrypted` via `room.encrypted`. `members()` maps
  `room.users` → `Member`, power level from `room.power_levels`.
- **messages()**: use `client.room_messages(room_id, start=..., limit=...)` for
  backfill, mapping `RoomMessageText` events → `Message`. Ignore non-text events
  for v0.1.0 (filter them out).
- **send_text**: `await client.room_send(room_id, "m.room.message",
  {"msgtype": "m.text", "body": body})`. Requires login.
- **callbacks → events**: register nio callbacks:
  - `client.add_event_callback(self._on_room_message, RoomMessageText)` →
    emit `NewMessage`.
  - `client.add_response_callback(self._on_sync, SyncResponse)` → emit
    `RoomsChanged` (recompute summaries) and, for rooms with membership deltas,
    `MembersChanged`.
  Each `_on_*` builds the dataclass and calls every subscribed handler
  (awaiting coroutine handlers; calling sync ones directly).
- **subscribe**: append handler to a list; return a closure that removes it.
- **Typing**: nio is untyped → `mypy` overrides ignore it (already configured).
  Keep nio objects local to `client.py`; everything returned is a dataclass.

## Test cases (write first)

### `tests/matrix/test_models.py`
1. Construct each dataclass; assert frozen (assignment raises
   `FrozenInstanceError`) and field defaults.

### `tests/matrix/test_client.py` — unit (injected mock nio client)
2. `test_login_success` — inject an `AsyncMock` nio client whose `login`
   returns a fake `LoginResponse` (`access_token`, `device_id`, `user_id`);
   `await client.login("u","p")` returns a `Session` with those values.
3. `test_login_failure_raises` — nio `login` returns a `LoginError`-like object
   → `MatrixClient.login` raises `telemente` `LoginError`.
4. `test_send_text_calls_room_send` — `await client.send_text("!r:s","hi")`
   asserts `nio.room_send` awaited once with msgtype `m.text`, body `hi`.
5. `test_send_text_requires_login` — fresh client (no token) → `send_text`
   raises `NotLoggedInError`.
6. `test_rooms_maps_state` — give the fake nio client a `rooms` dict with one
   encrypted room; `client.rooms()` returns one `RoomSummary` with
   `encrypted=True` and the right display name.
7. `test_subscribe_receives_new_message` — register a handler; manually invoke
   `client._on_room_message(room, event)` with a fake `RoomMessageText`; assert
   the handler received a `NewMessage` whose `Message.body` matches.
8. `test_unsubscribe` — unsubscribe closure removes the handler; subsequent
   events are not delivered.
9. `test_start_sync_requires_login` — `start_sync` before login raises
   `NotLoggedInError`. After a fake login, `start_sync` creates a task; `close`
   cancels it cleanly (no pending-task warnings).

### `tests/matrix/test_client.py` — integration (aioresponses)
10. `test_login_integration_aioresponses` — with `aioresponses`, stub
    `POST .../login` returning a real Matrix login JSON; build a **real**
    `nio.AsyncClient`; `await client.login(...)` parses token/device/user.
11. `test_login_forbidden_integration` — stub `/login` → 403 `M_FORBIDDEN`;
    assert `LoginError` raised.

## Mocking strategy

- **Unit**: inject `nio_client=AsyncMock(spec=AsyncClient)`; set `.rooms`,
  configure return values for `login`/`room_send`/`room_messages`. Build fake
  nio event objects with `types.SimpleNamespace` or lightweight stand-ins (only
  the attributes the mapper reads). Put builders in `tests/fakes.py`:
  `make_login_response(...)`, `make_room(...)`, `make_text_event(...)`.
- **Integration**: `from aioresponses import aioresponses`; match the homeserver
  base URL; assert nio parses correctly. Keep these to login/error paths.
- **`FakeMatrixClient`** (in `tests/fakes.py`): a hand-written object
  implementing the same public surface as `MatrixClient` (login/rooms/members/
  messages/send_text/subscribe/start_sync/close) backed by in-memory data and a
  way to **push** scripted events to subscribers (`fake.emit(NewMessage(...))`).
  UI plans (0004–0009) depend on this — implement it here.

## Done-when

- [ ] All tests pass (unit + 2 aioresponses integration tests).
- [ ] No `nio` import anywhere outside `src/telemente/matrix/`.
- [ ] `MatrixClient` returns only `models.py` dataclasses.
- [ ] `tests/fakes.py::FakeMatrixClient` exists and can emit scripted events.
- [ ] `mypy --strict` + `ruff` clean; works **without** libolm (no store_path).
