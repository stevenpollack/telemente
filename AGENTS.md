# AGENTS.md — guide for AI agents & contributors

This is the canonical engineering guide for working on **telemente**. Read it
fully before making changes. (`CLAUDE.md` is a short pointer to this file.)

## What telemente is

A terminal (TUI) chat client for the Matrix protocol. Python + Textual for the
UI; matrix-nio for the protocol. Managed with `uv`. Public GitHub repo,
distributed via GitHub Releases.

## Architecture

```
cli.py            Entry point: parse args, launch TelementeApp.
config.py         XDG paths, settings, secure credential storage.
matrix/
  client.py       MatrixClient: the ONLY code that talks to matrix-nio.
  models.py       Plain dataclasses (RoomSummary, Message, Member) — no nio types leak out.
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
- **Textual tests** use `async with app.run_test() as pilot:` and drive the UI
  with `pilot.press(...)`, `pilot.click(...)`, and `await pilot.pause()` to let
  messages settle. Assert via `app.query_one(...)`.
- **E2EE tests** are marked `@pytest.mark.olm` and skip when libolm is absent;
  CI installs `libolm-dev`.
- Tests mirror the package: `tests/matrix/`, `tests/tui/`.

## Fast feedback loop

```bash
uv run ruff check .      # lint  (also runs on commit via pre-commit)
uv run ruff format .     # format
uv run mypy              # strict types (runs on commit)
uv run pytest            # tests  (runs on push via pre-commit pre-push stage)
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

Plan order: `0001 → 0002 → 0003 → 0004 → 0005 → 0006/0007/0008 → 0009 → 0010`.

## Dependencies

Runtime: `textual`, `matrix-nio[e2e]`, `keyring`, `platformdirs`.
Dev: `pytest(+asyncio,+cov)`, `aioresponses`, `ruff`, `mypy`, `pre-commit`,
`textual-dev`.

E2EE requires the system library **libolm** (see README for install commands).

## Commits & PRs

- Keep commits focused; do not commit if ruff/mypy/pytest fail.
- Conventional-style messages are encouraged (`feat:`, `fix:`, `test:`,
  `chore:`, `docs:`).
- Do not commit secrets, access tokens, or the local `.telemente/` store.
