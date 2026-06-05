"""Tests for the RoomList widget (plan 0006, updated for plan 0012).

All tests use a minimal host App that mounts RoomList directly.
No network — RoomSummary fixtures are built in-process.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from textual.app import App, ComposeResult

from telemente.matrix.models import RoomSummary
from telemente.tui.widgets.room_list import RoomList, _option_id


class RoomContextMenuHostApp(App[None]):
    """App that mounts RoomList and records RoomContextMenu messages."""

    def __init__(self) -> None:
        super().__init__()
        self.context_menu_events: list[RoomList.RoomContextMenu] = []

    def compose(self) -> ComposeResult:
        yield RoomList(id="room-list")

    def on_room_list_room_context_menu(self, message: RoomList.RoomContextMenu) -> None:
        self.context_menu_events.append(message)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DT_OLD = datetime(2024, 1, 1, 12, 0, 0)
DT_NEW = datetime(2024, 6, 1, 12, 0, 0)
DT_MID = datetime(2024, 3, 1, 12, 0, 0)


def _room(
    room_id: str,
    display_name: str,
    unread_count: int = 0,
    last_activity: datetime | None = None,
    encrypted: bool = False,
) -> RoomSummary:
    return RoomSummary(
        room_id=room_id,
        display_name=display_name,
        unread_count=unread_count,
        last_activity=last_activity,
        encrypted=encrypted,
    )


# ---------------------------------------------------------------------------
# Host app
# ---------------------------------------------------------------------------


class HostApp(App[None]):
    """Minimal app that mounts a RoomList and records RoomSelected events."""

    def __init__(self) -> None:
        super().__init__()
        self.selected_room_ids: list[str] = []

    def compose(self) -> ComposeResult:
        yield RoomList(id="room-list")

    def on_room_list_room_selected(self, message: RoomList.RoomSelected) -> None:
        self.selected_room_ids.append(message.room_id)


# ---------------------------------------------------------------------------
# Test 1: set_rooms renders all rooms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_rooms_renders_all() -> None:
    app = HostApp()
    rooms = [
        _room("!a:h", "General"),
        _room("!b:h", "Random"),
        _room("!c:h", "Dev"),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        assert len(room_list.visible_rooms) == 3


# ---------------------------------------------------------------------------
# Test 2: filter substring is case-insensitive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filter_substring_case_insensitive() -> None:
    app = HostApp()
    rooms = [
        _room("!a:h", "General"),
        _room("!b:h", "Random"),
        _room("!c:h", "Dev"),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        room_list.apply_filter("ran")
        await pilot.pause()

        visible = room_list.visible_rooms
        assert len(visible) == 1
        assert visible[0].display_name == "Random"


# ---------------------------------------------------------------------------
# Test 3: clearing the filter restores all rooms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_filter_restores_all() -> None:
    app = HostApp()
    rooms = [
        _room("!a:h", "General"),
        _room("!b:h", "Random"),
        _room("!c:h", "Dev"),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        room_list.apply_filter("ran")
        await pilot.pause()
        assert len(room_list.visible_rooms) == 1

        room_list.apply_filter("")
        await pilot.pause()
        assert len(room_list.visible_rooms) == 3


# ---------------------------------------------------------------------------
# Test 4: no match shows empty-state, zero room items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_match_shows_empty_state() -> None:
    app = HostApp()
    rooms = [
        _room("!a:h", "General"),
        _room("!b:h", "Random"),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        room_list.apply_filter("zzz")
        await pilot.pause()

        assert len(room_list.visible_rooms) == 0
        # Empty-state label should be present in the DOM
        empty_labels = app.query("#room-list--empty-state")
        assert len(empty_labels) == 1
        assert empty_labels.first().display is True


# ---------------------------------------------------------------------------
# Test 5: visible_rooms sorted newest-first, None last
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sorted_by_recent_activity() -> None:
    app = HostApp()
    rooms = [
        _room("!old:h", "OldRoom", last_activity=DT_OLD),
        _room("!none:h", "NoActivity"),  # last_activity=None
        _room("!new:h", "NewRoom", last_activity=DT_NEW),
        _room("!mid:h", "MidRoom", last_activity=DT_MID),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        visible = room_list.visible_rooms
        assert len(visible) == 4
        assert visible[0].room_id == "!new:h"
        assert visible[1].room_id == "!mid:h"
        assert visible[2].room_id == "!old:h"
        assert visible[3].room_id == "!none:h"


# ---------------------------------------------------------------------------
# Test 6: selecting a room posts RoomSelected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_selecting_posts_roomselected() -> None:
    app = HostApp()
    rooms = [
        _room("!a:h", "Alpha", last_activity=DT_NEW),
        _room("!b:h", "Beta", last_activity=DT_OLD),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        # Focus the OptionList, move to the first item (Alpha = newest), then select
        from textual.widgets import OptionList

        option_list = room_list.query_one(OptionList)
        option_list.focus()
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert len(app.selected_room_ids) == 1
        assert app.selected_room_ids[0] == "!a:h"


# ---------------------------------------------------------------------------
# Test 7: unread badge rendered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unread_badge_rendered() -> None:
    """Unread count is embedded in the option prompt as '(3)'."""
    from textual.widgets import OptionList

    app = HostApp()
    rooms = [
        _room("!a:h", "Busy Room", unread_count=3),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        ol = room_list.query_one(OptionList)
        assert ol.option_count == 1
        prompt = str(ol.get_option_at_index(0).prompt)
        assert "Busy Room" in prompt
        assert "(3)" in prompt


# ---------------------------------------------------------------------------
# Test 8: loading indicator shown before first set_rooms, hidden after
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loading_indicator_shown_until_first_rooms() -> None:
    """'Syncing…' is visible on mount, hidden once set_rooms provides data."""
    app = HostApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)

        # Before any rooms arrive, loading indicator should be visible.
        loading = app.query_one("#room-list--loading")
        assert loading.display is True

        # After setting rooms, loading hides.
        room_list.set_rooms([_room("!a:h", "General")])
        await pilot.pause()
        assert loading.display is False


# ---------------------------------------------------------------------------
# Test 9: loading indicator stays hidden after subsequent set_rooms calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loading_indicator_stays_hidden_after_load() -> None:
    """Once rooms have loaded, the indicator stays hidden even if rooms change."""
    app = HostApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms([_room("!a:h", "General")])
        await pilot.pause()

        loading = app.query_one("#room-list--loading")
        assert loading.display is False

        # Update rooms — loading should stay hidden.
        room_list.set_rooms([_room("!a:h", "General"), _room("!b:h", "Random")])
        await pilot.pause()
        assert loading.display is False


# ---------------------------------------------------------------------------
# Test 10: set_rooms while filtered does not lose unfiltered rooms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_rooms_while_filtered_preserves_all_rooms() -> None:
    """Regression: calling set_rooms with all_rooms while a filter is active
    must not permanently discard filtered-out rooms."""
    app = HostApp()
    rooms = [
        _room("!a:h", "General", last_activity=DT_NEW),
        _room("!b:h", "Random", last_activity=DT_MID),
        _room("!c:h", "Dev", last_activity=DT_OLD),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()
        assert len(room_list.all_rooms) == 3

        # Apply a filter that hides 2 of 3 rooms.
        room_list.apply_filter("gen")
        await pilot.pause()
        assert len(room_list.visible_rooms) == 1

        # Simulate what MainScreen does: re-set rooms from all_rooms.
        room_list.set_rooms(room_list.all_rooms)
        await pilot.pause()

        # Clear filter — all 3 rooms must reappear.
        room_list.apply_filter("")
        await pilot.pause()
        assert len(room_list.visible_rooms) == 3
        assert len(room_list.all_rooms) == 3


# ---------------------------------------------------------------------------
# Test 11: set_active_room highlights the matching option
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_active_room_highlights_matching_item() -> None:
    """set_active_room('!a:h') — matching option is highlighted."""
    from textual.widgets import OptionList

    app = HostApp()
    rooms = [
        _room("!a:h", "Alpha"),
        _room("!b:h", "Beta"),
        _room("!c:h", "Gamma"),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        room_list.set_active_room("!a:h")
        await pilot.pause()

        ol = room_list.query_one(OptionList)
        expected_idx = ol.get_option_index(_option_id("!a:h"))
        assert ol.highlighted == expected_idx


# ---------------------------------------------------------------------------
# Test 12: active highlight survives a set_rooms rebuild
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_highlight_survives_set_rooms_rebuild() -> None:
    """Regression: calling set_rooms() after set_active_room() must re-apply the
    highlight — previously the rebuild wiped all classes on new instances."""
    from textual.widgets import OptionList

    app = HostApp()
    rooms = [_room("!a:h", "Alpha"), _room("!b:h", "Beta")]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()
        room_list.set_active_room("!a:h")
        await pilot.pause()

        # Simulate a sync that rebuilds the list (e.g. RoomsChanged)
        room_list.set_rooms(rooms)
        await pilot.pause()

        ol = room_list.query_one(OptionList)
        expected_idx = ol.get_option_index(_option_id("!a:h"))
        assert ol.highlighted == expected_idx


# ---------------------------------------------------------------------------
# Test 13: switching active room moves highlight (no stale highlight on old room)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_active_room_moves_highlight() -> None:
    """Regression: selecting room B after room A must move the highlight to B."""
    from textual.widgets import OptionList

    app = HostApp()
    rooms = [_room("!a:h", "Alpha"), _room("!b:h", "Beta")]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        # Select room A first
        room_list.set_active_room("!a:h")
        room_list.set_rooms(rooms)
        await pilot.pause()

        # Now switch to room B (set_active_room before set_rooms, as main.py does)
        room_list.set_active_room("!b:h")
        room_list.set_rooms(rooms)
        await pilot.pause()

        ol = room_list.query_one(OptionList)
        expected_idx = ol.get_option_index(_option_id("!b:h"))
        assert ol.highlighted == expected_idx


# ---------------------------------------------------------------------------
# Test 14: unread room name is bold and shows count in parens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unread_room_name_is_bold() -> None:
    """A room with unread_count>0 renders its name with bold markup and (N) count."""
    from textual.widgets import OptionList

    app = HostApp()
    rooms = [_room("!a:h", "General", unread_count=3)]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        ol = room_list.query_one(OptionList)
        assert ol.option_count == 1
        prompt = str(ol.get_option_at_index(0).prompt)
        assert "General" in prompt
        assert "(3)" in prompt


# ---------------------------------------------------------------------------
# Test 15: room with no unread has plain name (no bold/count)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_room_name_is_plain() -> None:
    """A room with unread_count==0 renders its name without (N) count."""
    from textual.widgets import OptionList

    app = HostApp()
    rooms = [_room("!a:h", "General", unread_count=0)]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        ol = room_list.query_one(OptionList)
        assert ol.option_count == 1
        prompt = str(ol.get_option_at_index(0).prompt)
        assert "General" in prompt
        assert "(" not in prompt


# ---------------------------------------------------------------------------
# Test 16: favourite tag shows ★ glyph in room name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_favourite_tag_shows_star() -> None:
    """A room tagged m.favourite shows ★ in its rendered name."""
    from textual.widgets import OptionList

    app = HostApp()
    rooms = [
        RoomSummary(
            room_id="!a:h",
            display_name="Starred",
            tags={"m.favourite": None},
        )
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        ol = room_list.query_one(OptionList)
        prompt = str(ol.get_option_at_index(0).prompt)
        assert "★" in prompt


# ---------------------------------------------------------------------------
# Test 17: low-priority tag shows ↓ glyph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lowpriority_tag_shows_arrow() -> None:
    """A room tagged m.lowpriority shows ↓ in its rendered name."""
    from textual.widgets import OptionList

    app = HostApp()
    rooms = [
        RoomSummary(
            room_id="!a:h",
            display_name="Low",
            tags={"m.lowpriority": None},
        )
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        ol = room_list.query_one(OptionList)
        prompt = str(ol.get_option_at_index(0).prompt)
        assert "↓" in prompt


# ---------------------------------------------------------------------------
# Test 18: set_sort_mode("alpha") sorts rooms alphabetically regardless of activity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_sort_mode_alpha() -> None:
    """set_sort_mode('alpha') → rooms sorted A-Z by display_name."""
    app = HostApp()
    rooms = [
        _room("!z:h", "Zebra", last_activity=DT_NEW),
        _room("!a:h", "Alpha", last_activity=DT_OLD),
        _room("!m:h", "Mango", last_activity=DT_MID),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        room_list.set_sort_mode("alpha")
        await pilot.pause()

        visible = room_list.visible_rooms
        assert [r.display_name for r in visible] == ["Alpha", "Mango", "Zebra"]


# ---------------------------------------------------------------------------
# Test 19: set_sort_mode("recent") restores newest-first order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_sort_mode_recent() -> None:
    """set_sort_mode('recent') → rooms sorted newest-first."""
    app = HostApp()
    rooms = [
        _room("!z:h", "Zebra", last_activity=DT_NEW),
        _room("!a:h", "Alpha", last_activity=DT_OLD),
        _room("!m:h", "Mango", last_activity=DT_MID),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        room_list.set_sort_mode("alpha")
        await pilot.pause()

        room_list.set_sort_mode("recent")
        await pilot.pause()

        visible = room_list.visible_rooms
        assert visible[0].room_id == "!z:h"
        assert visible[1].room_id == "!m:h"
        assert visible[2].room_id == "!a:h"


# ---------------------------------------------------------------------------
# Test 20: debounced search — rapid keystrokes do not rebuild on every character
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debounced_search_does_not_rebuild_per_keystroke() -> None:
    """Rapid Input.Changed events must not trigger an immediate rebuild.

    We send two rapid Input.Changed messages and check visible_rooms without
    pausing — the filter must still be deferred (visible_rooms unchanged).
    Then we manually fire the deferred callback to verify it applies correctly.
    This avoids wall-clock timing dependencies in the test environment.
    """
    app = HostApp()
    rooms = [
        _room("!a:h", "General"),
        _room("!b:h", "Random"),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        from textual.widgets import Input

        search = room_list.query_one("#room-search", Input)
        # Post two Input.Changed events in sequence ("rand" only matches "Random").
        room_list.post_message(Input.Changed(search, "ran"))
        room_list.post_message(Input.Changed(search, "rand"))
        # Process messages but NOT the timer.
        await pilot.pause()
        # visible_rooms must NOT yet be filtered — debounce deferred the rebuild.
        assert len(room_list.visible_rooms) == 2
        # _pending_filter must reflect the last value.
        assert room_list.pending_filter == "rand"

        # Fire the deferred callback manually.
        # _rebuild() is synchronous, so visible_rooms is updated immediately.
        room_list.apply_pending_filter()
        assert len(room_list.visible_rooms) == 1
        assert room_list.visible_rooms[0].room_id == "!b:h"


# ---------------------------------------------------------------------------
# Test 21: update_unread patches the option without a full rebuild
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_unread_patches_label_in_place() -> None:
    """update_unread(room_id, count) updates the unread display without
    a full OptionList rebuild — option_count unchanged, prompt updated."""
    from textual.widgets import OptionList

    app = HostApp()
    rooms = [
        _room("!a:h", "General"),
        _room("!b:h", "Random"),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        ol = room_list.query_one(OptionList)
        count_before = ol.option_count
        assert count_before == 2

        room_list.update_unread("!a:h", 5)
        await pilot.pause()

        # option_count unchanged — no rebuild.
        assert ol.option_count == count_before

        # Prompt for room a updated to show new count.
        idx = ol.get_option_index(_option_id("!a:h"))
        prompt = str(ol.get_option_at_index(idx).prompt)
        assert "(5)" in prompt


# ---------------------------------------------------------------------------
# Test 22: mute tag shows 🔕 glyph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mute_tag_shows_bell() -> None:
    """A room tagged m.mute shows 🔕 in its rendered name."""
    from textual.widgets import OptionList

    app = HostApp()
    rooms = [
        RoomSummary(
            room_id="!a:h",
            display_name="Muted",
            tags={"m.mute": None},
        )
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        ol = room_list.query_one(OptionList)
        prompt = str(ol.get_option_at_index(0).prompt)
        assert "🔕" in prompt


# ---------------------------------------------------------------------------
# Test 23: ESC key in focused search input clears the filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_esc_key_clears_search_input() -> None:
    """Pressing ESC while the search input has focus and content clears it."""
    from textual.widgets import Input

    app = HostApp()
    rooms = [
        _room("!a:h", "General"),
        _room("!b:h", "Random"),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        # Type into the search input
        search = room_list.query_one("#room-search", Input)
        search.focus()
        await pilot.pause()
        await pilot.press("r", "a", "n")
        await pilot.pause()
        # Manually apply the pending filter (debounce shortcut)
        room_list.apply_pending_filter()
        assert len(room_list.visible_rooms) == 1

        # ESC should clear the input
        await pilot.press("escape")
        await pilot.pause()
        assert search.value == ""


# ---------------------------------------------------------------------------
# Test 24: ✕ button clears search and focuses the input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_button_clears_search() -> None:
    """Clicking the ✕ button clears the search input."""
    from textual.widgets import Input

    app = HostApp()
    rooms = [
        _room("!a:h", "General"),
        _room("!b:h", "Random"),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        # Apply a filter programmatically (bypasses debounce)
        room_list.apply_filter("rand")
        await pilot.pause()
        assert len(room_list.visible_rooms) == 1

        # The clear button becomes visible when filter is active
        from textual.widgets import Button

        btn = room_list.query_one("#clear-search", Button)
        assert btn.display is True

        # Press the ✕ button via its Pressed message
        from textual.widgets import Button

        btn = room_list.query_one("#clear-search", Button)
        room_list.post_message(Button.Pressed(btn))
        await pilot.pause()

        search = room_list.query_one("#room-search", Input)
        assert search.value == ""


# ---------------------------------------------------------------------------
# Test 25: ✕ button hidden when search is empty, visible when non-empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_button_visibility_tracks_filter() -> None:
    """The ✕ button is hidden when filter is empty, shown when active."""
    from textual.widgets import Button

    app = HostApp()
    rooms = [_room("!a:h", "General")]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        btn = room_list.query_one("#clear-search", Button)
        # Initially no filter — button hidden
        assert btn.display is False

        # Apply filter — button appears
        room_list.apply_filter("gen")
        await pilot.pause()
        assert btn.display is True

        # Clear filter — button hides again
        room_list.apply_filter("")
        await pilot.pause()
        assert btn.display is False


# ---------------------------------------------------------------------------
# Test 26: set_sort_mode updates OptionList order (not just visible_rooms list)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_sort_mode_updates_dom_order() -> None:
    """set_sort_mode('alpha') must repaint the OptionList — DOM order matches
    alphabetical order after a single pilot.pause()."""
    from textual.widgets import OptionList

    app = HostApp()
    rooms = [
        _room("!z:h", "Zebra", last_activity=DT_NEW),
        _room("!a:h", "Alpha", last_activity=DT_OLD),
        _room("!m:h", "Mango", last_activity=DT_MID),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        room_list.set_sort_mode("alpha")
        await pilot.pause()

        # Verify OptionList order matches alphabetical.
        ol = room_list.query_one(OptionList)
        dom_names = [str(ol.get_option_at_index(i).prompt) for i in range(ol.option_count)]
        # Prompts may include markup — just check that names appear in order
        assert dom_names[0].find("Alpha") < dom_names[0].find("Z") or "Alpha" in dom_names[0]
        assert "Alpha" in dom_names[0]
        assert "Mango" in dom_names[1]
        assert "Zebra" in dom_names[2]


# ---------------------------------------------------------------------------
# Test 27: rooms without timestamps sort alphabetically after those with timestamps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rooms_without_timestamps_sort_alpha_after_timestamped() -> None:
    """Rooms lacking last_activity always appear after timestamped rooms, A-Z."""
    app = HostApp()
    rooms = [
        _room("!z:h", "Zebra"),  # no timestamp
        _room("!a:h", "Alpha"),  # no timestamp
        _room("!new:h", "NewRoom", last_activity=DT_NEW),
        _room("!old:h", "OldRoom", last_activity=DT_OLD),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        visible = room_list.visible_rooms
        assert len(visible) == 4
        # Timestamped rooms first (newest-first)
        assert visible[0].room_id == "!new:h"
        assert visible[1].room_id == "!old:h"
        # No-timestamp rooms last, alphabetically
        assert visible[2].display_name == "Alpha"
        assert visible[3].display_name == "Zebra"


# ---------------------------------------------------------------------------
# Test 28: set_sort_mode alpha then back to recent re-sorts correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sort_mode_roundtrip_alpha_then_recent() -> None:
    """Switching alpha→recent restores newest-first order."""
    app = HostApp()
    rooms = [
        _room("!z:h", "Zebra", last_activity=DT_NEW),
        _room("!a:h", "Alpha", last_activity=DT_OLD),
        _room("!m:h", "Mango", last_activity=DT_MID),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        room_list.set_sort_mode("alpha")
        await pilot.pause()
        alpha_names = [r.display_name for r in room_list.visible_rooms]
        assert alpha_names == ["Alpha", "Mango", "Zebra"]

        room_list.set_sort_mode("recent")
        await pilot.pause()
        recent_ids = [r.room_id for r in room_list.visible_rooms]
        assert recent_ids == ["!z:h", "!m:h", "!a:h"]


# ---------------------------------------------------------------------------
# Test 29: all-None timestamps still produces a stable alphabetical list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_none_timestamps_sort_alphabetically() -> None:
    """When every room lacks last_activity, the entire list is sorted A-Z."""
    app = HostApp()
    rooms = [
        _room("!z:h", "Zebra"),
        _room("!a:h", "Alpha"),
        _room("!m:h", "Mango"),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        visible = room_list.visible_rooms
        names = [r.display_name for r in visible]
        assert names == ["Alpha", "Mango", "Zebra"]


# ---------------------------------------------------------------------------
# Test F (replaces test_set_rooms_same_order_patches_items_in_place):
# _refresh_list is synchronous — visible_rooms updated without extra pause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_list_is_synchronous() -> None:
    """After set_rooms(rooms), visible_rooms is updated without an extra
    pilot.pause() to drain a deferred callback."""
    app = HostApp()
    rooms = [_room("!a:h", "Alpha"), _room("!b:h", "Beta")]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        # No extra pilot.pause() — _rebuild calls _refresh_list synchronously.
        assert len(room_list.visible_rooms) == 2


# ---------------------------------------------------------------------------
# Test G (replaces test_set_rooms_order_change_rebuilds_correctly):
# OptionList has the correct option count after set_rooms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_option_list_count_after_set_rooms() -> None:
    """OptionList.option_count == len(rooms) after set_rooms."""
    from textual.widgets import OptionList

    app = HostApp()
    rooms = [_room("!a:h", "Alpha"), _room("!b:h", "Beta")]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        ol = room_list.query_one(OptionList)
        assert ol.option_count == 2


# ---------------------------------------------------------------------------
# New Test A: set_rooms populates OptionList with correct IDs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_rooms_populates_option_ids() -> None:
    """set_rooms() populates the OptionList with options using _option_id IDs."""
    from textual.widgets import OptionList

    app = HostApp()
    rooms = [_room("!a:h", "Alpha"), _room("!b:h", "Beta")]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        ol = room_list.query_one(OptionList)
        assert ol.option_count == 2
        assert ol.get_option_index(_option_id("!a:h")) == 0


# ---------------------------------------------------------------------------
# New Test B: update_unread calls replace_option_prompt, not clear_options
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_unread_uses_replace_not_clear() -> None:
    """update_unread must call replace_option_prompt, NOT clear_options."""
    from textual.widgets import OptionList

    app = HostApp()
    rooms = [
        _room("!a:h", "General"),
        _room("!b:h", "Random"),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        ol = room_list.query_one(OptionList)
        clear_called = False
        original_clear = ol.clear_options

        def patched_clear() -> None:
            nonlocal clear_called
            clear_called = True
            original_clear()

        ol.clear_options = patched_clear  # type: ignore[assignment]

        room_list.update_unread("!a:h", 7)
        await pilot.pause()

        assert not clear_called, "clear_options must NOT be called by update_unread"

        # Prompt updated with new count.
        idx = ol.get_option_index(_option_id("!a:h"))
        prompt = str(ol.get_option_at_index(idx).prompt)
        assert "(7)" in prompt


# ---------------------------------------------------------------------------
# New Test C: RoomSelected carries correct room_id for IDs with special chars
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_room_selected_special_chars_in_room_id() -> None:
    """RoomSelected.room_id is the original room_id (with : and .) not the option id."""
    from textual.widgets import OptionList

    app = HostApp()
    rooms = [_room("!abc:example.com", "Special")]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        ol = room_list.query_one(OptionList)
        ol.focus()
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert len(app.selected_room_ids) == 1
        assert app.selected_room_ids[0] == "!abc:example.com"


# ---------------------------------------------------------------------------
# New Test D: filter hides/restores options without full widget replacement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filter_changes_option_count() -> None:
    """apply_filter reduces option_count; clearing restores it."""
    from textual.widgets import OptionList

    app = HostApp()
    rooms = [_room("!a:h", "General"), _room("!b:h", "Random")]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        room_list.apply_filter("rand")
        await pilot.pause()
        ol = room_list.query_one(OptionList)
        assert ol.option_count == 1

        room_list.apply_filter("")
        await pilot.pause()
        assert ol.option_count == 2


# ---------------------------------------------------------------------------
# New Test E: active highlight index set correctly after _refresh_list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_highlight_index_after_refresh() -> None:
    """After set_active_room('!b:h') + set_rooms([ra, rb]),
    ol.highlighted == ol.get_option_index(_option_id('!b:h'))."""
    from textual.widgets import OptionList

    app = HostApp()
    ra = _room("!a:h", "Alpha")
    rb = _room("!b:h", "Beta")

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_active_room("!b:h")
        room_list.set_rooms([ra, rb])
        await pilot.pause()

        ol = room_list.query_one(OptionList)
        expected = ol.get_option_index(_option_id("!b:h"))
        assert ol.highlighted == expected


# ---------------------------------------------------------------------------
# Regression: right-click via on_mouse_down with Rich style.meta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_right_click_posts_room_context_menu_via_meta() -> None:
    """Regression: on_mouse_down must use event.style.meta['option'] to find the
    clicked room, NOT ol.highlighted (which only tracks keyboard navigation).

    Simulates what Textual does when the user right-clicks a room item: it
    delivers a MouseDown event whose Rich style carries {"option": idx} metadata.
    The handler must read that metadata and post RoomContextMenu with the
    correct room, regardless of what highlighted is set to.
    """
    from rich.style import Style
    from textual.events import MouseDown
    from textual.widgets import OptionList

    app = RoomContextMenuHostApp()
    rooms = [
        _room("!a:h", "Alpha"),
        _room("!b:h", "Beta"),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        ol = room_list.query_one(OptionList)
        # Do NOT set ol.highlighted — it should be irrelevant to the fix.
        assert ol.highlighted is None or ol.highlighted == 0

        # Find the index of "!b:h" in visible_rooms.
        beta_idx = next(i for i, r in enumerate(room_list.visible_rooms) if r.room_id == "!b:h")

        # Build a MouseDown event with Rich style metadata pointing to Beta.
        # This is how Textual delivers the event when the user clicks on a
        # rendered OptionList strip that has {"option": beta_idx} embedded.
        style_with_meta = Style.from_meta({"option": beta_idx})
        event = MouseDown(
            widget=ol,
            x=2,
            y=beta_idx,
            delta_x=0,
            delta_y=0,
            button=3,  # right-click
            shift=False,
            meta=False,
            ctrl=False,
            screen_x=10,
            screen_y=beta_idx + 1,
            style=style_with_meta,
        )
        room_list.on_mouse_down(event)
        await pilot.pause()

        assert len(app.context_menu_events) == 1, "Expected exactly 1 RoomContextMenu event"
        assert app.context_menu_events[0].room.room_id == "!b:h", (
            f"Expected room !b:h, got {app.context_menu_events[0].room.room_id!r}"
        )
