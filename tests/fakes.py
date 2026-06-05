"""Fakes and builders for telemente tests (plan 0003).

``FakeMatrixClient`` is the primary test double used by UI plans (0004-0009).
It mirrors the public surface of ``MatrixClient`` and lets tests push scripted
events to subscribers via ``fake.emit(event)``.

Helper builders (``make_login_response``, ``make_room``, ``make_text_event``)
are also here so they can be reused across matrix unit tests.

Plan 0011: extended with SSO surface:
  - ``set_flows(LoginFlows)`` — script what ``login_flows()`` returns.
  - ``login_flows()`` — returns scripted flows.
  - ``sso_redirect_url(redirect_url, idp_id)`` — builds URL, records spy data.
  - ``login_with_token(token)`` — scriptable success/failure, spy.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from telemente.config import Session
from telemente.matrix.auth import LoginFlows, build_sso_redirect_url
from telemente.matrix.client import (
    ClientEvent,
    EventHandler,
    LoginError,
    MatrixError,
    NotLoggedInError,
)
from telemente.matrix.models import Member, Message, RoomSummary

# ---------------------------------------------------------------------------
# Payload builders (used in unit tests that inject a mock nio client)
# ---------------------------------------------------------------------------


def make_login_response(
    user_id: str = "@alice:matrix.org",
    device_id: str = "TESTDEV",
    access_token: str = "test_access_token",
) -> Any:
    """Build a minimal fake nio LoginResponse (SimpleNamespace)."""
    return SimpleNamespace(
        user_id=user_id,
        device_id=device_id,
        access_token=access_token,
    )


def make_room(
    room_id: str = "!room1:matrix.org",
    display_name: str = "Test Room",
    encrypted: bool = False,
    users: dict[str, Any] | None = None,
    power_levels_users: dict[str, int] | None = None,
) -> Any:
    """Build a minimal fake nio MatrixRoom (SimpleNamespace)."""
    pl = SimpleNamespace(users=power_levels_users or {})
    return SimpleNamespace(
        room_id=room_id,
        display_name=display_name,
        encrypted=encrypted,
        users=users or {},
        power_levels=pl,
    )


def make_text_event(
    event_id: str = "$ev1:matrix.org",
    sender: str = "@alice:matrix.org",
    body: str = "Hello!",
    server_timestamp: int = 1_700_000_000_000,
) -> Any:
    """Build a minimal fake nio RoomMessageText (SimpleNamespace)."""
    return SimpleNamespace(
        event_id=event_id,
        sender=sender,
        body=body,
        server_timestamp=server_timestamp,
    )


# ---------------------------------------------------------------------------
# FakeMatrixClient
# ---------------------------------------------------------------------------


class FakeMatrixClient:
    """In-memory test double for ``MatrixClient``.

    Implements the same public surface as ``MatrixClient`` without touching
    any real network or nio code.  UI tests inject this via the DI seam.

    Scripted behaviour
    ------------------
    - ``login_should_fail``: if True, ``login()`` raises ``LoginError``.
    - ``login_should_block``: if True, ``login()`` blocks until
      ``unblock_login()`` is called (useful for testing loading states).
    - Rooms, members and messages are populated via ``_rooms``, ``_members``,
      ``_messages`` dicts before calling the method under test.

    Test spies
    ----------
    - ``login_called``: bool — was login() called?
    - ``start_sync_called``: bool
    - ``close_called``: bool
    - ``sent_messages``: list of (room_id, body) tuples
    """

    def __init__(self) -> None:
        # Scripted state
        self.login_should_fail: bool = False
        self.login_should_block: bool = False
        self._login_event: asyncio.Event = asyncio.Event()
        self._login_event.set()  # not blocking by default

        # SSO scripted state (plan 0011)
        self._flows: LoginFlows = LoginFlows(password=True, sso=False, token=False)
        self.login_with_token_should_fail: bool = False
        self._fake_homeserver: str = "https://matrix.org"
        self.homeserver: str = "https://matrix.org"

        # In-memory data
        self.rooms_data: list[RoomSummary] = []
        self.members_data: dict[str, list[Member]] = {}
        self.messages_data: dict[str, list[Message]] = {}
        # Scripted search results: room_id -> list of matching event_ids
        self.search_results: dict[str, list[str]] = {}

        # Subscriptions
        self._handlers: list[EventHandler] = []

        # Spies
        self.login_called: bool = False
        self.start_sync_called: bool = False
        self.close_called: bool = False
        self.sent_messages: list[tuple[str, str, str | None]] = []
        self.sent_reactions: list[tuple[str, str, str]] = []
        self.edited_messages: list[tuple[str, str, str]] = []
        self.redacted_messages: list[tuple[str, str]] = []
        self.left_rooms: list[str] = []
        self.set_tags: list[tuple[str, str, float | None]] = []
        self.removed_tags: list[tuple[str, str]] = []
        self.logged_in: bool = False

        # SSO spies (plan 0011)
        self.login_with_token_called: bool = False
        self.login_with_token_token: str = ""
        self.sso_redirect_url_called: bool = False
        self.sso_redirect_url_idp_id: str | None = None

        # §2.3.1 Per-operation failure scripting (plan 0018)
        self._fail_next: set[str] = set()
        self._always_fail: set[str] = set()
        self.raise_not_logged_in: bool = False  # §2.3.9

        # §2.3.2 Per-operation blocking (plan 0018)
        self._blocked_ops: dict[str, asyncio.Event] = {}

        # §2.3.3 Scripted me() (plan 0018)
        self._me: tuple[str, str] = ("@fake:matrix.org", "Fake User")

        # Scripted can_redact results (plan 0020): (room_id, target_sender) -> bool
        # Default False for other users. Not cleared by reset_spies().
        self.can_redact_results: dict[tuple[str, str], bool] = {}

        # §2.3.4 Paginated messages (plan 0018)
        self._messages_page_size: int = 50

        # §2.3.5 Auto-emit on send_text (plan 0018)
        self.auto_emit_sent_messages: bool = False

        # §2.3.6 Subscription counters (plan 0018)
        self.subscribe_count: int = 0
        self.unsubscribe_count: int = 0

    # ------------------------------------------------------------------
    # Scripting helpers
    # ------------------------------------------------------------------

    def unblock_login(self) -> None:
        """Release a blocked login() call."""
        self._login_event.set()

    def set_flows(self, flows: LoginFlows) -> None:
        """Script the LoginFlows returned by login_flows()."""
        self._flows = flows

    def set_homeserver(self, homeserver: str) -> None:
        """Script the homeserver URL used by SSO helpers."""
        self._fake_homeserver = homeserver
        self.homeserver = homeserver

    def set_me(self, user_id: str, display_name: str) -> None:
        """Script the (user_id, display_name) tuple returned by me()."""
        self._me = (user_id, display_name)

    def set_messages_page_size(self, size: int) -> None:
        """Limit how many messages messages() returns per call (simulates pagination)."""
        self._messages_page_size = size

    def fail_next(self, op: str) -> None:
        """Make the next call to ``op`` raise MatrixError (one-shot)."""
        self._fail_next.add(op)

    def always_fail(self, op: str) -> None:
        """Make every call to ``op`` raise MatrixError until cleared."""
        self._always_fail.add(op)

    def clear_failures(self, op: str | None = None) -> None:
        """Clear failure scripting for ``op``, or all ops if None."""
        if op is None:
            self._fail_next.clear()
            self._always_fail.clear()
        else:
            self._fail_next.discard(op)
            self._always_fail.discard(op)

    def _check_fail(self, op: str) -> None:
        if self.raise_not_logged_in:
            raise NotLoggedInError(f"Scripted not-logged-in: {op}")
        if op in self._always_fail:
            raise MatrixError(f"Scripted failure: {op}")
        if op in self._fail_next:
            self._fail_next.discard(op)
            raise MatrixError(f"Scripted failure: {op}")

    def block_op(self, op: str) -> None:
        """Make the next call to ``op`` block until ``unblock_op(op)`` is called."""
        ev = asyncio.Event()
        self._blocked_ops[op] = ev

    def unblock_op(self, op: str) -> None:
        """Release a blocked operation."""
        ev = self._blocked_ops.pop(op, None)
        if ev:
            ev.set()

    async def _maybe_block(self, op: str) -> None:
        ev = self._blocked_ops.get(op)
        if ev is not None:
            await ev.wait()
            self._blocked_ops.pop(op, None)

    def reset_spies(self) -> None:
        """Clear all call recording without affecting scripted state."""
        self.login_called = False
        self.start_sync_called = False
        self.close_called = False
        self.sent_messages.clear()
        self.sent_reactions.clear()
        self.edited_messages.clear()
        self.redacted_messages.clear()
        self.left_rooms.clear()
        self.set_tags.clear()
        self.removed_tags.clear()
        self.login_with_token_called = False
        self.login_with_token_token = ""
        self.sso_redirect_url_called = False
        self.sso_redirect_url_idp_id = None
        self.subscribe_count = 0
        self.unsubscribe_count = 0

    async def emit_sequence(self, *events: ClientEvent, pause: float = 0.0) -> None:
        """Emit events in order, optionally sleeping between each."""
        for event in events:
            await self.emit(event)
            if pause > 0:
                await asyncio.sleep(pause)

    # ------------------------------------------------------------------
    # Auth — SSO surface (plan 0011)
    # ------------------------------------------------------------------

    async def login_flows(self) -> LoginFlows:
        """Return the scripted login flows."""
        return self._flows

    def sso_redirect_url(self, redirect_url: str, idp_id: str | None = None) -> str:
        """Build SSO redirect URL and record spy data."""
        self.sso_redirect_url_called = True
        self.sso_redirect_url_idp_id = idp_id
        return build_sso_redirect_url(self._fake_homeserver, redirect_url, idp_id)

    async def login_with_token(self, token: str) -> Session:
        """Exchange a loginToken for a Session (scriptable)."""
        self.login_with_token_called = True
        self.login_with_token_token = token
        if self.login_with_token_should_fail:
            raise LoginError("Scripted token login failure")
        self.logged_in = True
        return Session(
            homeserver=self._fake_homeserver,
            user_id="@sso_user:matrix.org",
            device_id="SSOFAKEDEV",
            access_token="fake_sso_access_token",
        )

    # ------------------------------------------------------------------
    # Auth — password
    # ------------------------------------------------------------------

    async def login(self, user: str, password: str) -> Session:
        self.login_called = True
        if self.login_should_block:
            self._login_event.clear()
            await self._login_event.wait()
        if self.login_should_fail:
            raise LoginError("Scripted login failure")
        self.logged_in = True
        return Session(
            homeserver="https://matrix.org",
            user_id=user,
            device_id="FAKEDEV",
            access_token="fake_token",
        )

    async def restore(self, _session: Session) -> None:
        self.logged_in = True

    async def logout(self) -> None:
        self.logged_in = False
        await self.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_sync(self) -> None:
        if not self.logged_in:
            raise NotLoggedInError("Not logged in")
        self.start_sync_called = True

    async def close(self) -> None:
        self.close_called = True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def rooms(self) -> list[RoomSummary]:
        return list(self.rooms_data)

    def members(self, room_id: str) -> list[Member]:
        return list(self.members_data.get(room_id, []))

    async def messages(self, room_id: str, limit: int = 50) -> list[Message]:
        all_msgs = list(self.messages_data.get(room_id, []))
        effective_limit = min(limit, self._messages_page_size)
        return all_msgs[:effective_limit]

    async def search_messages(self, room_id: str, query: str) -> list[str]:
        """Return scripted search results for the given room and query."""
        if not query:
            return []
        return list(self.search_results.get(room_id, []))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def me(self) -> tuple[str, str]:
        return self._me

    def can_redact(self, room_id: str, target_sender: str) -> bool:
        """True if the logged-in user may redact target_sender's message.

        Own messages are always redactable. Other senders use can_redact_results
        (defaults to False).
        """
        if target_sender == self._me[0]:
            return True
        return self.can_redact_results.get((room_id, target_sender), False)

    async def send_text(self, room_id: str, body: str, reply_to_event_id: str | None = None) -> str:
        if not self.logged_in:
            raise NotLoggedInError("Not logged in")
        self._check_fail("send_text")
        await self._maybe_block("send_text")
        self.sent_messages.append((room_id, body, reply_to_event_id))
        event_id = f"$fake_sent_{len(self.sent_messages)}:matrix.org"
        if self.auto_emit_sent_messages:
            from datetime import UTC, datetime

            from telemente.matrix.client import NewMessage
            from telemente.matrix.models import Message

            msg = Message(
                event_id=event_id,
                room_id=room_id,
                sender=self._me[0],
                sender_display_name=self._me[1],
                body=body,
                timestamp=datetime.now(UTC),
                reply_to_event_id=reply_to_event_id,
            )
            await self.emit(NewMessage(message=msg))
        return event_id

    async def send_reaction(self, room_id: str, event_id: str, emoji: str) -> None:
        if not self.logged_in:
            raise NotLoggedInError("Not logged in")
        self._check_fail("send_reaction")
        await self._maybe_block("send_reaction")
        self.sent_reactions.append((room_id, event_id, emoji))

    async def edit_message(self, room_id: str, event_id: str, new_body: str) -> str:
        if not self.logged_in:
            raise NotLoggedInError("Not logged in")
        self._check_fail("edit_message")
        await self._maybe_block("edit_message")
        self.edited_messages.append((room_id, event_id, new_body))
        return f"$fake_edit_{len(self.edited_messages)}:matrix.org"

    async def redact_message(self, room_id: str, event_id: str, reason: str = "") -> None:
        if not self.logged_in:
            raise NotLoggedInError("Not logged in")
        self._check_fail("redact_message")
        await self._maybe_block("redact_message")
        self.redacted_messages.append((room_id, event_id))

    async def leave_room(self, room_id: str) -> None:
        if not self.logged_in:
            raise NotLoggedInError("Not logged in")
        self._check_fail("leave_room")
        await self._maybe_block("leave_room")
        self.left_rooms.append(room_id)
        self.rooms_data = [r for r in self.rooms_data if r.room_id != room_id]

    async def set_room_tag(self, room_id: str, tag: str, order: float | None = None) -> None:
        if not self.logged_in:
            raise NotLoggedInError("Not logged in")
        self._check_fail("set_room_tag")
        await self._maybe_block("set_room_tag")
        self.set_tags.append((room_id, tag, order))
        # Update rooms_data so rooms() reflects the new tag immediately.
        self.rooms_data = [
            RoomSummary(
                room_id=r.room_id,
                display_name=r.display_name,
                unread_count=r.unread_count,
                last_activity=r.last_activity,
                encrypted=r.encrypted,
                tags={**r.tags, tag: order},
            )
            if r.room_id == room_id
            else r
            for r in self.rooms_data
        ]
        from telemente.matrix.client import RoomsChanged

        await self.emit(RoomsChanged(rooms=list(self.rooms_data)))

    async def remove_room_tag(self, room_id: str, tag: str) -> None:
        if not self.logged_in:
            raise NotLoggedInError("Not logged in")
        self._check_fail("remove_room_tag")
        await self._maybe_block("remove_room_tag")
        self.removed_tags.append((room_id, tag))
        # Update rooms_data so rooms() reflects the removed tag immediately.
        self.rooms_data = [
            RoomSummary(
                room_id=r.room_id,
                display_name=r.display_name,
                unread_count=r.unread_count,
                last_activity=r.last_activity,
                encrypted=r.encrypted,
                tags={k: v for k, v in r.tags.items() if k != tag},
            )
            if r.room_id == room_id
            else r
            for r in self.rooms_data
        ]
        from telemente.matrix.client import RoomsChanged

        await self.emit(RoomsChanged(rooms=list(self.rooms_data)))

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        self._handlers.append(handler)
        self.subscribe_count += 1

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._handlers.remove(handler)
            self.unsubscribe_count += 1

        return _unsubscribe

    # ------------------------------------------------------------------
    # Test control: push scripted events to subscribers
    # ------------------------------------------------------------------

    async def emit(self, event: ClientEvent) -> None:
        """Push a scripted event to all registered subscribers.

        Use this in tests to simulate incoming Matrix events without a
        real homeserver:

            fake = FakeMatrixClient()
            ...
            await fake.emit(NewMessage(message=some_message))
        """
        for handler in list(self._handlers):
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
