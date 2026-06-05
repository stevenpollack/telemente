# 0015 — Partial nio Type Stubs

**Status: done**

## Goal

Write inline partial stubs for `matrix-nio` covering exactly the API surface
`client.py` uses.  Both mypy (`--strict`) and Pyright must validate every nio
attribute access in `src/telemente/matrix/client.py` without a single
`# type: ignore` for nio types.  Bugs like accessing `MatrixRoom.timeline`
(which doesn't exist on that class) must become a type error at write-time.

## Motivation

`matrix-nio` ships no `py.typed` marker and no stubs.  The `[[tool.mypy.overrides]]
ignore_missing_imports = true` entry in `pyproject.toml` silences all nio
diagnostics.  That made it possible for `_newest_timestamp_from_nio_room` to
silently access `room.timeline.events` on a `nio.MatrixRoom` — a field that
only exists on `nio.RoomInfo` (from a sync response).  The function always
returned `None`, the bug was invisible to the type system, and the unit tests
used `SimpleNamespace` mocks that never exercised the real class.

## Dependencies

None — self-contained tooling change.

## Files to create / modify

```
stubs/
  nio/
    __init__.pyi          # all stubs in one file (nio re-exports everything)
pyproject.toml            # point mypy + pyright at stubs/; remove ignore_missing_imports
AGENTS.md                 # note that stubs must be kept in sync with nio upgrades
```

No changes to `src/` or `tests/` unless the stubs expose real type errors that
need fixing.

## Stub scope — exactly the nio surface used by client.py

Derived from `python3 -c "import ast ..."` audit of `client.py`:

### Classes to stub

**`AsyncClientConfig`**
```python
class AsyncClientConfig:
    def __init__(self, *, store_type: type[Any] | None = ...) -> None: ...
```

**`AsyncClient`**
```python
class AsyncClient:
    rooms: dict[str, MatrixRoom]
    user_id: str | None
    homeserver: str

    def __init__(
        self,
        homeserver: str,
        user: str = ...,
        device_id: str = ...,
        store_path: str = ...,
        config: AsyncClientConfig | None = ...,
    ) -> None: ...

    async def login(self, password: str, device_name: str = ...) -> LoginResponse | LoginError: ...
    async def login_with_token(self, token: str, device_name: str = ...) -> LoginResponse | LoginError: ...
    async def restore_login(self, user_id: str, device_id: str, access_token: str) -> None: ...
    async def logout(self) -> LogoutResponse | ErrorResponse: ...
    async def sync(self, timeout: int = ..., full_state: bool = ..., since: str | None = ...) -> SyncResponse | SyncError: ...
    async def sync_forever(self, timeout: int = ..., full_state: bool = ...) -> None: ...
    async def room_messages(self, room_id: str, start: str = ..., end: str | None = ..., limit: int = ..., message_filter: dict[str, Any] | None = ...) -> RoomMessagesResponse | ErrorResponse: ...
    async def room_send(self, room_id: str, message_type: str, content: dict[str, Any], tx_id: str | None = ...) -> RoomSendResponse | ErrorResponse: ...
    async def room_redact(self, room_id: str, event_id: str, reason: str = ..., tx_id: str | None = ...) -> RoomRedactResponse | ErrorResponse: ...
    async def keys_query(self, user_set: set[str]) -> KeysQueryResponse | ErrorResponse: ...
    async def load_store(self) -> None: ...
    async def close(self) -> None: ...
    def mxc_to_http(self, mxc: str) -> str: ...
    def add_event_callback(
        self,
        callback: Callable[[MatrixRoom, Event], Awaitable[None] | None],
        cb_filter: type[Event] | tuple[type[Event], ...] | None = ...,
    ) -> None: ...
    def add_response_callback(
        self,
        callback: Callable[[Response], Awaitable[None] | None],
        cb_filter: type[Response] | None = ...,
    ) -> None: ...
```

**`MatrixRoom`** — the in-memory room object; no `timeline` attribute.
```python
class MatrixRoom:
    room_id: str
    display_name: str
    encrypted: bool
    users: dict[str, MatrixUser]
    power_levels: PowerLevelsEvent   # re-used as a state container by nio
    tags: dict[str, dict[str, float]]  # tag name -> {"order": float}
    # NOTE: no `timeline` attribute — that lives on RoomInfo (sync response)
```

**`MatrixUser`**
```python
class MatrixUser:
    user_id: str
    display_name: str | None
    name: str
```

**Sync response tree**
```python
class Timeline:
    events: list[Event]
    limited: bool
    prev_batch: str

class RoomInfo:
    timeline: Timeline
    state: StateInfo
    account_data: AccountDataInfo

class Rooms:
    join: dict[str, RoomInfo]
    invite: dict[str, Any]
    leave: dict[str, Any]

class SyncResponse(Response):
    next_batch: str
    rooms: Rooms
```

**Event base + concrete types**
```python
class Event:
    event_id: str
    sender: str
    server_timestamp: int           # milliseconds since epoch
    source: dict[str, Any]

class RoomMessage(Event):
    body: str

class RoomMessageText(RoomMessage): ...

class RoomMessageMedia(RoomMessage):
    url: str

class RoomMessageImage(RoomMessageMedia): ...
class RoomMessageVideo(RoomMessageMedia): ...
class RoomMessageAudio(RoomMessageMedia): ...
class RoomMessageFile(RoomMessageMedia): ...

class ReactionEvent(Event):
    reacts_to: str
    key: str

class MegolmEvent(Event):
    session_id: str

class UnknownEncryptedEvent(Event): ...
```

**Response types**
```python
class Response: ...
class ErrorResponse(Response):
    message: str
    status_code: str

class LoginResponse(Response):
    user_id: str
    device_id: str
    access_token: str

class LoginError(ErrorResponse): ...
class SyncError(ErrorResponse): ...

class RoomMessagesResponse(Response):
    chunk: list[Event]
    start: str
    end: str | None

class RoomSendResponse(Response):
    event_id: str

class RoomRedactResponse(Response):
    event_id: str

class LogoutResponse(Response): ...
class KeysQueryResponse(Response): ...
```

**Misc**
```python
class PowerLevelsEvent:
    users: dict[str, int]

class LocalProtocolError(Exception): ...
```

## pyproject.toml changes

```toml
# Remove the ignore_missing_imports override for nio:
# [[tool.mypy.overrides]]
# module = ["nio.*"]
# ignore_missing_imports = true

# Add stubs path:
[tool.mypy]
mypy_path = "stubs"

# Add to aioresponses override if still needed:
[[tool.mypy.overrides]]
module = ["aioresponses.*"]
ignore_missing_imports = true
```

For Pyright, add to `pyproject.toml`:
```toml
[tool.pyright]
stubPath = "stubs"
```

## Test cases

No new test files needed.  The stubs are validated by the existing
`uv run mypy` and Pyright pass on `src/`.  If any real type errors are
exposed (wrong attribute access, wrong return type assumption), fix them
in `client.py` — do not suppress.

Expected errors that may surface and their fixes:

| Location | Error | Fix |
|----------|-------|-----|
| `client.py:_update_last_activity` | `RoomInfo.timeline` is `Timeline` (not a plain object) | no change needed — already correct |
| Any `getattr(room, "timeline", None)` remnant | access is now typed — remove getattr | remove |
| `isinstance(tag_data, dict)` | Pyright: unnecessary isinstance on `dict[str, float]` | remove the isinstance guard; the type already says dict |

## Done-when

- [x] `uv run mypy` passes with 0 errors and the `nio.*` `ignore_missing_imports`
  override removed.
- [x] Pyright reports 0 `reportArgumentType` / `reportAttributeAccessIssue` errors
  in `client.py`.
- [x] `uv run pytest -n auto` still passes.

## Maintenance note (add to AGENTS.md)

When upgrading `matrix-nio`, run `python -c "import nio; help(nio.MatrixRoom)"` and
`python -c "import nio; help(nio.SyncResponse)"` to check for API changes, then
update `stubs/nio/__init__.pyi` accordingly.
