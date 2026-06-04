"""Tests for telemente.config (plan 0002)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import keyring.errors
import pytest

from telemente.config import CredentialStore, Paths, Session, Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paths(base: Path) -> Paths:
    return Paths(
        config_dir=base / "config",
        data_dir=base / "data",
        store_dir=base / "data" / "store",
    )


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def test_paths_ensure_creates_dirs(tmp_store: Path) -> None:
    paths = _make_paths(tmp_store)
    result = paths.ensure()
    assert paths.config_dir.exists()
    assert paths.data_dir.exists()
    assert paths.store_dir.exists()
    assert result is paths


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_settings_roundtrip(tmp_store: Path) -> None:
    path = tmp_store / "settings.toml"
    s = Settings(homeserver="https://example.com", default_device_name="mydevice")
    s.save(path)
    loaded = Settings.load(path)
    assert loaded.homeserver == "https://example.com"
    assert loaded.default_device_name == "mydevice"


def test_settings_load_missing_returns_defaults(tmp_store: Path) -> None:
    path = tmp_store / "nonexistent.toml"
    s = Settings.load(path)
    assert s.homeserver == "https://matrix.org"
    assert s.default_device_name == "telemente"


def test_settings_load_corrupt_returns_defaults(
    tmp_store: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_store / "settings.toml"
    path.write_text("this is !!! not valid toml @@@ = !!!")
    with caplog.at_level(logging.WARNING):
        s = Settings.load(path)
    assert s.homeserver == "https://matrix.org"
    assert s.default_device_name == "telemente"
    assert any(
        "warn" in r.levelname.lower() or r.levelno >= logging.WARNING for r in caplog.records
    )


# ---------------------------------------------------------------------------
# CredentialStore — keyring roundtrip
# ---------------------------------------------------------------------------


def test_credentialstore_keyring_roundtrip(
    tmp_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store: dict[str, str] = {}

    def fake_set(service: str, key: str, value: str) -> None:
        store[f"{service}:{key}"] = value

    def fake_get(service: str, key: str) -> str | None:
        return store.get(f"{service}:{key}")

    def fake_delete(service: str, key: str) -> None:
        store.pop(f"{service}:{key}", None)

    monkeypatch.setattr("keyring.set_password", fake_set)
    monkeypatch.setattr("keyring.get_password", fake_get)
    monkeypatch.setattr("keyring.delete_password", fake_delete)

    paths = _make_paths(tmp_store).ensure()
    cred_store = CredentialStore(paths)

    session = Session(
        homeserver="https://matrix.org",
        user_id="@alice:matrix.org",
        device_id="ABCDEF",
        access_token="secret-token",
    )
    cred_store.save(session)
    loaded = cred_store.load()
    assert loaded == session

    cred_store.clear()
    assert cred_store.load() is None


# ---------------------------------------------------------------------------
# CredentialStore — fallback file
# ---------------------------------------------------------------------------


def test_credentialstore_fallback_file(tmp_store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_no_keyring(*args: Any, **kwargs: Any) -> None:
        raise keyring.errors.NoKeyringError

    def raise_no_keyring_get(*args: Any, **kwargs: Any) -> str | None:
        raise keyring.errors.NoKeyringError

    monkeypatch.setattr("keyring.set_password", raise_no_keyring)
    monkeypatch.setattr("keyring.get_password", raise_no_keyring_get)
    monkeypatch.setattr("keyring.delete_password", raise_no_keyring)

    paths = _make_paths(tmp_store).ensure()
    cred_store = CredentialStore(paths)

    session = Session(
        homeserver="https://matrix.org",
        user_id="@bob:matrix.org",
        device_id="XYZABC",
        access_token="another-secret",
    )
    cred_store.save(session)

    fallback_file = paths.data_dir / "session.json"
    assert fallback_file.exists()
    # Check file permissions are 0600
    mode_str = oct(fallback_file.stat().st_mode)[-3:]
    assert mode_str == "600", f"Expected 0600 permissions, got {mode_str}"

    loaded = cred_store.load()
    assert loaded == session


# ---------------------------------------------------------------------------
# CredentialStore — no token in config_dir
# ---------------------------------------------------------------------------


def test_credentialstore_no_token_in_repr(tmp_store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The store must never write the access token under config_dir."""
    store: dict[str, str] = {}

    def fake_set(service: str, key: str, value: str) -> None:
        store[f"{service}:{key}"] = value

    def fake_get(service: str, key: str) -> str | None:
        return store.get(f"{service}:{key}")

    def fake_delete(service: str, key: str) -> None:
        store.pop(f"{service}:{key}", None)

    monkeypatch.setattr("keyring.set_password", fake_set)
    monkeypatch.setattr("keyring.get_password", fake_get)
    monkeypatch.setattr("keyring.delete_password", fake_delete)

    paths = _make_paths(tmp_store).ensure()
    cred_store = CredentialStore(paths)

    session = Session(
        homeserver="https://matrix.org",
        user_id="@charlie:matrix.org",
        device_id="CHARLIE1",
        access_token="super-secret-token",
    )
    cred_store.save(session)

    # No files under config_dir should contain the access token
    for f in paths.config_dir.rglob("*"):
        if f.is_file():
            content = f.read_text()
            assert "super-secret-token" not in content, f"Token found in config_dir file: {f}"
