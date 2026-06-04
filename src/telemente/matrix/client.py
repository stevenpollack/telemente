"""MatrixClient: the single async boundary between telemente and matrix-nio.

Plan 0003: matrix client wrapper.

The UI NEVER imports nio. All protocol access goes through this module.
Only telemente.matrix.models dataclasses cross the boundary — no nio types.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

# nio imports are ONLY in this module (matrix/ package)
import nio
import nio.responses

from telemente.config import Session
from telemente.matrix.models import Member, Message, RoomSummary

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MatrixError(Exception):
    """Base exception for Matrix client errors."""


class LoginError(MatrixError):
    """Raised when login fails (bad credentials, homeserver unreachable, etc.)."""


class NotLoggedInError(MatrixError):
    """Raised when an operation requires login but the client is not logged in."""


# ---------------------------------------------------------------------------
# Client events (delivered to subscribers)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoomsChanged:
    """The set of joined rooms has changed."""

    rooms: list[RoomSummary]


@dataclass(frozen=True, slots=True)
class NewMessage:
    """A new text message arrived in a room."""

    message: Message


@dataclass(frozen=True, slots=True)
class MembersChanged:
    """The membership of a room changed."""

    room_id: str
    members: list[Member]


ClientEvent = RoomsChanged | NewMessage | MembersChanged
EventHandler = Callable[[ClientEvent], "Awaitable[None] | None"]

# ---------------------------------------------------------------------------
# MatrixClient
# ---------------------------------------------------------------------------


class MatrixClient:
    """Async wrapper around matrix-nio's AsyncClient.

    The UI and tests interact only with this class; nio stays fully encapsulated.
    """

    def __init__(
        self,
        homeserver: str,
        *,
        store_path: str | None = None,
        device_name: str = "telemente",
        nio_client: nio.AsyncClient | None = None,
    ) -> None:
        self._homeserver = homeserver
        self._device_name = device_name
        self._store_path = store_path
        self._handlers: list[EventHandler] = []
        self._task: asyncio.Task[None] | None = None
        self._logged_in: bool = False

        if nio_client is not None:
            self._client = nio_client
        else:
            config = nio.AsyncClientConfig(store_sync_tokens=True)
            self._client = nio.AsyncClient(
                homeserver,
                config=config,
            )

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def login(self, user: str, password: str) -> Session:
        """Login to the homeserver with user/password credentials.

        Returns a Session on success; raises LoginError on failure.
        """
        response = await self._client.login(password, device_name=self._device_name)
        if isinstance(response, nio.LoginError):
            raise LoginError(str(response))

        session = Session(
            homeserver=self._homeserver,
            user_id=response.user_id,
            device_id=response.device_id,
            access_token=response.access_token,
        )
        self._logged_in = True
        self._register_callbacks()
        return session

    async def restore(self, session: Session) -> None:
        """Restore a previously saved session without re-authenticating."""
        self._client.restore_login(
            user_id=session.user_id,
            device_id=session.device_id,
            access_token=session.access_token,
        )
        self._logged_in = True
        self._register_callbacks()

    async def logout(self) -> None:
        """Logout from the homeserver and close the client."""
        await self.close()
        self._logged_in = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_sync(self) -> None:
        """Launch the sync loop as an asyncio task.

        Idempotent — no-op if already running. Raises NotLoggedInError if
        not yet logged in.
        """
        if not self._logged_in:
            raise NotLoggedInError("Must be logged in before starting sync")
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._client.sync_forever(timeout=30000, full_state=True))

    async def close(self) -> None:
        """Cancel the sync task and close the nio client."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        await self._client.close()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def rooms(self) -> list[RoomSummary]:
        """Return summaries of all joined rooms (from current sync state)."""
        summaries: list[RoomSummary] = []
        for room_id, room in self._client.rooms.items():
            summaries.append(
                RoomSummary(
                    room_id=room_id,
                    display_name=room.display_name or room_id,
                    encrypted=bool(room.encrypted),
                )
            )
        return summaries

    def members(self, room_id: str) -> list[Member]:
        """Return members of the given room (from current sync state)."""
        room = self._client.rooms.get(room_id)
        if room is None:
            return []
        result: list[Member] = []
        for user_id, user in room.users.items():
            power_level = room.power_levels.users.get(user_id, 0)
            result.append(
                Member(
                    user_id=user_id,
                    display_name=user.display_name or user.name or user_id,
                    power_level=int(power_level),
                )
            )
        return result

    async def messages(self, room_id: str, limit: int = 50) -> list[Message]:
        """Fetch recent messages for a room via backfill.

        Returns Message dataclasses; non-text events are ignored.
        """
        if not self._logged_in:
            raise NotLoggedInError("Must be logged in to fetch messages")
        response = await self._client.room_messages(room_id, limit=limit)
        if not isinstance(response, nio.RoomMessagesResponse):
            logger.warning("room_messages failed for %s: %s", room_id, response)
            return []

        result: list[Message] = []
        room = self._client.rooms.get(room_id)
        for event in response.chunk:
            if not isinstance(event, nio.RoomMessageText):
                continue
            sender_display_name = _get_display_name(room, event.sender)
            ts = datetime.fromtimestamp(event.server_timestamp / 1000, tz=UTC)
            result.append(
                Message(
                    event_id=event.event_id,
                    room_id=room_id,
                    sender=event.sender,
                    sender_display_name=sender_display_name,
                    body=event.body,
                    timestamp=ts,
                )
            )
        return result

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def send_text(self, room_id: str, body: str) -> None:
        """Send a plain-text message to a room."""
        if not self._logged_in:
            raise NotLoggedInError("Must be logged in to send messages")
        await self._client.room_send(
            room_id,
            "m.room.message",
            {"msgtype": "m.text", "body": body},
        )

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        """Register an event handler. Returns an unsubscribe callable."""
        self._handlers.append(handler)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._handlers.remove(handler)

        return _unsubscribe

    # ------------------------------------------------------------------
    # Internal: nio callbacks
    # ------------------------------------------------------------------

    def _register_callbacks(self) -> None:
        self._client.add_event_callback(self._on_room_message, nio.RoomMessageText)
        self._client.add_response_callback(self._on_sync, nio.SyncResponse)

    async def _emit(self, event: ClientEvent) -> None:
        """Deliver an event to all subscribed handlers."""
        for handler in list(self._handlers):
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result

    async def _on_room_message(self, room: nio.MatrixRoom, event: nio.RoomMessageText) -> None:
        """nio callback: a new text message arrived."""
        sender_display_name = _get_display_name(room, event.sender)
        ts = datetime.fromtimestamp(event.server_timestamp / 1000, tz=UTC)
        message = Message(
            event_id=event.event_id,
            room_id=room.room_id,
            sender=event.sender,
            sender_display_name=sender_display_name,
            body=event.body,
            timestamp=ts,
        )
        await self._emit(NewMessage(message=message))

    async def _on_sync(self, response: nio.SyncResponse) -> None:
        """nio callback: a sync response arrived — emit RoomsChanged."""
        await self._emit(RoomsChanged(rooms=self.rooms()))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_display_name(room: nio.MatrixRoom | None, user_id: str) -> str:
    """Return the display name for a user in a room, falling back to user_id."""
    if room is None:
        return user_id
    user = room.users.get(user_id)
    if user is None:
        return user_id
    return str(user.display_name or user.name or user_id)
