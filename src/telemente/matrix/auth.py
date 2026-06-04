"""Matrix authentication helpers (plan 0011).

Pure dataclasses and parsing functions for Matrix login flows.
No nio types leak out of this module; it is imported by client.py.

Security notes:
- The ``redirectUrl`` used for SSO is loopback-only (``http://localhost:PORT/<nonce>``).
  The nonce path prevents an unrelated request from resolving the token.
- The ``loginToken`` is single-use and short-lived — exchange it immediately; NEVER log it.
- Some homeservers restrict allowed SSO redirect URLs; if loopback is rejected,
  the manual-paste path is the documented escape hatch.
- ``.well-known`` homeserver discovery (resolving a bare server name to a base URL)
  is out of scope — the user enters a full base URL. This is a future enhancement.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode


@dataclass(frozen=True, slots=True)
class IdentityProvider:
    """A single Identity Provider advertised by the homeserver for SSO."""

    id: str
    name: str
    icon: str | None = None


@dataclass(frozen=True, slots=True)
class LoginFlows:
    """Supported login flows advertised by a homeserver.

    Parsed from ``GET /_matrix/client/v3/login``.
    """

    password: bool
    sso: bool
    token: bool  # m.login.token (required for SSO exchange)
    identity_providers: list[IdentityProvider] = field(default_factory=list)


def parse_login_flows(payload: Mapping[str, object]) -> LoginFlows:
    """Parse a ``GET /login`` JSON payload into a ``LoginFlows`` dataclass.

    Parameters
    ----------
    payload:
        The raw JSON dict from the homeserver (must contain a ``"flows"`` list).
    """
    flows_list = payload.get("flows", [])
    if not isinstance(flows_list, list):
        flows_list = []

    has_password = False
    has_sso = False
    has_token = False
    idps: list[IdentityProvider] = []

    for flow in flows_list:
        if not isinstance(flow, dict):
            continue
        flow_type = flow.get("type", "")
        if flow_type == "m.login.password":
            has_password = True
        elif flow_type == "m.login.sso":
            has_sso = True
            raw_idps = flow.get("identity_providers", [])
            if isinstance(raw_idps, list):
                for idp in raw_idps:
                    if not isinstance(idp, dict):
                        continue
                    idp_id = idp.get("id", "")
                    idp_name = idp.get("name", "")
                    idp_icon = idp.get("icon")
                    if isinstance(idp_id, str) and isinstance(idp_name, str):
                        idps.append(
                            IdentityProvider(
                                id=idp_id,
                                name=idp_name,
                                icon=str(idp_icon) if idp_icon is not None else None,
                            )
                        )
        elif flow_type == "m.login.token":
            has_token = True

    return LoginFlows(
        password=has_password,
        sso=has_sso,
        token=has_token,
        identity_providers=idps,
    )


def build_sso_redirect_url(homeserver: str, redirect_url: str, idp_id: str | None = None) -> str:
    """Build the full SSO redirect URL for the given homeserver.

    Parameters
    ----------
    homeserver:
        Base URL of the homeserver (e.g. ``https://matrix.example.com``).
    redirect_url:
        The loopback callback URL to redirect the browser to after auth
        (e.g. ``http://localhost:PORT/<nonce>``).
    idp_id:
        Optional identity provider ID. When given, appended as a path
        segment (``/sso/redirect/{idp_id}``).

    Returns
    -------
    str
        The full URL to open in the browser.
    """
    hs = homeserver.rstrip("/")
    base_path = "/_matrix/client/v3/login/sso/redirect"
    if idp_id is not None:
        base_path = f"{base_path}/{quote(idp_id, safe='')}"
    query = urlencode({"redirectUrl": redirect_url})
    return f"{hs}{base_path}?{query}"
