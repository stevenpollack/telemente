# 0017 — Matrix Test Black-Box Refactor

**Status: done**

## Goal

Refactor `tests/matrix/test_client.py` (and related helpers) so unit tests
exercise `MatrixClient` through its **public API** and **observable behavior**,
not private attributes or methods (`_logged_in`, `_on_sync`, `_last_activity`,
etc.).  Aligns matrix-layer tests with the pattern already used for UI tests
(`FakeMatrixClient` mirrors the public surface only).

Secondary outcome: Pyright `reportPrivateUsage` diagnostics disappear from
test files without silencing rules in `pyrightconfig.json`.

## Motivation

`tests/matrix/test_client.py` grew as white-box tests during plan 0003:

- `client._logged_in = True` (36×) bypasses `login()` / `restore()` lifecycle.
- Callback tests call `client._on_room_message()` directly instead of the
  callback registered on the injected nio mock.
- Sync tests call `client._on_sync()` / `client._sync_loop()` and assert on
  `client._last_activity`, `client._task`, `client._initial_sync_done`.

These pass under mypy but violate common 2026 testing practice: **mock at
boundaries, assert on public contracts.**  They also bind tests to
implementation details that refactors should be free to change.

The UI layer already does this correctly via `tests/fakes.py::FakeMatrixClient`.

## Dependencies

- Plan 0003 (matrix client wrapper) — existing test file being refactored.
- Plan 0015 (nio type stubs) — `cast(nio.SyncResponse, …)` patterns stay.
- Plan 0016 (cassette integration tests) — Phase 3 may share JSON fixtures.

## Files to create / modify

```
plans/0017-matrix-test-blackbox-refactor.md   # this file
tests/matrix/helpers.py                     # shared fixtures & nio mock builders
tests/matrix/conftest.py                    # optional pytest fixtures (if needed)
tests/fakes.py                              # consolidate duplicate builders; FakeMatrixClient.set_homeserver()
tests/matrix/test_client.py                 # primary refactor target
tests/tui/test_login_sso.py                 # stop poking fake._fake_homeserver (Phase 1)
AGENTS.md                                   # document black-box testing rule (when done)
```

No changes to `src/telemente/matrix/client.py` unless a tiny public seam is
needed (prefer avoiding — use existing `restore()`, `subscribe()`, `rooms()`).

---

## Public testing contract

Tests may:

- Inject a mock/real `nio.AsyncClient` via `MatrixClient(..., nio_client=…)`.
- Call any **public** `MatrixClient` method.
- Subscribe via `subscribe()` and assert on emitted `ClientEvent` payloads.
- Assert on return values (`Session`, `list[RoomSummary]`, `list[Message]`, etc.).
- Use `aioresponses` for HTTP integration tests (unchanged).

Tests must **not**:

- Assign or read `MatrixClient._*` attributes.
- Call `MatrixClient._*` methods directly.
- Duplicate builder helpers that already live in `tests/fakes.py` / `helpers.py`.

Exception: `FakeMatrixClient` internal `_logged_in` is owned by the fake —
setting scripted state **on the fake object via its public methods** (`login()`,
`restore()`) is fine.  External access to `fake._fake_homeserver` is not.

---

## Phase 1 — Auth lifecycle & helper consolidation

**Status: done**

### Scope

1. Add `tests/matrix/helpers.py`:
   - Constants: `HOMESERVER`, `USER`, `PASSWORD`, `DEVICE_ID`, `TOKEN`.
   - `make_session() -> Session`.
   - `build_nio_mock(...) -> AsyncMock` (moved from `test_client.py`).
   - `async def restore_client(nio_mock, *, homeserver=…) -> MatrixClient` —
     constructs client, calls `await client.restore(session)`, returns client.
   - Nio payload builders: `make_nio_room`, `make_text_event`, `make_login_response`,
     `make_rooms_response`, `make_media_event`, `make_megolm_event` (consolidate
     with `tests/fakes.py` where identical; extend `fakes.py` builders if needed).

2. Replace every `client._logged_in = True` in `test_client.py` with
   `client = await restore_client(nio_mock)` (or `await client.login(…)` where
   login itself is under test).

3. Delete unused `_make_login_error()`.

4. Refactor `test_seed_last_activity_*` to assert via `rooms()[].last_activity`
   (public) instead of `client._last_activity`.

5. Refactor `test_seed_last_activity_does_not_overwrite_existing` to seed sync
   timestamp via public `messages()` backfill path instead of writing
   `_last_activity` directly.

6. Refactor `test_start_sync_and_close` to verify cancellation via a mock
   `sync_forever` coroutine that records `CancelledError`, not `client._task`.

7. Add `FakeMatrixClient.set_homeserver(url: str)`; update `test_login_sso.py`
   `_tracking_factory` to use it instead of `fake._fake_homeserver = …`.

### Tests touched (Phase 1)

All tests currently using `_logged_in = True` (~36), plus:
- `test_seed_last_activity_pre_seeds_missing_entries`
- `test_seed_last_activity_does_not_overwrite_existing`
- `test_start_sync_and_close`

### Done-when (Phase 1)

