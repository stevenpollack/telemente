#!/usr/bin/env python3
"""Record Matrix fixtures by driving a real nio.AsyncClient (plan 0018).

Uses aiohttp TraceConfig injection so recorded fixtures are guaranteed
parseable by the same nio version that captured them.

Reads credentials from ``.env.local`` (copy from ``.env.local.example``).
Tokens are sanitized before write. CI never runs this — replay uses aioresponses.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import aiohttp
import nio

ROOT = Path(__file__).resolve().parent.parent
RECORDED = ROOT / "tests" / "fixtures" / "nio" / "recorded"
ENV_FILE = ROOT / ".env.local"
PLACEHOLDER_TOKEN = "RECORDED_PLACEHOLDER_TOKEN"

_log = logging.getLogger(__name__)


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


class _Recorder:
    """Captures aiohttp requests/responses via TraceConfig."""

    def __init__(self) -> None:
        self._log: list[dict[str, Any]] = []

    def trace_config(self) -> aiohttp.TraceConfig:
        tc = aiohttp.TraceConfig()
        tc.on_request_end.append(self._on_request_end)
        return tc

    async def _on_request_end(
        self,
        _session: aiohttp.ClientSession,
        _ctx: Any,
        params: aiohttp.TraceRequestEndParams,
    ) -> None:
        # Read and cache the body so nio can still read it downstream.
        body_bytes = await params.response.read()
        try:
            payload: Any = json.loads(body_bytes)
        except Exception:
            payload = body_bytes.decode()
        self._log.append(
            {
                "method": params.method,
                "url": str(params.url),
                "status": params.response.status,
                "body": payload,
            }
        )

    def last_body_for(self, url_fragment: str) -> dict[str, Any]:
        for entry in reversed(self._log):
            if url_fragment in entry["url"]:
                body = entry["body"]
                if not isinstance(body, dict):
                    raise TypeError(
                        f"Response body for {url_fragment!r} is not a JSON object: {body!r}"
                    )
                return cast(dict[str, Any], body)
        raise KeyError(f"No recorded response matching {url_fragment!r}")


async def _record(homeserver: str, user: str, password: str, *, full_sync: bool) -> None:
    recorder = _Recorder()
    nio_client = nio.AsyncClient(homeserver, user)
    nio_client.client_session = aiohttp.ClientSession(trace_configs=[recorder.trace_config()])

    try:
        resp = await nio_client.login(password, device_name="telemente-recorder")
        if not isinstance(resp, nio.LoginResponse):
            raise SystemExit(f"Login failed: {resp}")

        sync_resp = await nio_client.sync(timeout=0, full_state=full_sync)
        if not isinstance(sync_resp, nio.SyncResponse):
            raise SystemExit(f"Sync failed: {sync_resp}")
    finally:
        await nio_client.close()

    login_body = recorder.last_body_for("/_matrix/client/")
    # Sanitise secrets.
    login_body["access_token"] = PLACEHOLDER_TOKEN
    login_body.pop("refresh_token", None)

    sync_body = recorder.last_body_for("/_matrix/client/v3/sync")
    sync_name = "sync_initial.json" if full_sync else "sync_incremental.json"

    RECORDED.mkdir(parents=True, exist_ok=True)
    login_path = RECORDED / "login.json"
    sync_path = RECORDED / sync_name
    meta_path = RECORDED / "meta.json"

    login_path.write_text(json.dumps(login_body, indent=2) + "\n")
    sync_path.write_text(json.dumps(sync_body, indent=2) + "\n")

    meta: dict[str, Any] = {
        "homeserver": homeserver.rstrip("/"),
        "user_id": login_body.get("user_id", user),
        "device_id": login_body.get("device_id"),
        "recorded_at": datetime.now(tz=UTC).isoformat(),
        "full_sync": full_sync,
        "nio_version": getattr(nio, "__version__", "unknown"),
        "room_ids": list(sync_resp.rooms.join.keys()),
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    _log.info("Wrote %s", login_path)
    _log.info("Wrote %s", sync_path)
    _log.info("Wrote %s", meta_path)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
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
