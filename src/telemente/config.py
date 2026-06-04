"""Configuration paths, settings, and secure credential storage for telemente.

Plan 0002: config & credentials.
"""

from __future__ import annotations

import contextlib
import json
import logging
import stat
from dataclasses import dataclass
from pathlib import Path

import keyring
import keyring.errors
import platformdirs

APP_NAME = "telemente"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Paths:
    config_dir: Path
    data_dir: Path
    store_dir: Path  # data_dir / "store" — nio/olm store lives here

    @classmethod
    def default(cls) -> Paths:
        config_dir = Path(platformdirs.user_config_dir(APP_NAME))
        data_dir = Path(platformdirs.user_data_dir(APP_NAME))
        return cls(
            config_dir=config_dir,
            data_dir=data_dir,
            store_dir=data_dir / "store",
        )

    def ensure(self) -> Paths:
        """Create all directories (parents=True, exist_ok=True) and return self."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        return self


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Settings:
    homeserver: str = "https://matrix.org"
    default_device_name: str = "telemente"

    @classmethod
    def load(cls, path: Path) -> Settings:
        """Load settings from a TOML file. Returns defaults if absent or unparseable."""
        if not path.exists():
            return cls()
        try:
            import tomllib

            raw = tomllib.loads(path.read_text(encoding="utf-8"))
            homeserver = str(raw.get("homeserver", "https://matrix.org"))
            default_device_name = str(raw.get("default_device_name", "telemente"))
            return cls(homeserver=homeserver, default_device_name=default_device_name)
        except Exception as exc:
            logger.warning("Failed to load settings from %s: %s — using defaults", path, exc)
            return cls()

    def save(self, path: Path) -> None:
        """Save settings as TOML (two string fields, hand-written to avoid deps)."""
        lines = [
            f'homeserver = "{self.homeserver}"\n',
            f'default_device_name = "{self.default_device_name}"\n',
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Session:
    homeserver: str
    user_id: str
    device_id: str
    access_token: str


# ---------------------------------------------------------------------------
# CredentialStore
# ---------------------------------------------------------------------------

_KEYRING_KEY = "session"
_FALLBACK_FILENAME = "session.json"


class CredentialStore:
    """Persists a Session. Prefers the OS keyring; falls back to a 0600 file."""

    def __init__(self, paths: Paths, *, service: str = APP_NAME) -> None:
        self._paths = paths
        self._service = service

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fallback_path(self) -> Path:
        return self._paths.data_dir / _FALLBACK_FILENAME

    def _to_json(self, session: Session) -> str:
        return json.dumps(
            {
                "homeserver": session.homeserver,
                "user_id": session.user_id,
                "device_id": session.device_id,
                "access_token": session.access_token,
            }
        )

    def _from_json(self, raw: str) -> Session:
        data = json.loads(raw)
        return Session(
            homeserver=data["homeserver"],
            user_id=data["user_id"],
            device_id=data["device_id"],
            access_token=data["access_token"],
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, session: Session) -> None:
        """Persist the session. Falls back to a 0600 file on NoKeyringError."""
        payload = self._to_json(session)
        try:
            keyring.set_password(self._service, _KEYRING_KEY, payload)
        except keyring.errors.NoKeyringError:
            logger.warning(
                "No system keyring available — storing session in plaintext file (0600): %s",
                self._fallback_path(),
            )
            self._write_fallback(payload)

    def load(self) -> Session | None:
        """Return the saved session, or None if none exists."""
        try:
            raw = keyring.get_password(self._service, _KEYRING_KEY)
            if raw is None:
                return None
            return self._from_json(raw)
        except keyring.errors.NoKeyringError:
            return self._read_fallback()

    def clear(self) -> None:
        """Remove the session from both keyring and fallback file."""
        with contextlib.suppress(keyring.errors.NoKeyringError, keyring.errors.PasswordDeleteError):
            keyring.delete_password(self._service, _KEYRING_KEY)
        fp = self._fallback_path()
        if fp.exists():
            fp.unlink()

    # ------------------------------------------------------------------
    # Fallback file helpers
    # ------------------------------------------------------------------

    def _write_fallback(self, payload: str) -> None:
        fp = self._fallback_path()
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(payload, encoding="utf-8")
        # Restrict to owner read/write only (0o600)
        fp.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def _read_fallback(self) -> Session | None:
        fp = self._fallback_path()
        if not fp.exists():
            return None
        try:
            return self._from_json(fp.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read fallback session file %s: %s", fp, exc)
            return None
