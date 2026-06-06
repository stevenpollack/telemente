# Test Suite Overview

Tests live in two tiers that must never be mixed:

| Tier | Directory | What it tests | Mocking layer |
|---|---|---|---|
| **Matrix unit** | `tests/matrix/` | `MatrixClient` public API | `aioresponses` + JSON cassettes in `tests/fixtures/nio/synthetic/` |
| **TUI integration** | `tests/tui/` | Textual widgets and screens | `FakeMatrixClient` from `tests/fakes.py` |

Shared fixtures live in `tests/conftest.py`. There is no `tests/tui/conftest.py`;
TUI tests import helpers directly from `conftest` using `from conftest import
wait_for_workers`.

Top-level tests (`test_config.py`, `test_smoke.py`) cover config logic and basic
app instantiation and belong to neither tier.

---

## Tier 1 — Matrix unit tests (`tests/matrix/`)

These tests exercise the real matrix-nio parsing stack. HTTP is stubbed at the
`aiohttp` level using `aioresponses`; cassettes in
`tests/fixtures/nio/synthetic/` contain deterministic JSON payloads with fixed
room IDs and timestamps.

**Rules:**
- Only the public `MatrixClient` API is called. No `client._on_*`, no private
  attribute writes.
- Use `matrix.helpers.restore_client()` for authenticated setup.
- Use `matrix.helpers.stub_sync()` / `start_sync_with_stubs()` for sync replay.
- Use `matrix.helpers.stub_get/post/put/delete` instead of calling
  `aioresponses` directly — keeps Pyright clean.
- Assertions go on public API return values (`rooms()`, `RoomSummary.last_activity`,
  etc.), not on `MatrixClient._*` attributes.
- Do **not** import `nio` directly in test files.

**Running:**
```bash
uv run pytest tests/matrix/
```

**Recorded cassettes** (`tests/fixtures/nio/recorded/`) are gitignored and not
run in CI. Run them locally with:
```bash
uv run pytest -m recorded -n 0
```

---

## Tier 2 — TUI tests (`tests/tui/`)

These tests exercise Textual widgets and screens entirely in-process with a
headless driver. No network; no nio. See `tests/tui/README.md` for detail.

**Running:**
```bash
uv run pytest tests/tui/
```

---

## Shared fixtures (`tests/conftest.py`)

### `wait_for_workers(app)`

```python
from conftest import wait_for_workers
await wait_for_workers(app)
```

Waits for all Textual background workers to finish, then drains the message
queue twice. Use this instead of chains of `await pilot.pause()` whenever you
are waiting for a `run_worker` call to complete. Fixed pause chains are flaky
on loaded CI machines and hide race conditions on fast developer machines.

**Do not use `asyncio.sleep()` in TUI tests.** It is wall-clock dependent and
semantically wrong — wait for the app to reach a stable state, not for a fixed
number of milliseconds.

### `tmp_store`

```python
def test_foo(tmp_store: Path) -> None: ...
```

A temporary `Path` standing in for telemente's XDG data/store directory. Use
in config and credential tests to avoid touching real user data.

---

## `asyncio_mode = "auto"`

`pyproject.toml` sets `asyncio_mode = "auto"` for pytest-asyncio. This means:

- **Do not add `@pytest.mark.asyncio` to individual tests.** It is a no-op when
  auto mode is active and is a code smell that indicates an older-pattern test.
- All `async def test_*` functions are automatically treated as asyncio tests.

---

## Key files

| File | Purpose |
|---|---|
| `tests/fakes.py` | `FakeMatrixClient` and helper builders |
| `tests/conftest.py` | `wait_for_workers`, `tmp_store` |
| `tests/matrix/helpers.py` | `restore_client`, `stub_sync`, `stub_get/post/put/delete`, `load_fixture` |
| `tests/fixtures/nio/synthetic/` | Deterministic JSON cassettes for matrix tests |
| `tests/fixtures/nio/README.md` | Cassette format documentation |
