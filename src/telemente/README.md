# `src/telemente/` — top-level package

This is the root of the `telemente` application package. It wires together the
two major subsystems — the Matrix protocol layer (`matrix/`) and the Textual UI
layer (`tui/`) — through a thin entry-point and configuration module.

## Purpose

Owns application-level concerns: CLI entry point, logging configuration, XDG
path resolution, settings persistence, secure credential storage, and the room
list disk cache. All these live here rather than in `tui/` or `matrix/` because
they are needed by both subsystems and carry no dependency on either.

## Key design decisions

**Lazy imports in `cli.py`.** `TelementeApp` and `MatrixClient` are imported
inside `main()` so that `telemente --version` stays fast without paying the full
Textual + matrix-nio import cost. The logging configuration is applied before
the lazy import so it covers every subsequent module load.

**`CredentialStore` prefers the OS keyring.** When `keyring` has no backend
(headless servers, some CI environments) it falls back to a 0600-mode JSON file
rather than failing hard. Both paths serialize identically to the `Session`
dataclass; callers never need to know which path was taken.

**`RoomCache` is deliberately lossy.** It persists `RoomSummary` lists to disk
so the UI can pre-populate the room list before the first sync returns, giving
zero-flicker startup. Unread counts are intentionally excluded from the cache
because they reset on restart anyway. Any read failure is silently ignored —
the cache is a display aid, not a source of truth.

**Settings are TOML, hand-written.** Only two string fields exist
(`homeserver`, `default_device_name`). Using `tomllib` for reads and a
hand-rolled writer avoids adding a `tomli_w`/`tomllib` write dependency for a
trivial two-key file.

## File map

| File | Role |
|------|------|
| `__init__.py` | Package marker; exposes `__version__` |
| `__main__.py` | Enables `python -m telemente`; delegates to `cli.main()` |
| `cli.py` | Argument parsing, logging setup, `main()` / `dev()` / `console()` entry points |
| `config.py` | `Paths`, `Settings`, `Session`, `CredentialStore`, `RoomCache` |
| `matrix/` | Matrix protocol layer (see `matrix/README.md`) |
| `tui/` | Textual UI layer (see `tui/README.md`) |
| `py.typed` | PEP 561 marker — package ships inline types |

## Patterns used

- **Frozen dataclasses** for `Paths` and `Session`: both are value objects that
  must not be mutated after construction.
- **Mutable dataclass** for `Settings`: fields are written back by `save()`.
- **`platformdirs`** for XDG-compliant paths instead of hardcoded `~/.config`.

## What lives elsewhere

- Matrix protocol logic → `matrix/`
- Textual screens and widgets → `tui/`
- Type stubs for matrix-nio → `stubs/nio/` (project root, not inside `src/`)
- Tests → `tests/` (mirrors this layout: `tests/matrix/`, `tests/tui/`)
