"""Shared pytest fixtures for the telemente test suite."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_store(tmp_path: Path) -> Iterator[Path]:
    """A throwaway directory standing in for telemente's data/store dir.

    Used by config/credential and e2e-store tests so nothing touches the real
    user data directories.
    """
    store = tmp_path / "telemente-store"
    store.mkdir()
    yield store
