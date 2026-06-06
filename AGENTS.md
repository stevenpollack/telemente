# AGENTS.md — guide for AI agents & contributors

This is the canonical engineering guide for working on **telemente**. Read it
fully before making changes. (`CLAUDE.md` is a short pointer to this file.)

## What telemente is

A terminal (TUI) chat client for the Matrix protocol. Python + Textual for the
UI; matrix-nio for the protocol. Managed with `uv`. Public GitHub repo,
distributed via GitHub Releases.

Core MVP is complete: login, sync, three-panel layout, tabbed messaging,
command palette, E2EE (plan 0010), SSO (plan 0011). Active work is on
performance (plan 0012: OptionList migration) and polish.

## Architecture

```
cli.py            Entry point: parse args, launch TelementeApp.
config.py         XDG paths, settings, secure credential storage.
matrix/
  client.py       MatrixClient: the ONLY code that talks to matrix-nio.
  models.py       Plain dataclasses (RoomSummary, Message, Member) — no nio types leak out.
stubs/nio/        Partial inline type stubs for matrix-nio (plan 0015).
tui/
  app.py          TelementeApp(App): owns the MatrixClient, routes events.
  screens/        login.py, main.py
  widgets/        room_list.py, message_view.py, member_list.py
  styles/app.tcss Styling.
```

### Core invariants (do not violate)

1. **The UI never imports or calls `nio` directly.** All protocol access goes
   through `telemente.matrix.client.MatrixClient`. Widgets/screens receive a
   `MatrixClient` (or a fake) by injection. This is what keeps the UI testable
   without a network.
2. **No `nio` types cross the boundary.** `MatrixClient` returns the dataclasses
   in `matrix/models.py`, never raw nio response/event objects.
3. **Everything is async on one event loop.** Textual and matrix-nio share the
   same asyncio loop. Run the sync loop as a Textual worker; never spin up a
   second loop or a background thread for I/O.
4. **Fully typed.** `mypy --strict` must pass. No `Any` leaks; no bare
   `# type: ignore` without a reason.
5. **No `print`.** Use `logging`. Stdout belongs to Textual.
6. **Every user-facing feature must be reachable via the command palette.**
   The command palette (`Ctrl+P`, implemented in `tui/commands.py`) is the
   canonical source of truth for what the app can do. Keybindings are
   shortcuts to palette commands, not replacements. When you add a feature,
   add a corresponding `DiscoveryHit` and `search()` entry in
   `TelementeCommands` at the same time — the two ship together.

## Test-driven development (required)

Every change follows: **write a failing test → implement → make it green →
ruff + mypy clean.** Each `plans/*.md` document lists its test cases first.

### Testing rules

- **Never hit a real homeserver.** Two mocking layers:
  - **Unit tests** inject a fake/mock into the unit under test. `MatrixClient`
    accepts an optional pre-built nio client for this (dependency injection).
    For the UI, use `tests/fakes.py::FakeMatrixClient`.
  - **Integration tests** for `MatrixClient` stub HTTP with **`aioresponses`**
    (matrix-nio uses aiohttp), exercising real nio request/response parsing.
- **Textual tests** use `async with app.run_test(size=(120, 40)) as pilot:` and
  drive the UI with `pilot.press(...)`, `pilot.click(...)`, and
  `await wait_for_workers(app)` (from `tests/conftest.py`) to let workers and
  messages settle. Never use `await pilot.pause(); await pilot.pause()` chains
  or `asyncio.sleep()` — use `wait_for_workers` instead. Assert via
  `app.query_one(...)`. Always assert `app.focused.__class__.__name__` before
  pressing a key that requires a specific widget to have focus. Use
  `message_hook=messages.append` in `run_test()` for cross-widget message
  assertions. Do not use `@pytest.mark.asyncio` — `asyncio_mode = "auto"` is
  set in `pyproject.toml`. See `tests/tui/README.md` for the full golden rules.
- **E2EE tests** are marked `@pytest.mark.olm` and skip when libolm is absent;
  CI installs `libolm-dev`.
- Tests mirror the package: `tests/matrix/`, `tests/tui/`.
- **Matrix unit tests use the public `MatrixClient` API only** (plan 0017).
  Use `matrix.helpers.restore_client()` for authenticated setup; invoke nio
  callbacks via `event_callback_for()` / `response_callback_for()` after
  `restore()` registers them.  Do not assign `client._logged_in` or call
  `client._on_*` directly.
- **HTTP integration tests** use typed `matrix.helpers.stub_get/post/put/delete`
  instead of calling `aioresponses` methods directly (keeps Pyright clean).

### Nio cassette fixtures (plan 0016)

Matrix integration tests replay **JSON cassettes** through **real matrix-nio**
with **`aioresponses`** — no live homeserver in CI. See
[`tests/fixtures/nio/README.md`](tests/fixtures/nio/README.md).

| Tier | Path | CI | Purpose |
|------|------|----|---------|
| **Synthetic** | `tests/fixtures/nio/synthetic/` | Yes | Deterministic behavioral tests (`test_client.py`); fixed room IDs and timestamps |
| **Recorded** | `tests/fixtures/nio/recorded/` | No (gitignored) | Optional smoke tests (`@pytest.mark.recorded`); real Synapse shapes from a live capture |

- Load cassettes via `matrix.helpers.load_fixture(name, tier="synthetic"|"recorded")`.
- Use `matrix.helpers.stub_sync()` / `start_sync_with_stubs()` for sync replay;
  assert on the **public API** (`rooms()`, `RoomSummary.last_activity`), not
  `MatrixClient._*` attributes.
- **Recording** (local only): copy `.env.local.example` → `.env.local`, then
  `uv run python scripts/record_nio_fixtures.py --full-sync` (and again without
  `--full-sync` for incremental). Tokens are sanitized on write. Run smoke tests
  with `uv run pytest -m recorded -n 0`.

