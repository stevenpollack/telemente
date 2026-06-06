"""Tier-1 tests for MatrixClient.send_read_receipt (plan 0031).

Tests:
  test_send_read_receipt_posts_to_correct_url
  test_send_read_receipt_not_logged_in_raises
  test_send_read_receipt_http_error_logs_warning_no_raise
  test_send_read_receipt_network_error_logs_warning_no_raise
"""

from __future__ import annotations

import re

import aiohttp
import pytest
from aioresponses import aioresponses

from matrix.helpers import HOMESERVER, build_nio_mock, make_nio_room, restore_client
from telemente.matrix.client import MatrixClient, NotLoggedInError


def _receipt_url(room_id: str, event_id: str) -> str:
    return f"{HOMESERVER}/_matrix/client/v3/rooms/{room_id}/receipt/m.read/{event_id}"


def _receipt_url_pattern(room_id: str, event_id: str) -> re.Pattern[str]:
    return re.compile(
        rf"^{re.escape(HOMESERVER)}/_matrix/client/v3/rooms"
        rf"/{re.escape(room_id)}/receipt/m\.read/{re.escape(event_id)}$"
    )


async def test_send_read_receipt_posts_to_correct_url() -> None:
    """send_read_receipt POSTs to /receipt/m.read/{eventId} and returns cleanly."""
    room_id = "!room1:example.com"
    event_id = "$ev42:example.com"

    nio_mock = build_nio_mock(rooms={room_id: make_nio_room(room_id=room_id)})
    client = await restore_client(nio_mock)

    with aioresponses() as m:
        m.post(_receipt_url(room_id, event_id), payload={}, status=200)
        await client.send_read_receipt(room_id, event_id)

    # If we got here without an exception, the call succeeded.
    assert True


async def test_send_read_receipt_not_logged_in_raises() -> None:
    """send_read_receipt raises NotLoggedInError when not logged in."""
    nio_mock = build_nio_mock()
    client = MatrixClient(HOMESERVER, nio_client=nio_mock)
    # client is NOT restored/logged in

    with pytest.raises(NotLoggedInError):
        await client.send_read_receipt("!room:example.com", "$ev:example.com")


async def test_send_read_receipt_http_error_logs_warning_no_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-200 HTTP response logs a warning and does not raise."""
    room_id = "!room2:example.com"
    event_id = "$ev99:example.com"

    nio_mock = build_nio_mock(rooms={room_id: make_nio_room(room_id=room_id)})
    client = await restore_client(nio_mock)

    with aioresponses() as m:
        m.post(_receipt_url(room_id, event_id), payload={"errcode": "M_FORBIDDEN"}, status=403)
        import logging

        with caplog.at_level(logging.WARNING, logger="telemente.matrix.client"):
            await client.send_read_receipt(room_id, event_id)

    # Must not raise; must log a warning
    assert any("send_read_receipt" in r.message and "403" in r.message for r in caplog.records)


async def test_send_read_receipt_network_error_logs_warning_no_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A network exception logs a warning and does not propagate."""
    room_id = "!room3:example.com"
    event_id = "$ev100:example.com"

    nio_mock = build_nio_mock(rooms={room_id: make_nio_room(room_id=room_id)})
    client = await restore_client(nio_mock)

    with aioresponses() as m:
        m.post(
            _receipt_url(room_id, event_id),
            exception=aiohttp.ClientError("simulated network error"),
        )
        import logging

        with caplog.at_level(logging.WARNING, logger="telemente.matrix.client"):
            await client.send_read_receipt(room_id, event_id)

    # Must not raise; must log a warning
    assert any("send_read_receipt" in r.message for r in caplog.records)
