"""Tests for Matrix homeserver discovery (resolve_homeserver)."""

from __future__ import annotations

import pytest
from aioresponses import aioresponses

from matrix.helpers import http_stub_count, stub_get
from telemente.matrix.discovery import (
    DiscoveryError,
    discover_homeserver_url,
    parse_server_name,
    resolve_homeserver,
)

_WELL_KNOWN = "https://clam.au/.well-known/matrix/client"
_CLAM_DISCOVERY = {
    "m.homeserver": {"base_url": "https://clam.au"},
    "org.matrix.msc4143.rtc_foci": [
        {
            "type": "livekit",
            "livekit_service_url": "https://livekit.clam.au/livekit/jwt",
        }
    ],
    "org.matrix.msc2965.authentication": {
        "issuer": "https://mas.clam.au/",
        "account": "https://mas.clam.au/human/account",
    },
}


def test_parse_server_name_mxid() -> None:
    assert parse_server_name("@steven:clam.au") == "clam.au"


def test_parse_server_name_bare_domain() -> None:
    assert parse_server_name("clam.au") == "clam.au"


def test_parse_server_name_full_url() -> None:
    assert parse_server_name("https://clam.au") == "https://clam.au"


def test_parse_server_name_empty_raises() -> None:
    with pytest.raises(DiscoveryError):
        parse_server_name("")


@pytest.mark.asyncio
async def test_discover_homeserver_url_success() -> None:
    with aioresponses() as m:
        stub_get(m, _WELL_KNOWN, payload=_CLAM_DISCOVERY)
        result = await discover_homeserver_url("clam.au")
    assert result == "https://clam.au"


@pytest.mark.asyncio
async def test_discover_homeserver_url_clam_au_full_well_known() -> None:
    """Realistic clam.au well-known payload still yields m.homeserver.base_url."""
    with aioresponses() as m:
        stub_get(m, _WELL_KNOWN, payload=_CLAM_DISCOVERY)
        result = await resolve_homeserver("@steven:clam.au")
    assert result == "https://clam.au"


@pytest.mark.asyncio
async def test_resolve_homeserver_mxid() -> None:
    with aioresponses() as m:
        stub_get(m, _WELL_KNOWN, payload=_CLAM_DISCOVERY)
        result = await resolve_homeserver("@steven:clam.au")
    assert result == "https://clam.au"


@pytest.mark.asyncio
async def test_resolve_homeserver_bare_domain() -> None:
    with aioresponses() as m:
        stub_get(m, _WELL_KNOWN, payload=_CLAM_DISCOVERY)
        result = await resolve_homeserver("clam.au")
    assert result == "https://clam.au"


@pytest.mark.asyncio
async def test_resolve_homeserver_full_url_skips_well_known() -> None:
    with aioresponses() as m:
        result = await resolve_homeserver("https://clam.au")
    assert result == "https://clam.au"
    assert http_stub_count(m) == 0


@pytest.mark.asyncio
async def test_discover_homeserver_url_404_fallback() -> None:
    with aioresponses() as m:
        stub_get(m, _WELL_KNOWN, status=404, body="Not Found")
        result = await discover_homeserver_url("clam.au")
    assert result == "https://clam.au"


@pytest.mark.asyncio
async def test_resolve_homeserver_empty_raises() -> None:
    with pytest.raises(DiscoveryError):
        await resolve_homeserver("   ")
