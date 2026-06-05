"""Matrix homeserver discovery via .well-known (MXID / server name → base URL).

Resolves user-entered homeserver values (MXID, bare domain, or full URL) to the
homeserver base URL used by MatrixClient and SSO redirects.
"""

from __future__ import annotations

import logging
from typing import Any, cast
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

_WELL_KNOWN_PATH = "/.well-known/matrix/client"
_DISCOVERY_TIMEOUT = 10.0


class DiscoveryError(Exception):
    """Raised when homeserver input cannot be parsed or resolved."""


def _is_homeserver_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def parse_server_name(value: str) -> str:
    """Extract a server name or return a full homeserver URL unchanged.

    - MXID ``@local:domain`` → ``domain``
    - Full URL ``https://host/...`` → returned as-is (no discovery)
    - Bare ``domain`` or ``domain:port`` → ``domain`` (host part only)

    Raises ``DiscoveryError`` when input is empty or not a valid identifier.
    """
    value = value.strip()
    if not value:
        raise DiscoveryError("Homeserver is required.")

    if value.startswith("@"):
        if ":" not in value:
            raise DiscoveryError(f"Invalid Matrix ID: {value}")
        return value.split(":", 1)[1]

    if _is_homeserver_url(value):
        return value.rstrip("/")

    # Bare server name — strip optional scheme/path fragments
    host = value.removeprefix("https://").removeprefix("http://").split("/")[0].strip()
    if not host:
        raise DiscoveryError("Homeserver is required.")
    return host


def _parse_well_known_base_url(payload: dict[str, Any]) -> str | None:
    homeserver_raw = payload.get("m.homeserver")
    if not isinstance(homeserver_raw, dict):
        return None
    homeserver: dict[str, Any] = cast(dict[str, Any], homeserver_raw)
    base_url = homeserver.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        return None
    return base_url.rstrip("/")


async def discover_homeserver_url(server_name: str) -> str:
    """Discover the homeserver base URL for a server name via .well-known.

    ``GET https://{server_name}/.well-known/matrix/client`` and read
    ``m.homeserver.base_url``. On failure, falls back to ``https://{server_name}``.
    """
    server_name = server_name.strip()
    if not server_name:
        raise DiscoveryError("Server name is required.")

    url = f"https://{server_name}{_WELL_KNOWN_PATH}"
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=_DISCOVERY_TIMEOUT)) as resp,
        ):
            if resp.status != 200:
                logger.debug(
                    "well-known for %s returned HTTP %s — using fallback",
                    server_name,
                    resp.status,
                )
                return f"https://{server_name}"

            payload: dict[str, Any] = await resp.json()
    except Exception as exc:
        logger.debug("well-known request for %s failed: %s — using fallback", server_name, exc)
        return f"https://{server_name}"

    base_url = _parse_well_known_base_url(payload)
    if base_url is None:
        logger.debug("well-known for %s missing base_url — using fallback", server_name)
        return f"https://{server_name}"

    return base_url


async def resolve_homeserver(value: str) -> str:
    """Resolve user input to a homeserver base URL.

    Full URLs are returned unchanged (normalized, no trailing slash).
    MXIDs and bare server names are discovered via ``discover_homeserver_url``.
    """
    parsed = parse_server_name(value)
    if _is_homeserver_url(parsed):
        return parsed.rstrip("/")

    return await discover_homeserver_url(parsed)


def server_name_from_mxid(user: str) -> str | None:
    """Return the server part of a full MXID, or None if not an MXID."""
    user = user.strip()
    if not user.startswith("@") or ":" not in user:
        return None
    return user.split(":", 1)[1]


def homeserver_hosts_match(resolved_a: str, resolved_b: str) -> bool:
    """Return True if two resolved base URLs refer to the same host."""
    host_a = urlparse(resolved_a).netloc.lower()
    host_b = urlparse(resolved_b).netloc.lower()
    return host_a == host_b and bool(host_a)


def server_name_matches_resolved_url(server_name: str, resolved_url: str) -> bool:
    """Return True if a Matrix server name matches a resolved homeserver URL host."""
    host = urlparse(resolved_url).netloc.lower()
    name = server_name.lower()
    if not host or not name:
        return False
    return host == name or host.endswith(f".{name}")
