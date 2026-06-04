"""Fakes and builders for telemente tests (plan 0003).

``FakeMatrixClient`` is the primary test double used by UI plans (0004-0009).
It mirrors the public surface of ``MatrixClient`` and lets tests push scripted
events to subscribers via ``fake.emit(event)``.

Helper builders (``make_login_response``, ``make_room``, ``make_text_event``)
are also here so they can be reused across matrix unit tests.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from telemente.config import Session
from telemente.matrix.client import (
    ClientEvent,
    EventHandler,
    LoginError,
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

        # In-memory data
        self._rooms: list[RoomSummary] = []
        self._members: dict[str, list[Member]] = {}
        self._messages: dict[str, list[Message]] = {}

        # Subscriptions
        self._handlers: list[EventHandler] = []

        # Spies
        self.login_called: bool = False
        self.start_sync_called: bool = False
        self.close_called: bool = False
        self.sent_messages: list[tuple[str, str]] = []
        self._logged_in: bool = False

    # ------------------------------------------------------------------
    # Scripting helpers
    # ------------------------------------------------------------------

    def unblock_login(self) -> None:
        """Release a blocked login() call."""
        self._login_event.set()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def login(self, user: str, password: str) -> Session:
        self.login_called = True
        if self.login_should_block:
            self._login_event.clear()
            await self._login_event.wait()
        if self.login_should_fail:
            raise LoginError("Scripted login failure")
        self._logged_in = True
        return Session(
            homeserver="https://matrix.org",
            user_id=user,
            device_id="FAKEDEV",
            access_token="fake_token",
        )

    async def restore(self, session: Session) -> None:
        self._logged_in = True

    async def logout(self) -> None:
        self._logged_in = False
        await self.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_sync(self) -> None:
        if not self._logged_in:
            raise NotLoggedInError("Not logged in")
        self.start_sync_called = True

    async def close(self) -> None:
        self.close_called = True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def rooms(self) -> list[RoomSummary]:
        return list(self._rooms)

    def members(self, room_id: str) -> list[Member]:
        return list(self._members.get(room_id, []))

    async def messages(self, room_id: str, limit: int = 50) -> list[Message]:
        return list(self._messages.get(room_id, []))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def send_text(self, room_id: str, body: str) -> None:
        if not self._logged_in:
            raise NotLoggedInError("Not logged in")
        self.sent_messages.append((room_id, body))

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        self._handlers.append(handler)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._handlers.remove(handler)

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