- [x] Zero `client._logged_in =` in `tests/matrix/test_client.py`.
- [x] Zero `client._last_activity` reads/writes in seed tests.
- [x] Zero `client._task` reads in `test_start_sync_and_close`.
- [x] `_make_login_error` removed.
- [x] Builders live in `helpers.py` / `fakes.py`, not duplicated in test file.
- [x] `uv run pytest tests/matrix/test_client.py -n auto` passes.
- [x] `uv run mypy` passes.
- [x] `npx pyright tests/matrix/test_client.py` — no `reportPrivateUsage` from
      Phase 1 scope (sync/callback privates may remain until Phase 2/3).

### Commit message

`test(matrix): phase 1 — restore() lifecycle, public seed assertions`

---

## Phase 2 — Event callbacks via nio registration

**Status: done**

### Scope

Replace direct calls to `MatrixClient` private nio callbacks with invocation of
the handlers **registered on the mock** during `restore()`:

```python
client = await restore_client(nio_mock)
callback = event_callback_for(nio_mock, nio.RoomMessageText)
await callback(room, event)
```

Add to `helpers.py`:

- `event_callback_for(nio_mock, event_type) -> Callable`
- `response_callback_for(nio_mock, response_type) -> Callable`

Both scan `nio_mock.add_event_callback` / `add_response_callback` call args
recorded by the `AsyncMock`.

### Tests to refactor (Phase 2)

| Test | Was | Becomes |
|------|-----|---------|
| `test_subscribe_receives_new_message` | `_on_room_message` | captured text callback |
| `test_unsubscribe` | `_on_room_message` | captured text callback |
| `test_on_room_media_emits_new_message` | `_on_room_media` | captured media callback |
| `test_on_megolm_event_emits_placeholder_and_requests_key` | `_on_megolm_event` | captured megolm callback |
| `test_on_megolm_event_request_key_failure_does_not_propagate` | `_on_megolm_event` | captured megolm callback |
| `test_update_last_activity_populates_cache` | `_update_last_activity` | `response_callback_for(SyncResponse)` then `rooms()` |
| `test_on_sync_skips_rooms_changed_when_nothing_changed` | `_on_sync` ×3 | `response_callback_for(SyncResponse)` |
| `test_on_sync_uploads_keys_when_needed` | `_on_sync` | response callback |
| `test_on_sync_queries_keys_when_needed` | `_on_sync` | response callback |
| `test_on_sync_keys_upload_error_does_not_propagate` | `_on_sync` | response callback |

### Done-when (Phase 2)

- [x] Zero `client._on_room_*` / `client._on_sync` / `client._update_last_activity`
      calls in `test_client.py`.
- [x] `test_update_last_activity_populates_cache` asserts via `rooms()[].last_activity`.
- [x] All Phase 2 tests still pass.

### Commit message

`test(matrix): phase 2 — invoke nio callbacks via registration capture`

---

## Phase 3 — Sync lifecycle via public API

**Status: done**

### Scope

Replace direct `_sync_loop()` invocation and internal task polling with public
`start_sync()` / `close()` and behavioral assertions.

| Test | Was | Becomes |
|------|-----|---------|
| `test_sync_loop_emits_cached_rooms_immediately` | `create_task(_sync_loop())` | `await start_sync()` + subscribe; assert `RoomsChanged` for cached rooms; `await close()` |
| `test_sync_loop_handles_sync_exception` | `_sync_loop` + `_initial_sync_done` | `start_sync()` with failing `nio_mock.sync`; assert `sync_forever` reached (event), no hang; `close()` |
| `test_close_cancels_poll_task` | `_rooms_poll_task.done()` | `start_sync()` + `close()`; assert clean shutdown (no dangling tasks / `close` completes) |

May add `tests/matrix/fixtures/sync_minimal.json` stub payloads (shared with
plan 0016) if needed for richer sync integration; keep Phase 3 unit-level with
mocks unless a small JSON fixture clearly helps.

### Done-when (Phase 3)

- [x] Zero `client._sync_loop`, `client._task`, `client._initial_sync_done`,
      `client._rooms_poll_task` access in `test_client.py`.
- [x] `npx pyright tests/matrix/test_client.py` — zero `reportPrivateUsage`.
- [x] `npx pyright tests/tui/test_login_sso.py` — zero `reportPrivateUsage`.
- [x] Full `uv run pytest -n auto` passes.
- [x] `AGENTS.md` updated with black-box matrix testing rule.

### Commit message

`test(matrix): phase 3 — sync lifecycle via start_sync/close`

---

## Progress log

| Date | Phase | Commit | Notes |
|------|-------|--------|-------|
| 2026-06-05 | — | `00d8168` | Plan written |
| 2026-06-05 | 1–3 | (this commit) | All phases implemented in one pass |

---

## Mocking strategy (unchanged)

- Unit tests: inject `AsyncMock(spec=nio.AsyncClient)` or real nio with mocked
  methods; never hit a real homeserver.
- Integration tests: `aioresponses` + real `nio.AsyncClient` (existing tests
  unchanged).
- UI tests: `FakeMatrixClient` only.

## Done-when (overall)

- [x] All three phases complete; progress log filled in.
- [x] `uv run pytest -n auto` passes.
- [x] `uv run mypy` passes.
- [x] `npx pyright tests/matrix/test_client.py tests/tui/test_login_sso.py` — 0
      `reportPrivateUsage` errors.
- [x] `AGENTS.md` documents the black-box rule for matrix unit tests.
