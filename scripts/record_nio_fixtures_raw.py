#!/usr/bin/env python3
"""Record Matrix HTTP responses into tests/fixtures/nio/recorded/ (local only).

Reads credentials from ``.env.local`` (copy from ``.env.local.example``).
Tokens are sanitized before write. CI never runs this — replay uses aioresponses.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
RECORDED = ROOT / "tests" / "fixtures" / "nio" / "recorded"
ENV_FILE = ROOT / ".env.local"
PLACEHOLDER_TOKEN = "RECORDED_PLACEHOLDER_TOKEN"


def _load_env_local() -> dict[str, str]:
    if not ENV_FILE.is_file():
        raise SystemExit(f"Missing {ENV_FILE} — copy .env.local.example and fill in credentials.")
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    required = ("MATRIX_HOMESERVER", "MATRIX_USER", "MATRIX_PASSWORD")
    missing = [k for k in required if k not in values or not values[k]]
    if missing:
        raise SystemExit(f".env.local missing keys: {', '.join(missing)}")
    return values


def _sanitize_login(body: dict[str, Any]) -> dict[str, Any]:
    """Strip secrets from a login response before persisting."""
    sanitized = dict(body)
    if "access_token" in sanitized:
        sanitized["access_token"] = PLACEHOLDER_TOKEN
    if "refresh_token" in sanitized:
        sanitized["refresh_token"] = PLACEHOLDER_TOKEN
    return sanitized


async def _record(homeserver: str, user: str, password: str, *, full_sync: bool) -> None:
    RECORDED.mkdir(parents=True, exist_ok=True)
    base = homeserver.rstrip("/")
    login_url = f"{base}/_matrix/client/v3/login"
    sync_url = f"{base}/_matrix/client/v3/sync"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            login_url,
            json={
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": user},
                "password": password,
            },
        ) as resp:
            login_body = await resp.json()
            if resp.status != 200:
                raise SystemExit(f"login failed ({resp.status}): {login_body}")

        login_path = RECORDED / "login.json"
        login_path.write_text(json.dumps(_sanitize_login(login_body), indent=2) + "\n")

        token = str(login_body["access_token"])
        params: dict[str, str | int] = {"timeout": 0}
        if full_sync:
            params["full_state"] = "true"
        headers = {"Authorization": f"Bearer {token}"}
        async with session.get(sync_url, params=params, headers=headers) as resp:
            sync_body = await resp.json()
            if resp.status != 200:
                raise SystemExit(f"sync failed ({resp.status}): {sync_body}")

        sync_name = "sync_initial.json" if full_sync else "sync_incremental.json"
        (RECORDED / sync_name).write_text(json.dumps(sync_body, indent=2) + "\n")

        meta = {
            "homeserver": base,
            "user_id": login_body.get("user_id", user),
            "device_id": login_body.get("device_id"),
            "recorded_at": datetime.now(tz=UTC).isoformat(),
            "full_sync": full_sync,
        }
        (RECORDED / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    print(f"Wrote {login_path}")
    print(f"Wrote {RECORDED / sync_name}")
    print(f"Wrote {RECORDED / 'meta.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record Matrix fixtures from a live homeserver into recorded/."
    )
    parser.add_argument(
        "--full-sync",
        action="store_true",
        help="Record a full-state sync as sync_initial.json (default: incremental).",
    )
    args = parser.parse_args()
    env = _load_env_local()
    asyncio.run(
        _record(
            env["MATRIX_HOMESERVER"],
            env["MATRIX_USER"],
            env["MATRIX_PASSWORD"],
            full_sync=args.full_sync,
        )
    )


if __name__ == "__main__":
    main()
