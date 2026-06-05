"""Partial inline stubs for matrix-nio — covers only the API used by client.py.

Keep in sync when upgrading matrix-nio (see AGENTS.md).
"""

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

_E = TypeVar("_E", bound="Event")
_R = TypeVar("_R", bound="Response")

# ---------------------------------------------------------------------------
# Config & client
# ---------------------------------------------------------------------------

class AsyncClientConfig:
    def __init__(
        self,
        *,
        store_sync_tokens: bool = ...,
        encryption_enabled: bool = ...,
        store_type: type[Any] | None = ...,
    ) -> None: ...

class OlmAccount:
    identity_keys: dict[str, str]

class Olm:
    account: OlmAccount

class AsyncClient:
    rooms: dict[str, MatrixRoom]
    user: str
    user_id: str
    access_token: str
    homeserver: str
    olm: Olm | None
    client_session: Any

    @property
    def should_upload_keys(self) -> bool: ...
    @property
    def should_query_keys(self) -> bool: ...
    def __init__(
        self,
        homeserver: str,
        user: str = ...,
        device_id: str = ...,
        store_path: str = ...,
        config: AsyncClientConfig | None = ...,
    ) -> None: ...
    async def login(
        self,
        password: str | None = ...,
        device_name: str = ...,
        token: str | None = ...,
    ) -> LoginResponse | LoginError: ...
    def restore_login(self, user_id: str, device_id: str, access_token: str) -> None: ...
    async def logout(self) -> LogoutResponse | ErrorResponse: ...
    async def sync(
        self,
        timeout: int = ...,
        full_state: bool = ...,
        since: str | None = ...,
    ) -> SyncResponse | SyncError: ...
    async def sync_forever(
        self, timeout: int = ..., full_state: bool = ..., loop_sleep_time: int | None = ...
    ) -> None: ...
    async def room_messages(
        self,
        room_id: str,
        start: str = ...,
        end: str | None = ...,
        limit: int = ...,
        message_filter: dict[str, Any] | None = ...,
    ) -> RoomMessagesResponse | ErrorResponse: ...
    async def room_send(
        self,
        room_id: str,
        message_type: str,
        content: dict[str, Any],
        tx_id: str | None = ...,
        ignore_unverified_devices: bool = ...,
    ) -> RoomSendResponse | RoomSendError: ...
    async def room_redact(
        self,
        room_id: str,
        event_id: str,
        reason: str = ...,
        tx_id: str | None = ...,
    ) -> RoomRedactResponse | ErrorResponse: ...
    async def room_leave(self, room_id: str) -> RoomLeaveResponse | RoomLeaveError: ...
    async def keys_query(self) -> KeysQueryResponse | KeysQueryError: ...
    async def keys_upload(self) -> KeysUploadResponse | KeysUploadError: ...
    def load_store(self) -> None: ...
    async def close(self) -> None: ...
    async def mxc_to_http(self, mxc: str, homeserver: str | None = ...) -> str | None: ...
    def room_devices(self, room_id: str) -> dict[str, dict[str, OlmDevice]]: ...
    def verify_device(self, device: OlmDevice) -> bool: ...
    async def request_room_key(
        self,
        event: MegolmEvent,
        tx_id: str | None = ...,
    ) -> RoomKeyRequestResponse | RoomKeyRequestError: ...
    def add_event_callback(
        self,
        callback: Callable[[MatrixRoom, _E], Awaitable[None] | None],
        cb_filter: type[_E] | tuple[type[_E], ...] | None = ...,
    ) -> None: ...
    def add_response_callback(
        self,
        callback: Callable[[_R], Awaitable[None] | None],
        cb_filter: type[_R] | None = ...,
    ) -> None: ...

# ---------------------------------------------------------------------------
# Rooms & users
# ---------------------------------------------------------------------------

class MatrixRoom:
    room_id: str
    display_name: str
    encrypted: bool
    users: dict[str, MatrixUser]
    power_levels: PowerLevelsEvent
    tags: dict[str, dict[str, float] | None]
    unread_notifications: int
    unread_highlights: int

    def __init__(
        self,
        room_id: str,
        own_user_id: str,
        encrypted: bool = False,
    ) -> None: ...

class MatrixUser:
    user_id: str
    display_name: str | None
    name: str

class OlmDevice: ...

# ---------------------------------------------------------------------------
# Sync response tree
# ---------------------------------------------------------------------------

class Timeline:
    events: list[Event]
    limited: bool
    prev_batch: str

class RoomInfo:
    timeline: Timeline

class Rooms:
    join: dict[str, RoomInfo]
    invite: dict[str, Any]
    leave: dict[str, Any]

class SyncResponse(Response):
    next_batch: str
    rooms: Rooms

# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class Event:
    event_id: str
    sender: str
    server_timestamp: int
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

# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

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
class RoomSendError(ErrorResponse): ...
class RoomLeaveError(ErrorResponse): ...
class KeysQueryError(ErrorResponse): ...
class KeysUploadError(ErrorResponse): ...
class RoomKeyRequestError(ErrorResponse): ...

class RoomMessagesResponse(Response):
    chunk: list[Event]
    start: str
    end: str | None

class RoomSendResponse(Response):
    event_id: str

class RoomRedactResponse(Response):
    event_id: str

class RoomLeaveResponse(Response): ...
class LogoutResponse(Response): ...
class KeysQueryResponse(Response): ...
class KeysUploadResponse(Response): ...
class RoomKeyRequestResponse(Response): ...

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

class PowerLevelsEvent:
    users: dict[str, int]

class LocalProtocolError(Exception): ...
