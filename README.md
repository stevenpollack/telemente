# telemente

A terminal-based chat client for the [Matrix](https://matrix.org) protocol,
built with [Textual](https://textual.textualize.io/) and
[matrix-nio](https://github.com/matrix-nio/matrix-nio).

> **Status: feature-complete MVP.** Core TUI, login, sync, messaging, and
> room management are implemented and tested. E2EE and SSO are in separate
> plans. See [`plans/`](plans/) for the full roadmap.

## Features (implemented)

- **Three-panel TUI** (collapsible panels):
  - **Left:** searchable/filterable room list — debounced search, unread
    badges, encryption indicator, tag decorators (favourite/low-priority/mute),
    sort by recent activity or alphabetically.
  - **Center:** tabbed message views (up to 8 rooms open simultaneously, LRU
    eviction), multi-line composer (Enter sends, Shift+Enter newlines), reply /
    react / edit / redact, unread notifications.
  - **Right:** live member list for the active room.
- Interactive login to any Matrix homeserver.
- Live sync — rooms update in real-time; departed rooms close their tabs
  automatically.
- Command palette (`Ctrl+P`) for all features.
- End-to-end encryption (E2EE) — implemented in plan 0010 (requires libolm).
- SSO login — implemented in plan 0011.

## Requirements

- Python **3.11+**
- [`uv`](https://docs.astral.sh/uv/) for environment management
- **`libolm`** — a C library used for end-to-end encryption. On most platforms
  it ships **bundled inside the `python-olm` wheel**, so you do not need to
  install anything. You only need a system `libolm` if `python-olm` has no
  prebuilt wheel for your platform and must build from source.

### Installing libolm (only if building python-olm from source)

| OS | Command |
|----|---------|
| Debian/Ubuntu | `sudo apt-get install -y libolm-dev` |
| Fedora | `sudo dnf install -y libolm-devel` |
| Arch | `sudo pacman -S libolm` |
| macOS (Homebrew) | `brew install libolm` |
| Windows | Use WSL2 and follow the Debian/Ubuntu steps |

If E2EE fails to import (`ImportError` from `olm`/`python-olm`), install the
system `libolm` above and reinstall: `uv sync --reinstall-package python-olm`.

## Quickstart

```bash
# 1. Clone the repo
git clone https://github.com/telemente/telemente
cd telemente

# 2. Create the environment and install dependencies
#    (libolm is bundled in the python-olm wheel on most platforms; see above)
uv sync

# 3. Run the app
uv run telemente
```

## Development

```bash
# Install dev dependencies (included by default with `uv sync`)
uv sync --all-extras --dev

# Wire up git hooks (fast checks on commit, tests on push)
uv run pre-commit install --install-hooks

# Fast feedback loop
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy                  # strict type check
uv run pytest                # tests

# Run a single test
uv run pytest tests/test_smoke.py::test_version

# Run the app in Textual dev mode (live CSS reload, console)
uv run textual run --dev telemente.tui.app:TelementeApp
```

This project practices **test-driven development** — see
[`AGENTS.md`](AGENTS.md) and the [`plans/`](plans/) directory for the workflow
and per-feature specifications.

## Project layout

```
src/telemente/      Application code
  matrix/           Async wrapper around matrix-nio (the only network layer)
  tui/              Textual screens & widgets
tests/              Test suite (mirrors the package layout)
plans/              Detailed, per-feature implementation plans
```

## Logging

telemente logs to a rotating file — never to stdout (Textual owns the terminal).

| Item | Default |
|------|---------|
| Log file | `~/.local/share/telemente/telemente.log` |
| Max size | 5 MB (3 backups kept) |
| Default level | `INFO` |
| Format | `2026-01-02 15:04:05 INFO     telemente.tui.app: message` |

Override with CLI flags:

```bash
uv run telemente --log-level DEBUG
uv run telemente --log-file /tmp/tele.log --log-level DEBUG
```

At `DEBUG`, third-party loggers (`nio`, `h11`, `h2`, `httpcore`) stay at
`WARNING`; `peewee` and `nio.responses` are capped at `INFO` to keep file
sizes sane.

```bash
# Tail the log while the app is running
tail -f ~/.local/share/telemente/telemente.log
```

## License

[MIT](LICENSE)
