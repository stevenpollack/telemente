"""MatrixClient: the single async boundary between telemente and matrix-nio.

Plan 0003: matrix client wrapper.
Plan 0010: end-to-end encryption (E2EE) with TOFU trust.

The UI NEVER imports nio. All protocol access goes through this module.
Only telemente.matrix.models dataclasses cross the boundary — no nio types.

# TOFU Trust Policy (v0.1.0)
# ============================
# telemente uses Trust-On-First-Use (TOFU): all devices encountered in an
# encrypted room are automatically marked as verified before each send.
# This is NOT secure against man-in-the-middle (MITM) attacks — a malicious
# server could inject a new device between participants and this client would
# silently trust it. Interactive device verification (SAS / QR code) is a
# future milestone. Users who require MITM protection should not rely on this
# version for sensitive communications.
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
        logger.debug(
            "MatrixClient.__init__: homeserver=%s store_path=%s device_name=%s",
            homeserver,
            store_path,
            device_name,
        )
        self._homeserver = homeserver
        self._device_name = device_name
        self._store_path = store_path
        self._handlers: list[EventHandler] = []
        self._task: asyncio.Task[None] | None = None
        self._logged_in: bool = False

        if nio_client is not None:
            self._client = nio_client
        else:
            # Enable encryption + store persistence when a store path is given.
            encryption_enabled = store_path is not None
            config = nio.AsyncClientConfig(
                store_sync_tokens=True,
                encryption_enabled=encryption_enabled,
            )
            self._client = nio.AsyncClient(
                homeserver,
                config=config,
                store_path=store_path or "",
            )

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def login(self, user: str, password: str) -> Session:
        """Login to the homeserver with user/password credentials.

        Returns a Session on success; raises LoginError on failure.
        """
        logger.info(
            "login: attempting password login for user=%s homeserver=%s",
            user,
            self._homeserver,
        )
        response = await self._client.login(password, device_name=self._device_name)
        if isinstance(response, nio.LoginError):
            raise LoginError(str(response))

        session = Session(
            homeserver=self._homeserver,
            user_id=response.user_id,
            device_id=response.device_id,
            access_token=response.access_token,
        )
        logger.info(
            "_finalize_login: session created user_id=%s device_id=%s",
            session.user_id,
            session.device_id,
        )
        self._logged_in = True
        self._load_store()
        self._register_callbacks()
        return session

    async def restore(self, session: Session) -> None:
        """Restore a previously saved session without re-authenticating."""
        logger.info(
            "restore: restoring session for user_id=%s homeserver=%s",
            session.user_id,
            self._homeserver,
        )
        self._client.restore_login(
            user_id=session.user_id,
            device_id=session.device_id,
            access_token=session.access_token,
        )
        self._logged_in = True
        self._load_store()
        self._register_callbacks()

    async def logout(self) -> None:
        """Logout from the homeserver and close the client."""
        logger.info("logout: logging out")
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
            logger.info("start_sync: already running, skipping")
            return
        logger.info("start_sync: launching sync loop")
        logger.info("Starting sync_forever (incremental)")
        self._task = asyncio.create_task(self._client.sync_forever(timeout=30000, full_state=True))

    async def close(self) -> None:
        """Cancel the sync task and close the nio client."""
        logger.info("close: cancelling sync task")
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
        logger.info("messages: fetching up to %d messages for room=%s", limit, room_id)
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
        logger.info("messages: returning %d messages for room=%s", len(result), room_id)
        return result

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def send_text(self, room_id: str, body: str) -> None:
        """Send a plain-text message to a room.

        For encrypted rooms, TOFU trust is applied first (all devices in the
        room are marked as verified), then the message is sent with
        ``ignore_unverified_devices=True``.

        WARNING — TOFU is NOT MITM-safe (see module docstring).
        """
        logger.info("send_text: room=%s reply_to=%s", room_id, None)
        logger.debug("send_text: body preview room=%s body=%.60r", room_id, body)
        if not self._logged_in:
            raise NotLoggedInError("Must be logged in to send messages")

        room = self._client.rooms.get(room_id)
        if room is not None and room.encrypted:
            self._tofu_trust_room(room_id)
            await self._client.room_send(
                room_id,
                "m.room.message",
                {"msgtype": "m.text", "body": body},
                ignore_unverified_devices=True,
            )
        else:
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

    def _load_store(self) -> None:
        """Load the nio/olm store if a store path is configured.

        Must be called after the client has a user_id and device_id
        (i.e. after login or restore_login). Safe to call when encryption
        is not configured — nio.AsyncClient.load_store() raises LocalProtocolError
        if the store was not initialised; we suppress that here.
        """
        if self._store_path is None:
            return
        try:
            self._client.load_store()
            logger.debug("Olm store loaded for %s", self._client.user_id)
        except Exception as exc:
            logger.warning("load_store() failed (encryption may be unavailable): %s", exc)

    def _tofu_trust_room(self, room_id: str) -> None:
        """Mark all devices in the room as verified (TOFU policy).

        WARNING: This is Trust-On-First-Use and is NOT secure against
        man-in-the-middle attacks. A compromised or malicious server can inject
        new devices. Interactive device verification (SAS/QR) is a future
        milestone (plan 0011+).
        """
        try:
            devices_by_user = self._client.room_devices(room_id)
        except Exception as exc:
            logger.debug("room_devices() unavailable for %s: %s", room_id, exc)
            return
        for user_devices in devices_by_user.values():
            for device in user_devices.values():
                try:
                    self._client.verify_device(device)
                except Exception as exc:
                    logger.warning("verify_device failed for %s: %s", device, exc)

    def _register_callbacks(self) -> None:
        self._client.add_event_callback(self._on_room_message, nio.RoomMessageText)
        self._client.add_event_callback(self._on_megolm_event, nio.MegolmEvent)
        self._client.add_response_callback(self._on_sync, nio.SyncResponse)

    async def _emit(self, event: ClientEvent) -> None:
        """Deliver an event to all subscribed handlers."""
        logger.debug("_emit: %s to %d handlers", type(event).__name__, len(self._handlers))
        for handler in list(self._handlers):
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result

    async def _on_room_message(self, room: nio.MatrixRoom, event: nio.RoomMessageText) -> None:
        """nio callback: a new text message arrived."""
        logger.debug(
            "_on_room_message: room=%s sender=%s event_id=%s",
            room.room_id,
            event.sender,
            event.event_id,
        )
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

    async def _on_megolm_event(self, room: nio.MatrixRoom, event: nio.MegolmEvent) -> None:
        """nio callback: an encrypted message that could not be decrypted.

        Requests the room key from the sender and surfaces a placeholder
        message so the user knows something arrived.
        """
        logger.warning(
            "Undecryptable MegolmEvent in %s from %s (session %s) — requesting key",
            room.room_id,
            event.sender,
            event.session_id,
        )
        try:
            await self._client.request_room_key(event)
        except Exception as exc:
            logger.warning("request_room_key failed for %s: %s", event.session_id, exc)

        ts = datetime.fromtimestamp(event.server_timestamp / 1000, tz=UTC)
        placeholder = Message(
            event_id=event.event_id,
            room_id=room.room_id,
            sender=event.sender,
            sender_display_name=_get_display_name(room, event.sender),
            body="\U0001f512 unable to decrypt",
            timestamp=ts,
        )
        await self._emit(NewMessage(message=placeholder))

    async def _on_sync(self, response: nio.SyncResponse) -> None:
        """nio callback: a sync response arrived — emit RoomsChanged and handle key ops."""
        rooms = self.rooms()
        logger.debug("_on_sync: %d rooms after sync", len(rooms))

        # Upload device keys if needed (first sync after login/account creation).
        if self._client.should_upload_keys:
            logger.info("Uploading device keys")
            try:
                await self._client.keys_upload()
            except Exception as exc:
                logger.error("keys_upload() failed: %s", exc)

        # Query keys for users whose device lists have changed.
        if self._client.should_query_keys:
            logger.info("Querying device keys")
            try:
                await self._client.keys_query()
            except Exception as exc:
                logger.warning("keys_query() failed: %s", exc)

        await self._emit(RoomsChanged(rooms=rooms))


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
