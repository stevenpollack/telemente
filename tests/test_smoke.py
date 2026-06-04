"""Smoke tests proving the scaffold imports and the Textual harness works."""

from __future__ import annotations

from pathlib import Path

import pytest

import telemente
from telemente.tui.app import TelementeApp


def test_version() -> None:
    assert telemente.__version__ == "0.1.0"


@pytest.mark.asyncio
async def test_app_boots(tmp_path: Path) -> None:
    """The app mounts and pushes the login screen (plan 0004)."""
    from telemente.config import CredentialStore, Paths
    from telemente.tui.screens.login import LoginScreen

    # Use an isolated credential store (unique service name + temp paths) so
    # a real saved session in the OS keyring does not interfere.
    paths = Paths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        store_dir=tmp_path / "store",
    )
    store = CredentialStore(paths, service="telemente-test-smoke")

    app = TelementeApp(credential_store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        # With no saved session the app pushes LoginScreen on mount.
        assert isinstance(app.screen, LoginScreen)
