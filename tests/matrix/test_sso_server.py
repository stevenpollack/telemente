"""Tests for SsoCallbackServer (plan 0011).

Uses real loopback aiohttp (127.0.0.1) — not external network.
"""

from __future__ import annotations

import pytest

from telemente.matrix.sso import SsoCallbackServer, SsoTimeoutError

# ---------------------------------------------------------------------------
# Test 10: captures loginToken from redirect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_captures_login_token() -> None:
    """start() + GET with ?loginToken → wait_for_token() returns the token."""
    import aiohttp

    server = SsoCallbackServer()
    redirect_url = await server.start()

    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(f"{redirect_url}?loginToken=abc123")
            assert resp.status == 200
            body = await resp.text()
            assert "close this tab" in body.lower() or "telemente" in body.lower()

        token = await server.wait_for_token(timeout=5.0)
        assert token == "abc123"
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# Test 11: missing loginToken returns 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_token_returns_400() -> None:
    """GET the redirect URL without loginToken → 400; future not resolved."""
    import asyncio

    import aiohttp

    server = SsoCallbackServer()
    redirect_url = await server.start()

    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(redirect_url)  # no ?loginToken
            assert resp.status == 400

        # The future should not be resolved — verify with a short timeout
        with pytest.raises((SsoTimeoutError, asyncio.TimeoutError)):
            await server.wait_for_token(timeout=0.1)
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# Test 12: wrong path returns 404, token not resolved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_path_ignored() -> None:
    """GET a different path → 404; token future unresolved."""
    import asyncio

    import aiohttp

    server = SsoCallbackServer()
    redirect_url = await server.start()

    # Extract base URL (scheme + host + port) and hit a different path
    from yarl import URL

    base = URL(redirect_url)
    wrong_url = str(base.with_path("/wrong_path"))

    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(f"{wrong_url}?loginToken=bad")
            assert resp.status == 404

        with pytest.raises((SsoTimeoutError, asyncio.TimeoutError)):
            await server.wait_for_token(timeout=0.1)
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# Test 13: timeout raises SsoTimeoutError; stop is idempotent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_raises() -> None:
    """wait_for_token(timeout=0.1) with no request → SsoTimeoutError."""
    server = SsoCallbackServer()
    await server.start()

    try:
        with pytest.raises(SsoTimeoutError):
            await server.wait_for_token(timeout=0.1)
    finally:
        # stop() is idempotent — calling twice should not raise
        await server.stop()
        await server.stop()
