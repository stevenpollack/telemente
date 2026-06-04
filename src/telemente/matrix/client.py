"""MatrixClient: the single async boundary between telemente and matrix-nio.

Plan 0003: matrix client wrapper.
Plan 0010: end-to-end encryption (E2EE) with TOFU trust.
Plan 0011: SSO login (login_flows, sso_redirect_url, login_with_token).

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
from telemente.matrix.auth import LoginFlows, build_sso_redirect_url, parse_login_flows
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
        self._rooms_poll_task: asyncio.Task[None] | None = None
        self._logged_in: bool = False
        self._initial_sync_done: bool = False

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

    @property
    def homeserver(self) -> str:
        """The homeserver URL this client is configured for."""
        return self._homeserver

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def login_flows(self) -> LoginFlows:
        """Fetch and parse the login flows advertised by the homeserver.

        Issues ``GET /_matrix/client/v3/login`` and returns a ``LoginFlows``
        dataclass. Raises ``LoginError`` on transport or HTTP error.
        """
        import aiohttp

        url = f"{self._homeserver}/_matrix/client/v3/login"
        try:
            async with (
                aiohttp.ClientSession() as http_session,
                http_session.get(url) as resp,
            ):
                if resp.status != 200:
                    raise LoginError(f"login_flows HTTP {resp.status} from {url}")
                payload: dict[str, object] = await resp.json()
        except LoginError:
            raise
        except Exception as exc:
            raise LoginError(f"login_flows request failed: {exc}") from exc

        return parse_login_flows(payload)

    def sso_redirect_url(self, redirect_url: str, idp_id: str | None = None) -> str:
        """Build the SSO redirect URL for this homeserver.

        Delegates to ``build_sso_redirect_url`` from ``matrix.auth``.
        The ``redirect_url`` should be the loopback callback URL returned
        by ``SsoCallbackServer.start()``.
        """
        return build_sso_redirect_url(self._homeserver, redirect_url, idp_id)

    async def login_with_token(self, token: str) -> Session:
        """Exchange a single-use ``loginToken`` (from SSO) for a Session.

        SECURITY: the token is NEVER logged.

        Raises ``LoginError`` on failure.
        """
        response = await self._client.login(token=token, device_name=self._device_name)
        if isinstance(response, nio.LoginError):
            raise LoginError(str(response))
        return self._finalize_login(response)

    async def login(self, user: str, password: str) -> Session:
        """Login to the homeserver with user/password credentials.

        Returns a Session on success; raises LoginError on failure.

        Bug fix (plan 0011): sets ``self._client.user = user`` before the nio
        ``login()`` call so nio knows which user to authenticate.  Previously
        this was silently omitted, causing real password logins to fail.
        """
        # Fix: set the user on the nio client BEFORE calling login()
        self._client.user = user
        response = await self._client.login(password, device_name=self._device_name)
        if isinstance(response, nio.LoginError):
            raise LoginError(str(response))
        return self._finalize_login(response)

    def _finalize_login(self, response: object) -> Session:
        """Shared post-login bookkeeping: build Session, set state, load store.

        Called by both ``login()`` and ``login_with_token()``.
        """
        # response is expected to have user_id / device_id / access_token attrs
        session = Session(
            homeserver=self._homeserver,
            user_id=getattr(response, "user_id", ""),
            device_id=getattr(response, "device_id", ""),
            access_token=getattr(response, "access_token", ""),
        )
        self._logged_in = True
        self._load_store()
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
        self._load_store()
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

        Performs one full-state sync first (populates all rooms), then starts
        sync_forever for incremental updates. A background poll task emits
        RoomsChanged progressively while the initial sync is processing.
        """
        if not self._logged_in:
            raise NotLoggedInError("Must be logged in before starting sync")
        if self._task is not None and not self._task.done():
            return
        logger.info("Starting sync")
        self._task = asyncio.create_task(self._sync_loop())

    async def _sync_loop(self) -> None:
        """Run initial sync, then switch to incremental sync_forever.

        If the nio store already has rooms from a previous session, emit them
        immediately (zero network round-trip) and do an incremental sync.
        Otherwise do a full-state sync to populate rooms for the first time.
        """
        # Emit whatever the store already knows — instant on restart.
        cached_rooms = self.rooms()
        if cached_rooms:
            logger.info("Emitting %d cached rooms from store before sync", len(cached_rooms))
            self._initial_sync_done = True
            await self._emit(RoomsChanged(rooms=cached_rooms))

        self._rooms_poll_task = asyncio.create_task(self._poll_rooms_during_sync())
        try:
            full_state = not bool(cached_rooms)
            logger.debug("Initial sync (full_state=%s)...", full_state)
            resp = await self._client.sync(timeout=30000, full_state=full_state)
            if isinstance(resp, nio.SyncResponse):
                self._initial_sync_done = True
                logger.info("Initial sync complete: %d rooms", len(self._client.rooms))
                await self._emit(RoomsChanged(rooms=self.rooms()))
            else:
                logger.warning("Initial sync failed: %s", resp)
                self._initial_sync_done = True
        except Exception as exc:
            logger.error("Initial sync error: %s", exc)
            self._initial_sync_done = True

        # Now run incremental sync forever.
        logger.debug("Starting sync_forever (incremental)")
        await self._client.sync_forever(timeout=30000)

    async def close(self) -> None:
        """Cancel the sync task and close the nio client."""
        if self._rooms_poll_task is not None and not self._rooms_poll_task.done():
            self._rooms_poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._rooms_poll_task
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
            # Extract last_activity from the most recent timeline event.
            last_activity: datetime | None = None
            if room.timeline:
                last_event = room.timeline[-1]
                if hasattr(last_event, "server_timestamp"):
                    last_activity = datetime.fromtimestamp(
                        last_event.server_timestamp / 1000, tz=UTC
                    )

            # Extract room tags (m.favourite, m.lowpriority, etc.).
            tags: dict[str, float | None] = {}
            if hasattr(room, "tags") and room.tags:
                for tag_name, tag_data in room.tags.items():
                    order: float | None = None
                    if tag_data and isinstance(tag_data, dict):
                        raw_order = tag_data.get("order")
                        if isinstance(raw_order, (int, float)):
                            order = float(raw_order)
                    tags[str(tag_name)] = order

            summaries.append(
                RoomSummary(
                    room_id=room_id,
                    display_name=room.display_name or room_id,
                    encrypted=bool(room.encrypted),
                    last_activity=last_activity,
                    tags=tags,
                )
            )
        logger.debug(
            "rooms(): returning %d rooms (%d with last_activity)",
            len(summaries),
            sum(1 for s in summaries if s.last_activity is not None),
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

        Returns Message dataclasses. Undecryptable encrypted events are
        returned as placeholder messages so the UI can inform the user.
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
            if isinstance(event, nio.RoomMessageText):
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
            elif isinstance(event, nio.RoomMessageMedia):
                sender_display_name = _get_display_name(room, event.sender)
                ts = datetime.fromtimestamp(event.server_timestamp / 1000, tz=UTC)
                media_type = _media_type_label(event)
                http_url = await self._client.mxc_to_http(event.url)
                result.append(
                    Message(
                        event_id=event.event_id,
                        room_id=room_id,
                        sender=event.sender,
                        sender_display_name=sender_display_name,
                        body=event.body or media_type,
                        timestamp=ts,
                        media_url=http_url,
                        media_type=media_type,
                    )
                )
            elif isinstance(event, nio.MegolmEvent):
                sender_display_name = _get_display_name(room, event.sender)
                ts = datetime.fromtimestamp(event.server_timestamp / 1000, tz=UTC)
                result.append(
                    Message(
                        event_id=event.event_id,
                        room_id=room_id,
                        sender=event.sender,
                        sender_display_name=sender_display_name,
                        body="\U0001f512 Unable to decrypt",
                        timestamp=ts,
                    )
                )
        # Backfill returns newest-first; reverse to chronological order.
        result.reverse()
        return result

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def leave_room(self, room_id: str) -> None:
        """Leave a room.

        Calls nio's room_leave(); raises NotLoggedInError if not logged in and
        MatrixError on failure.
        """
        if not self._logged_in:
            raise NotLoggedInError("Must be logged in to leave a room")
        response = await self._client.room_leave(room_id)
        if isinstance(response, nio.ErrorResponse):
            raise MatrixError(f"leave_room failed for {room_id}: {response}")
        logger.info("Left room %s", room_id)

    async def set_room_tag(self, room_id: str, tag: str, order: float | None = None) -> None:
        """Add or update a room tag (e.g. m.favourite, m.lowpriority).

        Calls PUT /_matrix/client/v3/user/{userId}/rooms/{roomId}/tags/{tag}.
        Raises NotLoggedInError if not logged in; MatrixError on failure.
        """
        if not self._logged_in:
            raise NotLoggedInError("Must be logged in to set a room tag")
        # nio doesn't have a dedicated room_tag method; use raw HTTP.
        import aiohttp

        user_id = self._client.user_id or ""
        url = f"{self._homeserver}/_matrix/client/v3/user/{user_id}/rooms/{room_id}/tags/{tag}"
        payload: dict[str, float] = {}
        if order is not None:
            payload["order"] = order
        try:
            async with (
                aiohttp.ClientSession() as http_session,
                http_session.put(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._client.access_token}"},
                ) as resp,
            ):
                if resp.status not in (200, 204):
                    raise MatrixError(f"set_room_tag HTTP {resp.status} for tag {tag} in {room_id}")
        except MatrixError:
            raise
        except Exception as exc:
            raise MatrixError(f"set_room_tag request failed: {exc}") from exc
        logger.debug("Set tag %s on room %s", tag, room_id)

    async def remove_room_tag(self, room_id: str, tag: str) -> None:
        """Remove a room tag.

        Calls DELETE /_matrix/client/v3/user/{userId}/rooms/{roomId}/tags/{tag}.
        Raises NotLoggedInError if not logged in; MatrixError on failure.
        """
        if not self._logged_in:
            raise NotLoggedInError("Must be logged in to remove a room tag")
        user_id = self._client.user_id or ""
        url = f"{self._homeserver}/_matrix/client/v3/user/{user_id}/rooms/{room_id}/tags/{tag}"
        import aiohttp

        try:
            async with (
                aiohttp.ClientSession() as http_session,
                http_session.delete(
                    url,
                    headers={"Authorization": f"Bearer {self._client.access_token}"},
                ) as resp,
            ):
                if resp.status not in (200, 204):
                    raise MatrixError(
                        f"remove_room_tag HTTP {resp.status} for tag {tag} in {room_id}"
                    )
        except MatrixError:
            raise
        except Exception as exc:
            raise MatrixError(f"remove_room_tag request failed: {exc}") from exc
        logger.debug("Removed tag %s from room %s", tag, room_id)

    async def send_text(self, room_id: str, body: str) -> None:
        """Send a plain-text message to a room.

        For encrypted rooms, TOFU trust is applied first (all devices in the
        room are marked as verified), then the message is sent with
        ``ignore_unverified_devices=True``.

        WARNING — TOFU is NOT MITM-safe (see module docstring).
        """
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
                    logger.debug("verify_device failed for %s: %s", device, exc)

    def _register_callbacks(self) -> None:
        self._client.add_event_callback(self._on_room_message, nio.RoomMessageText)
        self._client.add_event_callback(self._on_room_media, nio.RoomMessageMedia)
        self._client.add_event_callback(self._on_megolm_event, nio.MegolmEvent)
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

    async def _on_room_media(self, room: nio.MatrixRoom, event: nio.RoomMessageMedia) -> None:
        """nio callback: a new media message (image/video/audio/file) arrived."""
        sender_display_name = _get_display_name(room, event.sender)
        ts = datetime.fromtimestamp(event.server_timestamp / 1000, tz=UTC)
        media_type = _media_type_label(event)
        http_url = await self._client.mxc_to_http(event.url)
        message = Message(
            event_id=event.event_id,
            room_id=room.room_id,
            sender=event.sender,
            sender_display_name=sender_display_name,
            body=event.body or media_type,
            timestamp=ts,
            media_url=http_url,
            media_type=media_type,
        )
        await self._emit(NewMessage(message=message))

    async def _on_megolm_event(self, room: nio.MatrixRoom, event: nio.MegolmEvent) -> None:
        """nio callback: an encrypted message that could not be decrypted.

        Requests the room key from the sender and surfaces a placeholder
        message so the user knows something arrived.
        """
        logger.debug(
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

    async def _poll_rooms_during_sync(self) -> None:
        """Periodically emit RoomsChanged while the initial sync is processing.

        nio processes events one-by-one during a large initial sync, populating
        self._client.rooms as it goes. The SyncResponse callback only fires
        AFTER all events are processed, which can take many seconds for large
        accounts. This task polls every 0.5s and emits RoomsChanged with
        whatever rooms have appeared so far, giving the UI progressive updates.

        Uses a lightweight room list (no timeline scanning) to avoid blocking
        the event loop during the sync.
        """
        last_count = 0
        while not self._initial_sync_done:
            await asyncio.sleep(0.5)
            current_count = len(self._client.rooms)
            if current_count > last_count:
                logger.debug(
                    "_poll_rooms_during_sync: %d rooms (was %d)",
                    current_count,
                    last_count,
                )
                last_count = current_count
                summaries = self._rooms_fast()
                await self._emit(RoomsChanged(rooms=summaries))

    def _rooms_fast(self) -> list[RoomSummary]:
        """Build room summaries without scanning timelines (for progress updates).

        Skips last_activity extraction which requires iterating timeline events.
        The full rooms() call will populate timestamps once _on_sync fires.
        """
        summaries: list[RoomSummary] = []
        for room_id, room in self._client.rooms.items():
            tags: dict[str, float | None] = {}
            if hasattr(room, "tags") and room.tags:
                for tag_name, tag_data in room.tags.items():
                    order: float | None = None
                    if tag_data and isinstance(tag_data, dict):
                        raw_order = tag_data.get("order")
                        if isinstance(raw_order, (int, float)):
                            order = float(raw_order)
                    tags[str(tag_name)] = order
            summaries.append(
                RoomSummary(
                    room_id=room_id,
                    display_name=room.display_name or room_id,
                    encrypted=bool(room.encrypted),
                    tags=tags,
                )
            )
        return summaries

    async def _on_sync(self, response: nio.SyncResponse) -> None:
        """nio callback: a sync response arrived — emit RoomsChanged and handle key ops."""
        self._initial_sync_done = True
        rooms = self.rooms()
        logger.debug("_on_sync: %d rooms after sync", len(rooms))

        # Upload device keys if needed (first sync after login/account creation).
        if self._client.should_upload_keys:
            logger.debug("Uploading device keys")
            try:
                await self._client.keys_upload()
            except Exception as exc:
                logger.warning("keys_upload() failed: %s", exc)

        # Query keys for users whose device lists have changed.
        if self._client.should_query_keys:
            logger.debug("Querying device keys")
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


def _media_type_label(event: nio.RoomMessageMedia) -> str:
    """Return a human-readable label for a media event type."""
    if isinstance(event, nio.RoomMessageImage):
        return "image"
    if isinstance(event, nio.RoomMessageVideo):
        return "video"
    if isinstance(event, nio.RoomMessageAudio):
        return "audio"
    return "file"
