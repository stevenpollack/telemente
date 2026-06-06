# `src/telemente/matrix/` — protocol boundary layer

This package is the **sole place in the codebase that imports `nio`**. Its job
is to translate between the Matrix protocol (via matrix-nio) and the
application's internal model types, so the TUI and tests never depend on
network, nio types, or I/O.

See `AGENTS.md §Core invariants` for the formal contracts this layer upholds.

## Purpose

Wraps `matrix-nio`'s `AsyncClient` behind `MatrixClient`, exposes a stable
async API for auth, sync, queries, and actions, and emits typed `ClientEvent`
objects to subscribers. Nothing outside this package should know that
matrix-nio exists.

## Key design decisions

**Hard boundary: no nio types cross the package edge.** `MatrixClient` returns
only `RoomSummary`, `Message`, and `Member` from `models.py`. Any nio response
or event object is consumed here and converted before the result is returned or
emitted. This is what makes `FakeMatrixClient` in `tests/fakes.py` possible:
the TUI only depends on the `MatrixClient` public API, which the fake mirrors
exactly.

**`nio.AsyncClient` is injected for testing.** The constructor accepts an
optional `nio_client` parameter. Test helpers pass a pre-configured client with
`aioresponses` stubs; production builds let `MatrixClient` construct its own.
This is the only DI seam into the nio layer — callers above never build their
own `nio.AsyncClient`.

**`sync()` does not run response callbacks; `sync_forever` does.** After a
manual `sync()` call (used for the initial full-state sync), `_on_sync` is
called explicitly to mirror what `sync_forever`'s response-callback pipeline
would do. Missing this call means `_last_activity` is never populated and rooms
appear unsorted. See the comment in `_sync_loop`.

**Optimistic tag overrides.** `set_room_tag` / `remove_room_tag` record the
mutation locally in `_tag_overrides` / `_removed_tag_overrides` and re-emit
`RoomsChanged` immediately. When the next sync response arrives with an
authoritative `m.tag` account-data event, the overrides are cleared. This
prevents the UI from flickering back to the old state between the HTTP call and
the next sync.

**RoomsChanged is fingerprinted before emit.** `_on_sync` computes a
`frozenset` of `(room_id, display_name, unread_count)` tuples and skips the
emit when nothing has changed. This prevents spurious re-renders on noisy sync
responses. See `AGENTS.md §Performance guidelines`.

**TOFU E2EE.** Before each encrypted send, `_tofu_trust_room` marks all
devices in the room as verified. This is not MITM-safe; see the module
docstring for the full caveat.

**`MessageCache` is an optional write-through SQLite cache** (`cache.py`).
Warm rooms are served from SQLite without a network call. Cold rooms fall back
to `room_messages`. The cache is keyed by `(room_id, event_id)` and silently
disabled on open failure.

**SSO loopback server** (`sso.py`) binds on `127.0.0.1` at an ephemeral port
with a random nonce path so only the redirected browser tab can complete the
flow. The `loginToken` is single-use and never logged.

## File map

| File | Role |
|------|------|
| `client.py` | `MatrixClient` — the only nio code; `ClientEvent` union type |
| `models.py` | `RoomSummary`, `Message`, `Member` — the only types that cross the boundary |
| `auth.py` | `LoginFlows`, `IdentityProvider`, `parse_login_flows`, `build_sso_redirect_url` |
| `sso.py` | `SsoCallbackServer` — loopback HTTP server for SSO token capture |
| `discovery.py` | MXID / server-name / `.well-known` resolution to a homeserver base URL |
| `cache.py` | `MessageCache` — async SQLite write-through cache for `Message` objects |
| `sort.py` | `sort_rooms_by_recency` — pure sort function shared with tests |
| `__init__.py` | Package docstring only |

## Patterns used

**Subscriber pattern for `ClientEvent`.** `MatrixClient.subscribe(handler)`
appends a handler to `_handlers` and returns an unsubscribe callable. The
`TelementeApp` holds the returned callable and calls it on exit. This keeps the
client decoupled from Textual — it emits plain Python callables, not Textual
messages.

**Structural protocols for DI.** Each screen and widget defines a `_*Client`
protocol naming exactly the `MatrixClient` methods it uses. The real
`MatrixClient` satisfies every such protocol implicitly. `FakeMatrixClient`
satisfies them explicitly. Pyright validates both at write-time.

**Progressive room list during initial sync.** `_poll_rooms_during_sync` runs
as a background task and emits lightweight `RoomsChanged` updates as nio
populates `self._client.rooms` during a slow full-state sync. The full
`_update_last_activity` scan runs only once the `SyncResponse` callback fires.

## What lives elsewhere

- TUI reaction to `ClientEvent` → `tui/app.py` (`_on_client_event`)
- Session and path resolution → `telemente/config.py`
- nio type stubs → `stubs/nio/` (project root) — extend these, never add
  `# type: ignore` for a nio type
- Test cassettes → `tests/fixtures/nio/synthetic/`
- Test helpers → `tests/matrix/helpers.py`
