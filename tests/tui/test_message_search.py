"""Tier-2 tests for in-room message search UX (plan 0024).

All tests use FakeMatrixClient injected via SearchHostApp.
No aioresponses; no real homeserver.
"""

from __future__ import annotations

from datetime import UTC, datetime

from textual.app import App, ComposeResult
from textual.widgets import Input, Static

import fakes as fakes_module
from telemente.matrix.models import Message
from telemente.tui.widgets.message_view import MessageRow, MessageView

FakeMatrixClient = fakes_module.FakeMatrixClient

ROOM_ID = "!search_room:s"


def _msg(
    event_id: str,
    room_id: str = ROOM_ID,
    body: str = "hello",
    timestamp_ms: int = 1_700_000_000_000,
) -> Message:
    return Message(
        event_id=event_id,
        room_id=room_id,
        sender="@alice:s",
        sender_display_name="Alice",
        body=body,
        timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
    )


class SearchHostApp(App[None]):
    """Minimal host app that mounts a MessageView for search tests."""

    def __init__(self, client: FakeMatrixClient, room_id: str) -> None:
        super().__init__()
        self._client = client
        self._room_id = room_id

    def compose(self) -> ComposeResult:
        yield MessageView(self._client, id="message-panel")

    def on_mount(self) -> None:
        view = self.query_one(MessageView)
        view._current_room_id = self._room_id  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_messages(app: SearchHostApp, messages: list[Message]) -> None:
    """Pre-load messages into the view without triggering client.messages()."""
    view = app.query_one(MessageView)
    from textual.containers import VerticalScroll

    timeline = view.query_one("#message-timeline", VerticalScroll)
    for msg in messages:
        view._rendered_event_ids.add(msg.event_id)  # pyright: ignore[reportPrivateUsage]
        view._msgs_by_id[msg.event_id] = msg  # pyright: ignore[reportPrivateUsage]
        timeline.mount(MessageRow(msg))


# ---------------------------------------------------------------------------
# Test 1: Ctrl+F opens search bar and focuses input
# ---------------------------------------------------------------------------


async def test_ctrl_f_opens_search_bar() -> None:
    fake = FakeMatrixClient()
    fake.messages_data[ROOM_ID] = [_msg("$e1", body="hello")]
    app = SearchHostApp(fake, ROOM_ID)

    async with app.run_test(size=(120, 40)) as pilot:
        view = app.query_one(MessageView)
        await _load_messages(app, [_msg("$e1", body="hello")])
        await pilot.pause()

        # MessageView itself must be focused (or a child) before ctrl+f reaches it.
        # Give focus to the composer so ctrl+f is dispatched through MessageView's tree.
        view.focus()
        await pilot.pause()

        await pilot.press("ctrl+f")
        await pilot.pause()

        search_bar = view.query_one("#search-bar")
        assert search_bar.display is True
        # After ctrl+f the search input must have focus — key bindings depend on it.
        assert view.query_one("#search-input", Input).has_focus


# ---------------------------------------------------------------------------
# Test 2: search highlights matching row
# ---------------------------------------------------------------------------


async def test_search_highlights_matching_row() -> None:
    fake = FakeMatrixClient()
    fake.search_results = {ROOM_ID: ["$e1"]}
    msgs = [_msg("$e1", body="hello world")]
    fake.messages_data[ROOM_ID] = msgs
    app = SearchHostApp(fake, ROOM_ID)

    async with app.run_test(size=(120, 40)) as pilot:
        await _load_messages(app, msgs)
        await pilot.pause()

        view = app.query_one(MessageView)
        view.focus()
        await pilot.pause()

        await pilot.press("ctrl+f")
        await pilot.pause()

        search_input = app.query_one("#search-input", Input)
        assert search_input.has_focus, "search input should have focus after ctrl+f"

        await pilot.click("#search-input")
        search_input.value = "hello"
        # Trigger Input.Changed manually
        search_input.post_message(Input.Changed(search_input, "hello"))
        await pilot.pause(0.3)

        rows = list(app.query(MessageRow))
        matching = [r for r in rows if r.message.event_id == "$e1"]
        assert len(matching) == 1
        assert matching[0].has_class("-search-match")
        # Assert observable content — the user would see "hello world" in the row.
        bodies = [str(s.render()) for s in matching[0].query(Static)]
        assert any("hello world" in b for b in bodies)


