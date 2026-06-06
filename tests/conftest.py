"""Shared pytest fixtures for the telemente test suite."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from textual.app import App
from textual.pilot import Pilot


async def wait_for_workers(app: App[Any]) -> None:
    """Wait for all of an app's background workers to finish, then settle once.

    Replaces brittle ``await pilot.pause(); await pilot.pause()`` chains. First
    waits on Textual's ``WorkerManager`` until every worker completes, then runs
    a full settle pass (the same screen/idle drain that ``pilot.pause()`` does)
    so any messages those workers posted are processed before the test asserts
    on widget or message state.
    """
    await app.workers.wait_for_complete()
    # Drain the message queues exactly like pilot.pause() would.
    pilot: Pilot[Any] = Pilot(app)
    await pilot.pause()
    # Workers spawned by those drained messages also need to finish.
    await app.workers.wait_for_complete()
    await pilot.pause()


@pytest.fixture
def tmp_store(tmp_path: Path) -> Iterator[Path]:
    """A throwaway directory standing in for telemente's data/store dir.

    Used by config/credential and e2e-store tests so nothing touches the real
    user data directories.
    """
    store = tmp_path / "telemente-store"
    store.mkdir()
    yield store
