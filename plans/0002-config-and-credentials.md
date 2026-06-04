# 0002 — Config & Credentials

## Goal

Provide telemente's configuration paths, a typed `Settings` object, and secure
persistence of the Matrix **session** (so users don't log in every launch).
This is the foundation the client wrapper (0003) and e2ee store (0010) build on.

## Dependencies

- 0001 (scaffolding). No other plans.

## Files to create / modify

- `src/telemente/config.py` — new.
- `tests/test_config.py` — new.

## Public interface

```python
# src/telemente/config.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

APP_NAME = "telemente"


@dataclass(frozen=True, slots=True)
class Paths:
    config_dir: Path     # platformdirs user_config_dir
    data_dir: Path       # platformdirs user_data_dir
    store_dir: Path      # data_dir / "store"  (nio/olm store lives here)

    @classmethod
    def default(cls) -> "Paths": ...
    def ensure(self) -> "Paths":  # mkdir -p all dirs, return self
        ...


@dataclass(slots=True)
class Settings:
    homeserver: str = "https://matrix.org"
    default_device_name: str = "telemente"

    @classmethod
    def load(cls, path: Path) -> "Settings": ...      # tolerant of missing file
    def save(self, path: Path) -> None: ...           # TOML


@dataclass(frozen=True, slots=True)
class Session:
    homeserver: str
    user_id: str
    device_id: str
    access_token: str


class CredentialStore:
    """Persists a Session. Prefers the OS keyring; falls back to a 0600 file."""

    def __init__(self, paths: Paths, *, service: str = APP_NAME) -> None: ...
    def save(self, session: Session) -> None: ...
    def load(self) -> Session | None: ...
    def clear(self) -> None: ...
```

## Behavior

- **Paths**: use `platformdirs.user_config_dir(APP_NAME)` /
  `user_data_dir(APP_NAME)`. `store_dir = data_dir / "store"`. `ensure()`
  creates all three with `parents=True, exist_ok=True`.
- **Settings**: TOML via stdlib `tomllib` (read) + `tomli_w` **or** hand-write a
  tiny serializer (avoid adding a dep — write TOML manually for two string
  fields, or use `json` to a `settings.json`; prefer TOML file `settings.toml`).
  Decision: use **`tomllib`** to read and a minimal manual writer (two
  string fields) to avoid a new dependency. `load` returns defaults if the file
  is absent or unparseable (log a warning, never crash).
- **CredentialStore**:
  - `save`: serialize the `Session` to JSON and store under
    `keyring.set_password(service, user_id_or_"session", json)`. Use a single
    well-known key (e.g. `"session"`).
  - `load`: read it back; return `None` if absent.
  - **Fallback**: if `keyring` raises `keyring.errors.NoKeyringError` (or import
    yields the fail backend), write/read a JSON file at
    `paths.data_dir / "session.json"` created with mode `0o600`. Log that the
    insecure fallback is in use.
  - `clear`: delete from keyring and remove the fallback file if present.
- **No secrets in logs.** Never log the access token.

## Test cases (write first)

`tests/test_config.py`:

1. `test_paths_ensure_creates_dirs` — build `Paths` rooted at `tmp_store`,
   call `ensure()`, assert all dirs exist.
2. `test_settings_roundtrip` — save then load `Settings`; values match;
   modified fields persist.
3. `test_settings_load_missing_returns_defaults` — load from a non-existent
   path → default `Settings`, no exception.
4. `test_settings_load_corrupt_returns_defaults` — write garbage to the file →
   defaults, no exception (assert a warning was logged via `caplog`).
5. `test_credentialstore_keyring_roundtrip` — **patch** `keyring.set_password`
   / `keyring.get_password` with an in-memory dict (monkeypatch); save a
   `Session`, load it back equal; `clear()` then `load()` → `None`.
6. `test_credentialstore_fallback_file` — monkeypatch keyring functions to
   raise `NoKeyringError`; save → a `session.json` exists with mode `0o600`
   (`oct(path.stat().st_mode)[-3:] == "600"`); load returns the session.
7. `test_credentialstore_no_token_in_repr` — `repr(session)` is fine, but assert
   the store never writes the token anywhere under `config_dir` (only data_dir).

## Mocking strategy

- No network. Use the `tmp_store` fixture for all paths.
- Mock keyring with `monkeypatch.setattr("keyring.get_password", ...)` /
  `set_password`, backed by a `dict`. Test the fallback by making those raise
  `keyring.errors.NoKeyringError`.

## Done-when

- [ ] All 7 tests pass.
- [ ] `mypy --strict` and `ruff` clean.
- [ ] No access token is ever logged; fallback file is `0o600`.
- [ ] `Paths`, `Settings`, `Session`, `CredentialStore` exported and importable.