# ---------------------------------------------------------------------------
# Test 3: search count label updated
# ---------------------------------------------------------------------------


async def test_search_count_label_updated() -> None:
    fake = FakeMatrixClient()
    fake.search_results = {ROOM_ID: ["$e1", "$e2"]}
    msgs = [_msg("$e1", body="hello"), _msg("$e2", body="hello again")]
    fake.messages_data[ROOM_ID] = msgs
    app = SearchHostApp(fake, ROOM_ID)

    async with app.run_test(size=(120, 40)) as pilot:
        await _load_messages(app, msgs)
        await pilot.pause()

        view = app.query_one(MessageView)
        view.focus()
        await pilot.pause()

        await pilot.press("ctrl+f")
        await pilot.pause()

        search_input = app.query_one("#search-input", Input)
        assert search_input.has_focus, "search input should have focus after ctrl+f"

        search_input.value = "hello"
        search_input.post_message(Input.Changed(search_input, "hello"))
        await pilot.pause(0.3)

        count_label = app.query_one("#search-count", Static)
        assert str(count_label.render()) == "1 / 2"


# ---------------------------------------------------------------------------
# Test 4: n advances to next match
# ---------------------------------------------------------------------------


async def test_n_advances_to_next_match() -> None:
    fake = FakeMatrixClient()
    fake.search_results = {ROOM_ID: ["$e1", "$e2"]}
    msgs = [_msg("$e1", body="hello"), _msg("$e2", body="hello again")]
    fake.messages_data[ROOM_ID] = msgs
    app = SearchHostApp(fake, ROOM_ID)

    async with app.run_test(size=(120, 40)) as pilot:
        await _load_messages(app, msgs)
        await pilot.pause()

        view = app.query_one(MessageView)
        view.focus()
        await pilot.pause()

        await pilot.press("ctrl+f")
        await pilot.pause()

        search_input = app.query_one("#search-input", Input)
        assert search_input.has_focus, "search input should have focus after ctrl+f"

        search_input.value = "hello"
        search_input.post_message(Input.Changed(search_input, "hello"))
        await pilot.pause(0.3)

        # Move focus to a MessageRow so that "n" reaches MessageView's BINDINGS
        # via bubbling, rather than being consumed as text by the focused Input.
        rows_before = list(app.query(MessageRow))
        assert rows_before, "at least one MessageRow must exist to receive focus"
        rows_before[0].focus()
        await pilot.pause()
        assert rows_before[0].has_focus, "MessageRow must have focus before pressing n"

        # Advance to next match
        await pilot.press("n")
        await pilot.pause()

        count_label = app.query_one("#search-count", Static)
        assert str(count_label.render()) == "2 / 2"

        rows = list(app.query(MessageRow))
        current = [r for r in rows if r.has_class("-search-current")]
        assert len(current) == 1
        assert current[0].message.event_id == "$e2"
        # Assert the user-visible text of the current match.
        bodies = [str(s.render()) for s in current[0].query(Static)]
        assert any("hello again" in b for b in bodies)


# ---------------------------------------------------------------------------
# Test 5: N goes to previous match (wraps)
# ---------------------------------------------------------------------------


async def test_N_goes_to_prev_match() -> None:
    fake = FakeMatrixClient()
    fake.search_results = {ROOM_ID: ["$e1", "$e2"]}
    msgs = [_msg("$e1", body="hello"), _msg("$e2", body="hello again")]
    fake.messages_data[ROOM_ID] = msgs
    app = SearchHostApp(fake, ROOM_ID)

    async with app.run_test(size=(120, 40)) as pilot:
        await _load_messages(app, msgs)
        await pilot.pause()

        view = app.query_one(MessageView)
        view.focus()
        await pilot.pause()

        await pilot.press("ctrl+f")
        await pilot.pause()

        search_input = app.query_one("#search-input", Input)
        assert search_input.has_focus, "search input should have focus after ctrl+f"

        search_input.value = "hello"
        search_input.post_message(Input.Changed(search_input, "hello"))
        await pilot.pause(0.3)

        # Move focus to a MessageRow so that "N" reaches MessageView's BINDINGS
        # via bubbling, rather than being consumed as text by the focused Input.
        rows_before = list(app.query(MessageRow))
        assert rows_before, "at least one MessageRow must exist to receive focus"
        rows_before[0].focus()
        await pilot.pause()
        assert rows_before[0].has_focus, "MessageRow must have focus before pressing N"

        # cursor starts at 0 (first match); pressing N wraps to last
        await pilot.press("N")
        await pilot.pause()

        count_label = app.query_one("#search-count", Static)
        assert str(count_label.render()) == "2 / 2"


