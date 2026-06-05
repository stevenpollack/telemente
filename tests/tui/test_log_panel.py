"""Tests for the LogPanel widget (plan 0014)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import RichLog

from telemente.tui.widgets.log_panel import LogPanel

# ---------------------------------------------------------------------------
# Minimal host apps
# ---------------------------------------------------------------------------


class _PanelApp(App[None]):
    """Hosts a single LogPanel for widget-level tests."""

    def __init__(self, log_file: Path) -> None:
        super().__init__()
        self._log_file = log_file
        self.close_requests: int = 0

    def compose(self) -> ComposeResult:
        yield LogPanel(self._log_file, id="lp")

    def on_log_panel_close_requested(self, _: LogPanel.CloseRequested) -> None:
        self.close_requests += 1


class _MainScreenApp(App[None]):
    """Mimics MainScreen's LogPanel hosting for toggle/ESC tests."""

    def __init__(self, log_file: Path) -> None:
        super().__init__()
        self._log_file = log_file

    def compose(self) -> ComposeResult:
        panel = LogPanel(self._log_file, id="lp")
        panel.display = False
        yield panel

    def action_toggle_log(self) -> None:
        panel = self.query_one("#lp", LogPanel)
        panel.display = not panel.display


# ---------------------------------------------------------------------------
# Panel visibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_panel_hidden_by_default(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    app = _MainScreenApp(log_file)
    async with app.run_test() as pilot:
        panel = pilot.app.query_one("#lp", LogPanel)
        assert not panel.display


@pytest.mark.asyncio
async def test_action_toggle_log_shows_panel(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    app = _MainScreenApp(log_file)
    async with app.run_test() as pilot:
        await pilot.app.run_action("toggle_log")
        await pilot.pause()
        panel = pilot.app.query_one("#lp", LogPanel)
        assert panel.display


@pytest.mark.asyncio
async def test_action_toggle_log_hides_panel(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    app = _MainScreenApp(log_file)
    async with app.run_test() as pilot:
        await pilot.app.run_action("toggle_log")
        await pilot.pause()
        await pilot.app.run_action("toggle_log")
        await pilot.pause()
        panel = pilot.app.query_one("#lp", LogPanel)
        assert not panel.display


# ---------------------------------------------------------------------------
# Close button and ESC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_button_posts_close_requested(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    app = _PanelApp(log_file)

    async with app.run_test() as pilot:
        await pilot.click("#log-close")
        await pilot.pause()

    assert app.close_requests == 1


@pytest.mark.asyncio
async def test_esc_closes_panel(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    app = _PanelApp(log_file)

    async with app.run_test() as pilot:
        app.query_one("#lp", LogPanel).focus()
        await pilot.press("escape")
        await pilot.pause()

    assert app.close_requests == 1


# ---------------------------------------------------------------------------
# File reading and tailing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_panel_reads_existing_log(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    log_file.write_text("line one\nline two\nline three\n")

    app = _PanelApp(log_file)
    async with app.run_test() as pilot:
        # Give the tail worker time to read the file.
        await asyncio.sleep(0.6)
        await pilot.pause()

        rich_log = pilot.app.query_one("#log-output", RichLog)
        lines = rich_log.lines
        text = "\n".join(str(line) for line in lines)
        assert "line one" in text
        assert "line three" in text


@pytest.mark.asyncio
async def test_panel_tails_new_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    log_file.write_text("existing line\n")

    app = _PanelApp(log_file)
    async with app.run_test() as pilot:
        await asyncio.sleep(0.4)
        await pilot.pause()

        # Append a new line after the initial read.
        with log_file.open("a") as f:
            f.write("appended line\n")

        await asyncio.sleep(0.6)
        await pilot.pause()

        rich_log = pilot.app.query_one("#log-output", RichLog)
        text = "\n".join(str(line) for line in rich_log.lines)
        assert "appended line" in text


@pytest.mark.asyncio
async def test_missing_log_file_no_crash(tmp_path: Path) -> None:
    log_file = tmp_path / "nonexistent.log"
    app = _PanelApp(log_file)
    async with app.run_test() as pilot:
        await asyncio.sleep(0.4)
        await pilot.pause()
        # Should be mounted with empty content, no exception.
        rich_log = pilot.app.query_one("#log-output", RichLog)
        assert rich_log is not None


# ---------------------------------------------------------------------------
# Command palette
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_command_palette_has_toggle() -> None:
    """'Toggle log viewer' must appear in TelementeCommands discovery hits."""
    import fakes as fakes_module
    from telemente.tui.app import TelementeApp
    from telemente.tui.commands import TelementeCommands
    from telemente.tui.screens.main import MainScreen

    fake = fakes_module.FakeMatrixClient()
    app = TelementeApp(client=fake)  # type: ignore[arg-type]

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        provider = TelementeCommands(app.screen)
        hits = [h async for h in provider.discover()]
        names = [h.text for h in hits if h.text is not None]
        assert any("log" in n.lower() for n in names)
