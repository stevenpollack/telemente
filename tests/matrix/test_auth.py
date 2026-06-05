"""Tests for matrix auth helpers and MatrixClient SSO methods (plan 0011).

Tests 1-9: pure helpers (parse_login_flows, build_sso_redirect_url) + MatrixClient
extensions (login_flows via aioresponses, login_with_token via DI mock, and the
password-login user-bug regression test).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import nio
import pytest
from aioresponses import aioresponses

from matrix.helpers import stub_get
from telemente.config import Session
from telemente.matrix.auth import (
    IdentityProvider,
    build_sso_redirect_url,
    parse_login_flows,
)
from telemente.matrix.client import LoginError, MatrixClient

_HOMESERVER = "https://matrix.example.com"
_USER = "@alice:example.com"
_PASSWORD = "s3cret"


# ---------------------------------------------------------------------------
# Test 1: parse_login_flows — password + SSO with 2 IdPs
# ---------------------------------------------------------------------------


def test_parse_login_flows_password_and_sso() -> None:
    payload = {
        "flows": [
            {"type": "m.login.password"},
            {
                "type": "m.login.sso",
                "identity_providers": [
                    {"id": "oidc-google", "name": "Google"},
                    {"id": "oidc-github", "name": "GitHub", "icon": "mxc://icon"},
                ],
            },
            {"type": "m.login.token"},
        ]
    }
    flows = parse_login_flows(payload)

    assert flows.password is True
    assert flows.sso is True
    assert flows.token is True
    assert len(flows.identity_providers) == 2
    assert flows.identity_providers[0] == IdentityProvider(id="oidc-google", name="Google")
    assert flows.identity_providers[1] == IdentityProvider(
        id="oidc-github", name="GitHub", icon="mxc://icon"
    )


# ---------------------------------------------------------------------------
# Test 2: parse_login_flows — password only
# ---------------------------------------------------------------------------


def test_parse_login_flows_password_only() -> None:
    payload = {"flows": [{"type": "m.login.password"}]}
    flows = parse_login_flows(payload)

    assert flows.password is True
    assert flows.sso is False
    assert flows.token is False
    assert flows.identity_providers == []


# ---------------------------------------------------------------------------
# Test 3: parse_login_flows — SSO without identity_providers
# ---------------------------------------------------------------------------


def test_parse_login_flows_sso_no_idps() -> None:
    payload = {
        "flows": [
            {"type": "m.login.sso"},
            {"type": "m.login.token"},
        ]
    }
    flows = parse_login_flows(payload)

    assert flows.sso is True
    assert flows.password is False
    assert flows.identity_providers == []


# ---------------------------------------------------------------------------
# Test 4: build_sso_redirect_url — no IdP
# ---------------------------------------------------------------------------


def test_build_sso_redirect_url_no_idp() -> None:
    redirect_url = "http://localhost:12345/nonce123"
    url = build_sso_redirect_url(_HOMESERVER, redirect_url)

    assert url.startswith(_HOMESERVER + "/_matrix/client/v3/login/sso/redirect")
    # redirectUrl must be URL-encoded in the query string
    assert "redirectUrl=http" in url
    assert "localhost" in url
    # No idp_id path segment
    assert "/redirect?" in url


# ---------------------------------------------------------------------------
# Test 5: build_sso_redirect_url — with IdP
# ---------------------------------------------------------------------------


def test_build_sso_redirect_url_with_idp() -> None:
    redirect_url = "http://localhost:9999/nonce456"
    url = build_sso_redirect_url(_HOMESERVER, redirect_url, idp_id="oidc-google")

    assert "/_matrix/client/v3/login/sso/redirect/oidc-google" in url
    assert "redirectUrl=http" in url


# ---------------------------------------------------------------------------
# Test 6: login_flows HTTP — success and error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_flows_http() -> None:
    """login_flows() parses the GET /login response via aioresponses."""
    flows_json = {
        "flows": [
            {"type": "m.login.password"},
            {"type": "m.login.sso"},
            {"type": "m.login.token"},
        ]
    }
    login_url = f"{_HOMESERVER}/_matrix/client/v3/login"

    with aioresponses() as m:
        stub_get(m, login_url, payload=flows_json)
        real_nio = nio.AsyncClient(_HOMESERVER, _USER)
        client = MatrixClient(_HOMESERVER, nio_client=real_nio)
        flows = await client.login_flows()

    await real_nio.close()

    assert flows.password is True
    assert flows.sso is True
    assert flows.token is True


@pytest.mark.asyncio
async def test_login_flows_http_error_raises_login_error() -> None:
    """login_flows() raises LoginError on HTTP error."""
    login_url = f"{_HOMESERVER}/_matrix/client/v3/login"

    with aioresponses() as m:
        stub_get(m, login_url, status=500, body="Internal Server Error")
        real_nio = nio.AsyncClient(_HOMESERVER, _USER)
        client = MatrixClient(_HOMESERVER, nio_client=real_nio)
        with pytest.raises(LoginError):
            await client.login_flows()

    await real_nio.close()


# ---------------------------------------------------------------------------
# Test 7: login_with_token — success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_with_token_success() -> None:
    """login_with_token() exchanges a loginToken for a Session."""
    from types import SimpleNamespace

    resp = SimpleNamespace(
        user_id="@alice:example.com",
        device_id="DEVICE1",
        access_token="token_abc",
    )
    nio_mock: Any = AsyncMock(spec=nio.AsyncClient)
    nio_mock.user_id = None
    nio_mock.device_id = None
    nio_mock.access_token = None
    nio_mock.login.return_value = resp

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    session = await client.login_with_token("my_login_token")

    assert isinstance(session, Session)
    assert session.user_id == "@alice:example.com"
    assert session.device_id == "DEVICE1"
    assert session.access_token == "token_abc"
    assert session.homeserver == _HOMESERVER

    # Verify token was passed to nio login, not logged
    nio_mock.login.assert_awaited_once()
    call_kwargs = nio_mock.login.call_args
    assert call_kwargs.kwargs.get("token") == "my_login_token" or (
        len(call_kwargs.args) > 0 and call_kwargs.args[0] == "my_login_token"
    )


# ---------------------------------------------------------------------------
# Test 8: login_with_token — failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_with_token_failure() -> None:
    """login_with_token() raises LoginError when nio returns nio.LoginError."""
    nio_mock: Any = AsyncMock(spec=nio.AsyncClient)
    nio_mock.login.return_value = MagicMock(spec=nio.LoginError)

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)
    with pytest.raises(LoginError):
        await client.login_with_token("bad_token")


# ---------------------------------------------------------------------------
# Test 9: password login sets user on nio client (regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_login_sets_user() -> None:
    """Regression: login() sets self._client.user = user before calling nio login.

    Without this fix, nio's login() would not know which user to authenticate.
    """
    from types import SimpleNamespace

    resp = SimpleNamespace(
        user_id=_USER,
        device_id="DEV",
        access_token="tok",
    )
    nio_mock: Any = AsyncMock(spec=nio.AsyncClient)
    nio_mock.user_id = None
    nio_mock.device_id = None
    nio_mock.access_token = None
    nio_mock.user = None
    nio_mock.login.return_value = resp

    client = MatrixClient(_HOMESERVER, nio_client=nio_mock)

    # Patch the nio_mock so we can capture the state of .user at login time
    captured_user: list[str] = []

    async def _capturing_login(*args: Any, **kwargs: Any) -> Any:
        captured_user.append(nio_mock.user)
        return resp

    nio_mock.login.side_effect = _capturing_login

    await client.login(_USER, _PASSWORD)

    # After the fix: nio_mock.user should have been set to _USER before login()
    assert captured_user[0] == _USER, (
        f"Expected nio client.user == {_USER!r} at login time, got {captured_user[0]!r}. "
        "This is the regression test for the password-login user bug."
    )
