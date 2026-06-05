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
import dataclasses
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

# nio imports are ONLY in this module (matrix/ package)
import nio

from telemente.config import Session
from telemente.matrix.auth import LoginFlows, build_sso_redirect_url, parse_login_flows
from telemente.matrix.cache import MessageCache
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


@dataclass(frozen=True, slots=True)
class TypingChanged:
    """Someone in a room started or stopped typing."""

    room_id: str
    user_ids: list[str]


ClientEvent = RoomsChanged | NewMessage | MembersChanged | TypingChanged
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
        cache_path: str | None = None,
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
        self._cache_path = cache_path
        self._cache: MessageCache | None = MessageCache() if cache_path is not None else None
        self._handlers: list[EventHandler] = []
        self._task: asyncio.Task[None] | None = None
        self._rooms_poll_task: asyncio.Task[None] | None = None
        self._logged_in: bool = False
        self._initial_sync_done: bool = False
        # Cache of room_id → last event timestamp, updated on each sync.
        self._last_activity: dict[str, datetime] = {}
        # Rooms we have locally left but nio hasn't pruned from its dict yet.
        self._left_rooms: set[str] = set()
        # Fingerprint of the last RoomsChanged payload; skip emit when unchanged.
        self._last_rooms_fingerprint: frozenset[tuple[str, str, int]] = frozenset()

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
        return await self._finalize_login(response)

    async def login(self, user: str, password: str) -> Session:
        """Login to the homeserver with user/password credentials.

        Returns a Session on success; raises LoginError on failure.

        Bug fix (plan 0011): sets ``self._client.user = user`` before the nio
        ``login()`` call so nio knows which user to authenticate.  Previously
        this was silently omitted, causing real password logins to fail.
        """
        logger.info(
            "login: attempting password login for user=%s homeserver=%s",
            user,
            self._homeserver,
        )
        # Fix: set the user on the nio client BEFORE calling login()
        self._client.user = user
        response = await self._client.login(password, device_name=self._device_name)
        if isinstance(response, nio.LoginError):
            raise LoginError(str(response))
        return await self._finalize_login(response)

    async def _finalize_login(self, response: object) -> Session:
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
        logger.info(
            "_finalize_login: session created user_id=%s device_id=%s",
            session.user_id,
            session.device_id,
        )
        self._logged_in = True
        self._load_store()
        await self._open_cache()
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
        await self._open_cache()
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

        Performs one full-state sync first (populates all rooms), then starts
        sync_forever for incremental updates. A background poll task emits
        RoomsChanged progressively while the initial sync is processing.
        """
        if not self._logged_in:
            raise NotLoggedInError("Must be logged in before starting sync")
        if self._task is not None and not self._task.done():
            logger.info("start_sync: already running, skipping")
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
            self._last_rooms_fingerprint = self._rooms_fingerprint(cached_rooms)
            await self._emit(RoomsChanged(rooms=cached_rooms))

        self._rooms_poll_task = asyncio.create_task(self._poll_rooms_during_sync())
        try:
            full_state = not bool(cached_rooms)
            logger.info("Initial sync (full_state=%s)...", full_state)
            resp = await self._client.sync(timeout=30000, full_state=full_state)
            if isinstance(resp, nio.SyncResponse):
                logger.info("Initial sync complete: %d rooms", len(self._client.rooms))
                # nio.sync() updates room state but does not run response callbacks
                # (only sync_forever does). Mirror that path so last_activity is set.
                await self._on_sync(resp)
            else:
                logger.warning("Initial sync failed: %s", resp)
                self._initial_sync_done = True
        except Exception as exc:
            logger.error("Initial sync error: %s", exc)
            self._initial_sync_done = True

        # Now run incremental sync forever.
        logger.info("Starting sync_forever (incremental)")
        # loop_sleep_time prevents aioresponses from accumulating unbounded
        # deepcopy'd RequestCall entries in tests (30s long-poll makes this
        # irrelevant against a real homeserver).
        await self._client.sync_forever(timeout=30000, loop_sleep_time=100)

    async def close(self) -> None:
        """Cancel the sync task and close the nio client."""
        logger.info("close: cancelling sync task")
        if self._rooms_poll_task is not None and not self._rooms_poll_task.done():
            self._rooms_poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._rooms_poll_task
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        await self._client.close()
        if self._cache is not None:
            await self._cache.close()
            self._cache = None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def seed_last_activity(self, activities: dict[str, datetime]) -> None:
        """Pre-seed _last_activity from persisted cache data.

        Only fills entries that are not already present — a timestamp written
        by a real sync response always takes precedence.  Call this once at
        startup (before ``start_sync``) so that rooms are sortable by recency
        immediately, without waiting for the first incremental sync.

        Args:
            activities: Mapping of room_id → last_activity datetime.  Entries
                with a value of ``None`` should be filtered out by the caller
                before passing here.
        """
        for room_id, ts in activities.items():
            if room_id not in self._last_activity:
                self._last_activity[room_id] = ts

    def rooms(self) -> list[RoomSummary]:
        """Return summaries of all joined rooms (from current sync state)."""
        summaries: list[RoomSummary] = []
        for room_id, room in self._client.rooms.items():
            if room_id in self._left_rooms:
                continue
            # last_activity is populated by _on_sync via _update_last_activity()
            # or pre-seeded at startup via seed_last_activity().
            last_activity: datetime | None = self._last_activity.get(room_id)
            unread_count = int(room.unread_notifications)

            # Extract room tags (m.favourite, m.lowpriority, etc.).
            tags: dict[str, float | None] = {}
            if hasattr(room, "tags") and room.tags:
                for tag_name, tag_data in room.tags.items():
                    order: float | None = None
                    if tag_data:
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
                    unread_count=unread_count,
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
        """Fetch recent messages for a room, cache-first.

        Warm room (cached) → returns from SQLite immediately.
        Cold room → HTTP backfill, populate cache, return.

        Returns Message dataclasses. Undecryptable encrypted events are
        returned as placeholder messages so the UI can inform the user.
        """
        logger.info("messages: fetching up to %d messages for room=%s", limit, room_id)
        if not self._logged_in:
            raise NotLoggedInError("Must be logged in to fetch messages")

        # Cache-first: serve warm rooms from SQLite without a network call.
        if self._cache is not None and not await self._cache.is_cold(room_id):
            logger.debug("messages: cache hit for room=%s", room_id)
            cached = await self._cache.get_room(room_id, limit)
            room = self._client.rooms.get(room_id)
            if room is None:
                logger.warning(
                    "messages: room=%s not in nio rooms dict — cannot resolve display names",
                    room_id,
                )
            else:
                logger.debug(
                    "messages: room=%s has %d known users: %s",
                    room_id,
                    len(room.users),
                    list(room.users.keys()),
                )
            updated: list[Message] = []
            result_messages: list[Message] = []
            for msg in cached:
                resolved = _get_display_name(room, msg.sender)
                if resolved != msg.sender_display_name:
                    logger.debug(
                        "messages: display name updated sender=%s %r -> %r",
                        msg.sender,
                        msg.sender_display_name,
                        resolved,
                    )
                    msg = dataclasses.replace(msg, sender_display_name=resolved)
                    updated.append(msg)
                else:
                    logger.debug(
                        "messages: display name unchanged sender=%s cached=%r resolved=%r",
                        msg.sender,
                        msg.sender_display_name,
                        resolved,
                    )
                result_messages.append(msg)
            if updated:
                logger.debug(
                    "messages: writing back %d refreshed display names for room=%s",
                    len(updated),
                    room_id,
                )
                await self._cache.update_display_names(updated)
            return result_messages

        response = await self._client.room_messages(room_id, limit=limit)
        if not isinstance(response, nio.RoomMessagesResponse):
            logger.warning("room_messages failed for %s: %s", room_id, response)
            return []

        # First pass: collect message events and reaction events separately.
        # reactions_by_event: target_event_id -> emoji -> [sender, ...]
        reactions_by_event: dict[str, dict[str, list[str]]] = {}
        raw_messages: list[Message] = []
        # Accumulates (nio_event, index_in_raw_messages) for concurrent URL resolution.
        media_events: list[tuple[nio.RoomMessageMedia, int]] = []

        room = self._client.rooms.get(room_id)
        for event in response.chunk:
            if isinstance(event, nio.ReactionEvent):
                bucket = reactions_by_event.setdefault(event.reacts_to, {})
                bucket.setdefault(event.key, []).append(event.sender)
            elif isinstance(event, nio.RoomMessageText):
                rel_type = event.source.get("content", {}).get("m.relates_to", {}).get("rel_type")
                if rel_type == "m.replace":
                    continue
                sender_display_name = _get_display_name(room, event.sender)
                ts = datetime.fromtimestamp(event.server_timestamp / 1000, tz=UTC)
                reply_to: str | None = (
                    event.source.get("content", {})
                    .get("m.relates_to", {})
                    .get("m.in_reply_to", {})
                    .get("event_id")
                )
                raw_messages.append(
                    Message(
                        event_id=event.event_id,
                        room_id=room_id,
                        sender=event.sender,
                        sender_display_name=sender_display_name,
                        body=event.body,
                        timestamp=ts,
                        reply_to_event_id=reply_to,
                    )
                )
            elif isinstance(event, nio.RoomMessageMedia):
                sender_display_name = _get_display_name(room, event.sender)
                ts = datetime.fromtimestamp(event.server_timestamp / 1000, tz=UTC)
                media_type = _media_type_label(event)
                media_events.append((event, len(raw_messages)))
                raw_messages.append(
                    Message(
                        event_id=event.event_id,
                        room_id=room_id,
                        sender=event.sender,
                        sender_display_name=sender_display_name,
                        body=event.body or media_type,
                        timestamp=ts,
                        media_url=None,
                        media_type=media_type,
                    )
                )
            elif isinstance(event, nio.MegolmEvent):
                sender_display_name = _get_display_name(room, event.sender)
                ts = datetime.fromtimestamp(event.server_timestamp / 1000, tz=UTC)
                raw_messages.append(
                    Message(
                        event_id=event.event_id,
                        room_id=room_id,
                        sender=event.sender,
                        sender_display_name=sender_display_name,
                        body="\U0001f512 Unable to decrypt",
                        timestamp=ts,
                    )
                )

        # Resolve all media mxc URLs concurrently.
        if media_events:
            from dataclasses import replace as _replace

            urls = await asyncio.gather(
                *(self._client.mxc_to_http(ev.url) for ev, _ in media_events)
            )
            for (_, idx), http_url in zip(media_events, urls, strict=True):
                raw_messages[idx] = _replace(raw_messages[idx], media_url=http_url)

        # Second pass: attach reactions to their target messages.
        # Reactions targeting unknown event_ids are silently ignored.
        from dataclasses import replace

        result: list[Message] = []
        for msg in raw_messages:
            rxns = reactions_by_event.get(msg.event_id)
            if rxns:
                result.append(replace(msg, reactions=rxns))
            else:
                result.append(msg)

        # Backfill returns newest-first; reverse to chronological order.
        result.reverse()
        # Seed _last_activity from backfill so rooms() can sort by recency
        # even before an incremental sync delivers a new timeline event.
        if result:
            newest_ts = result[-1].timestamp
            if newest_ts > self._last_activity.get(room_id, datetime.min.replace(tzinfo=UTC)):
                self._last_activity[room_id] = newest_ts
        # Populate the cache with this backfill result.
        if self._cache is not None and result:
            await self._cache.put_many(result)
            await self._cache.evict_old(room_id)
        logger.info("messages: returning %d messages for room=%s", len(result), room_id)
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
        # nio doesn't prune its in-memory rooms dict until the next sync
        # response. Record the departure so rooms() hides it immediately and
        # _on_sync continues to hide it until nio catches up.
        self._left_rooms.add(room_id)
        await self._emit(RoomsChanged(rooms=self.rooms()))

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

    def me(self) -> tuple[str, str]:
        """Return (user_id, display_name) for the logged-in user."""
        user_id = self._client.user_id or ""
        return user_id, user_id

    async def send_reaction(self, room_id: str, event_id: str, emoji: str) -> None:
        """Send an m.reaction to the given event_id in a room.

        Raises NotLoggedInError if not logged in.
        """
        if not self._logged_in:
            raise NotLoggedInError("Must be logged in to send reactions")
        await self._client.room_send(
            room_id,
            "m.reaction",
            {
                "m.relates_to": {
                    "rel_type": "m.annotation",
                    "event_id": event_id,
                    "key": emoji,
                }
            },
        )

    async def edit_message(self, room_id: str, event_id: str, new_body: str) -> str:
        """Send an m.replace edit for an existing message. Returns the new event_id."""
        if not self._logged_in:
            raise NotLoggedInError("Must be logged in to edit messages")
        content: dict[str, object] = {
            "msgtype": "m.text",
            "body": f"* {new_body}",
            "m.new_content": {"msgtype": "m.text", "body": new_body},
            "m.relates_to": {"rel_type": "m.replace", "event_id": event_id},
        }
        resp = await self._client.room_send(room_id, "m.room.message", content)
        return getattr(resp, "event_id", "") or ""

    async def redact_message(self, room_id: str, event_id: str, reason: str = "") -> None:
        """Redact (delete) a message. Uses nio's room_redact."""
        if not self._logged_in:
            raise NotLoggedInError("Must be logged in to redact messages")
        await self._client.room_redact(room_id, event_id, reason=reason)
        logger.debug("Redacted %s in %s", event_id, room_id)

    async def send_text(self, room_id: str, body: str, reply_to_event_id: str | None = None) -> str:
        """Send a plain-text message to a room. Returns the server-assigned event_id.

        When reply_to_event_id is set, includes m.in_reply_to in the content.

        For encrypted rooms, TOFU trust is applied first (all devices in the
        room are marked as verified), then the message is sent with
        ``ignore_unverified_devices=True``.

        WARNING — TOFU is NOT MITM-safe (see module docstring).
        """
        logger.info("send_text: room=%s reply_to=%s", room_id, None)
        logger.debug("send_text: body preview room=%s body=%.60r", room_id, body)
        if not self._logged_in:
            raise NotLoggedInError("Must be logged in to send messages")

        content: dict[str, object] = {"msgtype": "m.text", "body": body}
        if reply_to_event_id is not None:
            content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to_event_id}}

        room = self._client.rooms.get(room_id)
        if room is not None and room.encrypted:
            self._tofu_trust_room(room_id)
            resp = await self._client.room_send(
                room_id,
                "m.room.message",
                content,
                ignore_unverified_devices=True,
            )
        else:
            resp = await self._client.room_send(
                room_id,
                "m.room.message",
                content,
            )
        return getattr(resp, "event_id", "") or ""

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

    async def _open_cache(self) -> None:
        """Open the message cache if a cache_path is configured.

        On failure, logs a warning and disables the cache for this session.
        """
        if self._cache is None or self._cache_path is None:
            return
        try:
            await self._cache.open(self._cache_path)
            logger.debug("MessageCache opened at %s", self._cache_path)
        except Exception as exc:
            logger.warning("MessageCache.open() failed — cache disabled: %s", exc)
            self._cache = None

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
        self._client.add_event_callback(self._on_room_media, nio.RoomMessageMedia)
        self._client.add_event_callback(self._on_megolm_event, nio.MegolmEvent)
        self._client.add_response_callback(self._on_sync, nio.SyncResponse)
        self._client.add_ephemeral_callback(self._on_typing, nio.TypingNoticeEvent)

    def _rooms_fingerprint(self, rooms: list[RoomSummary]) -> frozenset[tuple[str, str, int]]:
        """Cheap identity check: (room_id, display_name, unread_count) for each room."""
        return frozenset((r.room_id, r.display_name, r.unread_count) for r in rooms)

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
        rel_type = event.source.get("content", {}).get("m.relates_to", {}).get("rel_type")
        if rel_type == "m.replace":
            return
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
        if self._cache is not None:
            await self._cache.put(message)
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
        if self._cache is not None:
            await self._cache.put(message)
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
            body="\U0001f512 Unable to decrypt",
            timestamp=ts,
        )
        if self._cache is not None:
            await self._cache.put(placeholder)
        await self._emit(NewMessage(message=placeholder))

    async def _on_typing(self, room: nio.MatrixRoom, event: nio.TypingNoticeEvent) -> None:
        """nio ephemeral callback: someone in a room is typing."""
        logger.debug("_on_typing: room=%s users=%s", room.room_id, event.users)
        await self._emit(TypingChanged(room_id=room.room_id, user_ids=list(event.users)))

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
                    if tag_data:
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

    def _update_last_activity(self, response: nio.SyncResponse) -> None:
        """Scan the sync response's joined-room timelines to update _last_activity.

        nio delivers timeline.events oldest-first; reversed() gives newest-first so
        the first event we find with server_timestamp is the most recent one.
        """
        try:
            join = response.rooms.join
        except AttributeError:
            return
        for room_id, room_info in join.items():
            try:
                events = reversed(room_info.timeline.events)
            except AttributeError:
                continue
            for event in events:
                if hasattr(event, "server_timestamp"):
                    ts = datetime.fromtimestamp(event.server_timestamp / 1000, tz=UTC)
                    if ts > self._last_activity.get(room_id, datetime.min.replace(tzinfo=UTC)):
                        self._last_activity[room_id] = ts
                    break

    async def _on_sync(self, response: nio.SyncResponse) -> None:
        """nio callback: a sync response arrived — emit RoomsChanged and handle key ops."""
        self._initial_sync_done = True
        self._update_last_activity(response)
        # Discard _left_rooms entries that nio has now removed from its dict.
        self._left_rooms &= set(self._client.rooms)
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

        fp = self._rooms_fingerprint(rooms)
        if fp != self._last_rooms_fingerprint:
            self._last_rooms_fingerprint = fp
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
