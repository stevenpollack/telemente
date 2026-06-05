# Plan 0018 — nio-native fixture recording

## Status: proposed

## Problem

`scripts/record_nio_fixtures.py` records Matrix HTTP responses using a bare
`aiohttp.ClientSession` that bypasses `matrix-nio` entirely. The consequences:

- Recorded fixtures capture the **raw HTTP wire format** the homeserver sends.
- Synthetic fixtures are hand-crafted guesses at what nio accepts.
- Fields that nio silently strips, normalises, or renames (e.g. `unsigned`,
  `age_ts`, extensible content fields) appear in recorded fixtures but not
  synthetic ones — the two tiers measure **different things**.
- A bug in nio's handling of such a field would be caught by recorded tests but
  missed by synthetic ones (or vice versa), making the tiers incomparable.
- There is no assertion that nio can actually parse the recorded response
  without error; the script only checks the HTTP status code.

## Goal

Replace the raw-aiohttp recorder with one that drives a real `nio.AsyncClient`
through its own HTTP session. The recorder should:

1. Inject a recording transport into nio — capturing what nio **actually sends
   and receives** — rather than speaking HTTP directly.
2. Assert `isinstance(resp, nio.LoginResponse)` / `isinstance(resp,
   nio.SyncResponse)` before writing, providing free round-trip verification.
3. Write fixtures that are guaranteed parseable by nio, not just syntactically
   valid JSON.

## Recommended approach: aiohttp TraceConfig injection

### Why, not alternatives

| Approach | Verdict |
|---|---|
| `vcrpy` / `pytest-recording` | Not installed; adds a dependency; intercepts at the urllib3/httpx layer, not aiohttp |
| Replace `aiohttp` with `respx` (httpx-based) | nio uses aiohttp internally; we cannot swap the transport without patching nio |
| Subclass `nio.AsyncClient.send()` | `send()` is private; fragile to nio internal changes |
| **aiohttp `TraceConfig` on `nio.client_session`** | **No new dependencies; officially supported aiohttp API; captures at exactly the right layer** |

### How it works

`nio.AsyncClient.client_session` is a plain `Optional[aiohttp.ClientSession]`
attribute, initialised to `None` on `__init__` (nio source: `async_client.py`
line ~431). It is lazily created by nio's internal `@client_session` decorator
on `AsyncClient.send()` only when still `None`.

**Injecting a pre-built session before the first call is safe and
deterministic.** We create a `ClientSession` with an `aiohttp.TraceConfig`
whose `on_request_end` callback captures `(url, method, response_body)` for
each request.

`TraceRequestEndParams` provides the live `ClientResponse`. Calling
`await response.read()` in the callback caches the body in `response._body`;
nio's downstream `parse_body()` call reads from that same cache — no body is
consumed twice.

### Concrete implementation

```python
import aiohttp
import asyncio
import json
from pathlib import Path
from typing import Any
import nio

RECORDED = Path("tests/fixtures/nio/recorded")
PLACEHOLDER_TOKEN = "RECORDED_PLACEHOLDER_TOKEN"


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
        session: aiohttp.ClientSession,
        ctx: aiohttp.TraceRequestEndParams,
        params: aiohttp.TraceRequestEndParams,
    ) -> None:
        body = await params.response.read()   # caches; nio can still read it
        try:
            payload = json.loads(body)
        except Exception:
            payload = body.decode()
        self._log.append({
            "method": params.method,
            "url": str(params.url),
            "status": params.response.status,
            "body": payload,
        })

    def last_body_for(self, url_fragment: str) -> dict[str, Any]:
        for entry in reversed(self._log):
            if url_fragment in entry["url"]:
                return dict(entry["body"])
        raise KeyError(f"No recorded response matching {url_fragment!r}")


async def _record(homeserver: str, user: str, password: str, *, incremental: bool) -> None:
    recorder = _Recorder()
    nio_client = nio.AsyncClient(homeserver, user)

    # Inject our recording session before nio lazily creates its own.
    nio_client.client_session = aiohttp.ClientSession(
        trace_configs=[recorder.trace_config()]
    )

    try:
        # Login — drives nio's own request path.
        resp = await nio_client.login(password, device_name="telemente-recorder")
        assert isinstance(resp, nio.LoginResponse), f"Login failed: {resp}"

        # Sync — drives nio's own sync path.
        sync_resp = await nio_client.sync(
            timeout=0,
            full_state=not incremental,
        )
        assert isinstance(sync_resp, nio.SyncResponse), f"Sync failed: {sync_resp}"
    finally:
        await nio_client.close()

    # Extract and sanitise.
    login_body = recorder.last_body_for("/_matrix/client/v3/login")
    sync_body = recorder.last_body_for("/_matrix/client/v3/sync")

    login_body["access_token"] = PLACEHOLDER_TOKEN
    login_body.pop("refresh_token", None)

    sync_name = "sync_incremental.json" if incremental else "sync_initial.json"
    RECORDED.mkdir(parents=True, exist_ok=True)
    (RECORDED / "login.json").write_text(json.dumps(login_body, indent=2) + "\n")
    (RECORDED / sync_name).write_text(json.dumps(sync_body, indent=2) + "\n")
    # ... write meta.json as before
```

### Key nio API entry points (verified against installed version)

| Symbol | Location | Notes |
|---|---|---|
| `nio.AsyncClient.client_session` | `nio/client/async_client.py` ~L431 | `Optional[aiohttp.ClientSession]`; assign before first call |
| `aiohttp.TraceConfig.on_request_end` | aiohttp public API | `TraceRequestEndParams.response` is the live `ClientResponse` |
| `response.read()` | aiohttp | Caches body in `_body`; safe to call before nio reads it |
| `nio.AsyncClient.login(password, device_name=…)` | public | Returns `LoginResponse | LoginError` |
| `nio.AsyncClient.sync(timeout=0, full_state=…)` | public | Returns `SyncResponse | SyncError` |

## Migration path

The old and new recorders can coexist during transition:

1. Rename current script to `scripts/record_nio_fixtures_raw.py` as a fallback.
2. Implement new `scripts/record_nio_fixtures.py` using the TraceConfig approach.
3. Delete `_raw` variant once the new recorder is verified on a live homeserver.

Existing synthetic fixtures remain unchanged — they are hand-crafted and serve
as deterministic unit-test inputs. The recorded tier supplements them with
real homeserver data.

## New test cases enabled

With nio-native recording, the following assertions become possible:

1. **Round-trip verified**: recorded login fixture is always parseable by the
   same nio version that captured it — `isinstance(resp, nio.LoginResponse)` is
   an assertion in the recorder, not just a hope.
2. **Normalisation-safe**: fields nio strips during parsing are absent from the
   recorded fixture by construction (they never make it out of `parse_body()`).
3. **Stub accuracy**: aioresponses stubs built from recorded fixtures provably
   elicit the same nio response objects as a live homeserver would.
4. **Version-pinned**: the `meta.json` can record the exact nio version used,
   flagging when a re-record is needed after a nio upgrade.

## Test cases to write

- `test_recorded_login_fixture_is_nio_loginresponse`: assert the fixture
  round-trips through nio and produces `LoginResponse` (not a dict check).
- `test_recorded_sync_fixture_has_known_rooms`: after replaying through real
  nio, `client.rooms()` matches the room IDs in `meta.json`.

## Dependencies

None — `aiohttp.TraceConfig` is already a transitive dependency via `matrix-nio`.