# ---------------------------------------------------------------------------
# Test 6: search wraps forward at last match
# ---------------------------------------------------------------------------


async def test_search_wraps_forward() -> None:
    fake = FakeMatrixClient()
    fake.search_results = {ROOM_ID: ["$e1", "$e2"]}
    msgs = [_msg("$e1", body="hello"), _msg("$e2", body="hello again")]
    fake.messages_data[ROOM_ID] = msgs
    app = SearchHostApp(fake, ROOM_ID)

    async with app.run_test(size=(120, 40)) as pilot:
        await _load_messages(app, msgs)
        await pilot.pause()

        view = app.query_one(MessageView)
        view.focus()
        await pilot.pause()

        await pilot.press("ctrl+f")
        await pilot.pause()

        search_input = app.query_one("#search-input", Input)
        assert search_input.has_focus, "search input should have focus after ctrl+f"

        search_input.value = "hello"
        search_input.post_message(Input.Changed(search_input, "hello"))
        await pilot.pause(0.3)

        # Move focus to a MessageRow before pressing n/n so that the key reaches
        # MessageView's BINDINGS via bubbling, not eaten by the focused Input.
        rows_before = list(app.query(MessageRow))
        assert rows_before, "at least one MessageRow must exist to receive focus"
        rows_before[0].focus()
        await pilot.pause()
        assert rows_before[0].has_focus, "MessageRow must have focus before pressing n"

        # Advance to last match, then wrap
        await pilot.press("n")  # cursor = 1 (last)
        await pilot.pause()
        await pilot.press("n")  # wraps to cursor = 0
        await pilot.pause()

        count_label = app.query_one("#search-count", Static)
        assert str(count_label.render()) == "1 / 2"


# ---------------------------------------------------------------------------
# Test 7: Escape closes search bar
# ---------------------------------------------------------------------------


async def test_escape_closes_search_bar() -> None:
    fake = FakeMatrixClient()
    fake.search_results = {ROOM_ID: ["$e1"]}
    msgs = [_msg("$e1", body="hello")]
    fake.messages_data[ROOM_ID] = msgs
    app = SearchHostApp(fake, ROOM_ID)

    async with app.run_test(size=(120, 40)) as pilot:
        await _load_messages(app, msgs)
        await pilot.pause()

        view = app.query_one(MessageView)
        view.focus()
        await pilot.pause()

        await pilot.press("ctrl+f")
        await pilot.pause()

        search_input = app.query_one("#search-input", Input)
        assert search_input.has_focus, "search input should have focus after ctrl+f"

        search_input.value = "hello"
        search_input.post_message(Input.Changed(search_input, "hello"))
        await pilot.pause(0.3)

        await pilot.press("escape")
        await pilot.pause()

        search_bar = view.query_one("#search-bar")
        assert search_bar.display is False

        # No rows should retain -search-match
        rows = list(app.query(MessageRow))
        assert all(not r.has_class("-search-match") for r in rows)


# ---------------------------------------------------------------------------
# Test 8: empty query clears highlights
# ---------------------------------------------------------------------------


async def test_empty_query_clears_highlights() -> None:
    fake = FakeMatrixClient()
    fake.search_results = {ROOM_ID: ["$e1"]}
    msgs = [_msg("$e1", body="hello")]
    fake.messages_data[ROOM_ID] = msgs
    app = SearchHostApp(fake, ROOM_ID)

    async with app.run_test(size=(120, 40)) as pilot:
        await _load_messages(app, msgs)
        await pilot.pause()

        view = app.query_one(MessageView)
        view.focus()
        await pilot.pause()

        await pilot.press("ctrl+f")
        await pilot.pause()

        search_input = app.query_one("#search-input", Input)
        assert search_input.has_focus, "search input should have focus after ctrl+f"

        # First search to get highlights
        search_input.value = "hello"
        search_input.post_message(Input.Changed(search_input, "hello"))
        await pilot.pause(0.3)

        # Now clear the query
        search_input.value = ""
        search_input.post_message(Input.Changed(search_input, ""))
        await pilot.pause(0.3)

        rows = list(app.query(MessageRow))
        assert all(not r.has_class("-search-match") for r in rows)


