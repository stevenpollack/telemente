# CLAUDE.md

This file points Claude Code (and other agents) at the canonical engineering
guide.

➡️ **Read [`AGENTS.md`](AGENTS.md) first.** It contains the architecture,
invariants, TDD workflow, testing/mocking rules, and the `plans/` process.

## TL;DR

- Python + Textual TUI for Matrix; `uv` for environment management.
- **The UI never calls matrix-nio directly** — only via
  `telemente.matrix.client.MatrixClient`. No nio types cross that boundary.
- **TDD always**: write the failing test first (each `plans/*.md` lists test
  cases), then implement. Never hit a real homeserver — use `aioresponses` or
  `tests/fakes.py::FakeMatrixClient`.
- Fast feedback: `uv run ruff check .` · `uv run ruff format .` ·
  `uv run mypy` · `uv run pytest`.
- `mypy --strict` must pass; no `print` (use `logging`).
- Feature specs live in [`plans/`](plans/); implement them in order.
- E2EE needs system **libolm** (see [`README.md`](README.md)).
