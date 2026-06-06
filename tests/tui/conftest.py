"""TUI-specific pytest fixtures.

Overrides ``snap_compare`` from pytest-textual-snapshot to normalise SVG
content by stripping trailing whitespace from each line before comparison
and storage.  Rich/Textual generates SVG with trailing spaces; the
pre-commit ``trailing-whitespace`` hook strips them from committed files,
causing a mismatch if we compare raw bytes.  Normalisation breaks the cycle.

Only loaded when pytest-textual-snapshot is available; the module-level
``importorskip`` in ``test_snapshots.py`` ensures graceful skipping otherwise.
"""

from __future__ import annotations

from typing import Any

import pytest


def _normalise_svg(svg: str) -> str:
    """Normalise SVG content for stable snapshot comparison.

    Strips trailing whitespace from each line and ensures a single trailing
    newline, matching what the pre-commit trailing-whitespace and
    end-of-file-fixer hooks produce on committed baseline files.
    """
    lines = "\n".join(line.rstrip() for line in svg.splitlines())
    return lines + "\n"


try:
    from syrupy.extensions.single_file import (
        SingleFileSnapshotExtension,
        WriteMode,
    )

    class _NormalisedSVGExtension(SingleFileSnapshotExtension):
        """SVG snapshot extension that strips trailing whitespace before storage."""

        file_extension = "raw"
        _write_mode = WriteMode.TEXT

        def serialize(self, data: Any, **kwargs: Any) -> str:
            if isinstance(data, str):
                return _normalise_svg(data)
            return str(data)

    @pytest.fixture
    def snap_compare(snapshot: Any, request: Any) -> Any:
        """Override snap_compare to normalise SVG trailing whitespace.

        Wraps the syrupy snapshot with a normalising extension so that
        pre-commit's trailing-whitespace hook does not cause baseline mismatches.
        """

        def compare(
            app: Any,
            press: Any = (),
            terminal_size: tuple[int, int] = (80, 24),
            run_before: Any = None,
        ) -> bool:
            from textual._doc import take_svg_screenshot

            actual_svg = take_svg_screenshot(
                app=app,
                press=press,
                terminal_size=terminal_size,
                run_before=run_before,
            )
            normalised = _normalise_svg(actual_svg)
            normalised_snapshot = snapshot.use_extension(_NormalisedSVGExtension)
            return normalised_snapshot == normalised  # type: ignore[no-any-return]

        return compare

except ImportError:
    # pytest-textual-snapshot/syrupy not installed; snap_compare fixture not provided.
    # test_snapshots.py uses pytest.importorskip so tests are skipped at module
    # collection time, and this fixture is never requested.
    pass
