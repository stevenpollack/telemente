"""Smoke tests proving the scaffold imports and the Textual harness works."""

from __future__ import annotations

import telemente
from telemente.tui.app import TelementeApp


def test_version() -> None:
    assert telemente.__version__ == "0.1.0"


async def test_app_boots() -> None:
    """The app mounts and pushes the login screen (plan 0004)."""
    from telemente.tui.screens.login import LoginScreen

    app = TelementeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # With no saved session the app pushes LoginScreen on mount.
        assert isinstance(app.screen, LoginScreen)