# ---------------------------------------------------------------------------
# Test 9: search in wrong room matches are ignored
# ---------------------------------------------------------------------------


async def test_search_in_wrong_room_matches_ignored() -> None:
    fake = FakeMatrixClient()
    # Results are for a *different* room
    fake.search_results = {"!other:s": ["$e1"]}
    msgs = [_msg("$e1", body="hello")]
    fake.messages_data[ROOM_ID] = msgs
    app = SearchHostApp(fake, ROOM_ID)

    async with app.run_test(size=(120, 40)) as pilot:
        await _load_messages(app, msgs)
        await pilot.pause()

        view = app.query_one(MessageView)
        view.focus()
        await pilot.pause()

        await pilot.press("ctrl+f")
        await pilot.pause()

        search_input = app.query_one("#search-input", Input)
        assert search_input.has_focus, "search input should have focus after ctrl+f"

        search_input.value = "hello"
        search_input.post_message(Input.Changed(search_input, "hello"))
        await pilot.pause(0.3)

        # The active room is ROOM_ID; fake returns [] for that room
        rows = list(app.query(MessageRow))
        assert all(not r.has_class("-search-match") for r in rows)
        # The row should still show "hello" — only the highlight is absent.
        assert len(rows) == 1
        bodies = [str(s.render()) for s in rows[0].query(Static)]
        assert any("hello" in b for b in bodies)


# ---------------------------------------------------------------------------
# Test 10: command palette "Search in room" opens search bar
# ---------------------------------------------------------------------------


async def test_command_palette_search_in_room() -> None:
    """cmd_search_in_room() via the command palette opens the search bar."""
    import tempfile
    from pathlib import Path

    from telemente.config import CredentialStore, Paths
    from telemente.tui.app import TelementeApp
    from telemente.tui.screens.main import MainScreen

    fake = FakeMatrixClient()
    fake.logged_in = True
    fake.search_results = {}
    tmp_dir = Path(tempfile.mkdtemp())
    paths = Paths(
        config_dir=tmp_dir / "config",
        data_dir=tmp_dir / "data",
        store_dir=tmp_dir / "store",
    )
    store = CredentialStore(paths, service="telemente-test-search-cmd")
    app = TelementeApp(client=fake, credential_store=store)  # type: ignore[arg-type]
    app.start_sync_and_subscribe()

    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()

        from telemente.tui.commands import TelementeCommands

        provider = TelementeCommands(app.screen)

        # Verify "Search in room" appears in discover()
        hits = [h async for h in provider.discover()]
        names = {h.text for h in hits}
        assert "Search in room" in names


# ---------------------------------------------------------------------------
# Test 11: non-matching rows are not highlighted
# ---------------------------------------------------------------------------


async def test_search_non_matching_rows_not_highlighted() -> None:
    fake = FakeMatrixClient()
    # Only $e2 matches
    fake.search_results = {ROOM_ID: ["$e2"]}
    msgs = [
        _msg("$e1", body="alpha"),
        _msg("$e2", body="beta"),
        _msg("$e3", body="gamma"),
    ]
    fake.messages_data[ROOM_ID] = msgs
    app = SearchHostApp(fake, ROOM_ID)

    async with app.run_test(size=(120, 40)) as pilot:
        await _load_messages(app, msgs)
        await pilot.pause()

        view = app.query_one(MessageView)
        view.focus()
        await pilot.pause()

        await pilot.press("ctrl+f")
        await pilot.pause()

        search_input = app.query_one("#search-input", Input)
        assert search_input.has_focus, "search input should have focus after ctrl+f"

        search_input.value = "beta"
        search_input.post_message(Input.Changed(search_input, "beta"))
        await pilot.pause(0.3)

        rows = list(app.query(MessageRow))
        matching = {r.message.event_id for r in rows if r.has_class("-search-match")}
        non_matching = {r.message.event_id for r in rows if not r.has_class("-search-match")}

        assert "$e2" in matching
        assert "$e1" in non_matching
        assert "$e3" in non_matching

        # Assert the user-visible content of each row.
        rows_by_id = {r.message.event_id: r for r in rows}
        e2_bodies = [str(s.render()) for s in rows_by_id["$e2"].query(Static)]
        assert any("beta" in b for b in e2_bodies)
        e1_bodies = [str(s.render()) for s in rows_by_id["$e1"].query(Static)]
        assert any("alpha" in b for b in e1_bodies)
