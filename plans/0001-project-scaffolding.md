# 0001 — Project Scaffolding (baseline)

> **Status: DONE.** This document records the baseline that already exists so
> later plans build on a known foundation. If you are implementing a feature,
> you do not need to (re)do this — just verify the checklist still passes.

## Goal

Provide a runnable, lint/type/test-clean repository skeleton with all tooling,
CI, and docs in place so feature plans can be implemented with fast feedback.

## What exists

- **`pyproject.toml`** — `telemente` package, `hatchling` build backend, dynamic
  version from `src/telemente/__init__.py`, `telemente` console script, runtime
  deps (`textual`, `matrix-nio[e2e]`, `keyring`, `platformdirs`), dev group,
  and config for ruff, mypy (`strict`), pytest (`asyncio_mode = "auto"`,
  coverage), and the `olm` marker.
- **`uv.lock`** — committed; CI runs `uv lock --check`.
- **Tooling**: ruff (lint + format), mypy strict, pytest + pytest-asyncio +
  pytest-cov, aioresponses (HTTP mocking), textual-dev.
- **`.pre-commit-config.yaml`** — commit stage: ruff lint+format, mypy, file
  hygiene; pre-push stage: full pytest. Install with
  `uv run pre-commit install --install-hooks`.
- **`.github/workflows/ci.yml`** — `quality` (ruff, format check, mypy,
  `uv lock --check`) + `test` (matrix 3.11/3.12/3.13). Installs `libolm-dev`
  as a safety net (usually unnecessary — `python-olm` ships bundled wheels).
- **`.github/workflows/release.yml`** — on tag `v*`: `uv build` + attach
  sdist/wheel to a GitHub Release. PyPI Trusted Publishing seam left commented.
- **Package skeleton** under `src/telemente/` (see `AGENTS.md` for the map),
  with a placeholder `TelementeApp`.
- **`tests/`** — `conftest.py` (`tmp_store` fixture), `test_smoke.py` (version
  + app-boots). Mirror dirs `tests/matrix/`, `tests/tui/`.
- **Docs** — `README.md`, `AGENTS.md` (canonical), `CLAUDE.md` (pointer).

## Done-when (verification)

```bash
uv sync --all-extras --dev
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
uv run telemente        # launches placeholder TUI; press q to quit
```

All commands succeed; `python-olm`/`nio` e2e imports work
(`uv run python -c "import nio; from nio.crypto import OlmDevice"`).