## Fast feedback loop

```bash
uv run ruff check .      # lint  (also runs on commit via pre-commit)
uv run ruff format .     # format
uv run mypy              # strict types (runs on commit)
pyright src/             # strict Pyright (validates nio stubs against client.py)
uv run pytest            # tests, parallel workers auto-detected (-n auto is the default)
uv run pytest --cov=telemente --cov-report=term-missing  # coverage (opt-in, slow)
```

Install hooks once: `uv run pre-commit install --install-hooks`.
- **commit** stage: ruff lint+format, mypy, file hygiene (fast).
- **pre-push** stage: full pytest run (the build-breaking gate).

## The plans/ workflow

Feature work is specified in numbered `plans/*.md` files. Each is
self-contained: **Goal · Files · Public interface · Behavior · Test cases ·
Mocking strategy · Done-when · Dependencies.** When implementing a plan:

1. Read the plan and its dependency plans.
2. Write the listed tests first (they should fail).
3. Implement until green, keeping the invariants above.
4. Run the full fast-feedback loop.
5. Update the plan's status / check off the Done-when list.

Plan order: `0001 → … → 0012 → 0015 (nio stubs) → …`.

## Dependencies

Runtime: `textual`, `matrix-nio[e2e]`, `keyring`, `platformdirs`.
Dev: `pytest(+asyncio,+cov)`, `aioresponses`, `ruff`, `mypy`, `pre-commit`,
`textual-dev`, `watchfiles`.

Two convenience scripts are registered for development:

```bash
# Hot-reload the app (restarts on any src/telemente/ change, textual --dev enabled)
uv run telemente-dev

# Textual devtools console — open in a second terminal alongside telemente-dev
uv run telemente-console
```

E2EE requires the system library **libolm** (see README for install commands).

### matrix-nio type stubs (plan 0015)

`matrix-nio` ships no `py.typed` marker.  Partial stubs in `stubs/nio/` cover
exactly the API surface `matrix/client.py` uses so mypy and Pyright catch
wrong attribute access at write-time (e.g. `MatrixRoom.timeline`, which lives
on `RoomInfo` in sync responses, not on the in-memory room object).

When upgrading `matrix-nio`, audit API changes and update the stubs:

```bash
uv run mypy && pyright src/telemente/matrix/client.py
```

To inspect dependency source — signatures, docstrings, internal behaviour —
**read the files directly** under `.venv/`:

```
.venv/lib/python*/site-packages/nio/
```

Use the `Read` tool (or `grep`/`find` via Bash) on those paths. No need to
write a Python helper script just to call `help()` or `inspect`.

### Reading Python source (required)

Never use `sed`, `awk`, `head`, `tail`, or `cat` to read `.py` files.
Use one of these instead, in order of preference:

1. **`Read` tool** — for any `.py` file, including those under `.venv/`.
2. **LSP** (`go to definition` / `hover`) — to jump directly to a symbol
   without knowing its file path.
3. **`grep`/`find` via Bash** — only to locate a file or symbol before
   reading it with `Read`.

This applies to project source, test files, stubs, and third-party packages
under `.venv/`.

Do not add `# type: ignore` for nio types — extend the stubs instead.

## Performance guidelines

Textual re-renders the full widget subtree on any DOM mutation. Keep the UI
snappy by following these patterns (already in place — don't regress them):

- **Fingerprint `RoomsChanged` at the source.** `MatrixClient._on_sync`
  compares a `frozenset` of `(room_id, display_name, unread_count)` before
  emitting; skip if nothing changed.
- **Debounce search input.** `RoomList.on_input_changed` waits 150 ms via
  `set_timer` before calling `_rebuild`. Cancel the pending timer on each
  new keystroke.
- **Surgical unread patches.** `RoomList.update_unread` mutates a single
  `_RoomItem` label in place instead of rebuilding the whole `ListView`.
  `MainScreen.handle_new_message` calls it, not `set_rooms`.
- **Avoid full rebuilds on tab switches.** Worker exclusivity
  (`run_worker(..., exclusive=True)`) serialises concurrent room selections.
- **Next target (plan 0012):** replace `ListView` with `OptionList` to get
  `replace_option_prompt` for zero-teardown option updates.

## Logging

telemente logs to a rotating file (never stdout — Textual owns the terminal).
Configuration lives in `cli.py::_configure_logging`.

| Item | Value |
|------|-------|
| Default log file | `~/.local/share/telemente/telemente.log` |
| Max size | 5 MB, 3 backups (`RotatingFileHandler`) |
| Default level | `INFO` |
| Override | `--log-level DEBUG` / `--log-file PATH` |

Level conventions:

- `DEBUG` — internal state, per-event noise, message body previews (truncated
  to ~60 chars). Never log full message bodies, tokens, or passwords at any
  level.
- `INFO` — user-visible transitions: login, sync start/stop, room selected,
  message sent, session restored.
- `WARNING` — degraded but recoverable: failed network call, cache miss.
- `ERROR` — user-facing failures that require action.

**When debugging, always read the log first.** Before adding print statements
or guessing at root causes, run the app with `--log-level DEBUG` and
`tail -f ~/.local/share/telemente/telemente.log`. The log records every sync
event, room change, message dispatch, and worker lifecycle — most bugs are
immediately visible there. Only add new `logger.*` calls if the relevant code
path has no coverage; do not add them as a temporary debugging aid.

## Commits & PRs

- Keep commits focused; do not commit if ruff/mypy/pytest fail.
- Conventional-style messages are encouraged (`feat:`, `fix:`, `test:`,
  `chore:`, `docs:`).
- Do not commit secrets, access tokens, or the local `.telemente/` store.
