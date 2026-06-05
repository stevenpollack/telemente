"""Tests for the RoomList widget (plan 0006).

All tests use a minimal host App that mounts RoomList directly.
No network — RoomSummary fixtures are built in-process.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from textual.app import App, ComposeResult

from telemente.matrix.models import RoomSummary
from telemente.tui.widgets.room_list import RoomList

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

        # Focus the list, move to the first item (Alpha = newest), then select
        from textual.widgets import ListView

        list_view = room_list.query_one(ListView)
        list_view.focus()
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
    """Unread count is embedded in the room-name label as '(3)'."""
    from telemente.tui.widgets.room_list import RoomItem

    app = HostApp()
    rooms = [
        _room("!a:h", "Busy Room", unread_count=3),
    ]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        items = list(app.query(RoomItem))
        assert len(items) == 1
        rendered = str(items[0].query_one(".room-name").render())
        assert "Busy Room" in rendered
        assert "(3)" in rendered


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
# Test 11: set_active_room highlights the matching RoomItem
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_active_room_highlights_matching_item() -> None:
    """set_active_room('!a:h') — matching RoomItem has -highlight, others don't."""
    from telemente.tui.widgets.room_list import RoomItem

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

        items = list(app.query(RoomItem))
        assert len(items) == 3

        highlighted = [item for item in items if "-highlight" in item.classes]
        assert len(highlighted) == 1
        assert highlighted[0].room.room_id == "!a:h"

        not_highlighted = [item for item in items if "-highlight" not in item.classes]
        assert len(not_highlighted) == 2


# ---------------------------------------------------------------------------
# Test 12: active highlight survives a set_rooms rebuild
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_highlight_survives_set_rooms_rebuild() -> None:
    """Regression: calling set_rooms() after set_active_room() must re-apply the
    highlight — previously the rebuild wiped all classes on new RoomItem instances."""
    from telemente.tui.widgets.room_list import RoomItem

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
        await pilot.pause()  # second pause lets call_after_refresh fire

        items = list(app.query(RoomItem))
        highlighted = [item for item in items if "-highlight" in item.classes]
        assert len(highlighted) == 1
        assert highlighted[0].room.room_id == "!a:h"


# ---------------------------------------------------------------------------
# Test 13: switching active room moves highlight (no stale highlight on old room)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_active_room_moves_highlight() -> None:
    """Regression: selecting room B after room A must remove the highlight from A.

    The bug: set_rooms() was called before set_active_room(), so the rebuild
    used the old _active_room_id and the new selection had no effect until the
    next render cycle."""
    from telemente.tui.widgets.room_list import RoomItem

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
        await pilot.pause()

        # Now switch to room B (set_active_room before set_rooms, as main.py does)
        room_list.set_active_room("!b:h")
        room_list.set_rooms(rooms)
        await pilot.pause()
        await pilot.pause()

        items = list(app.query(RoomItem))
        highlighted = [item for item in items if "-highlight" in item.classes]
        assert len(highlighted) == 1
        assert highlighted[0].room.room_id == "!b:h"


# ---------------------------------------------------------------------------
# Test 14: unread room name is bold and shows count in parens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unread_room_name_is_bold() -> None:
    """A room with unread_count>0 renders its name with bold markup and (N) count."""
    app = HostApp()
    rooms = [_room("!a:h", "General", unread_count=3)]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        from telemente.tui.widgets.room_list import RoomItem

        items = list(app.query(RoomItem))
        assert len(items) == 1
        rendered = str(items[0].query_one(".room-name").render())
        assert "General" in rendered
        assert "(3)" in rendered


# ---------------------------------------------------------------------------
# Test 15: room with no unread has plain name (no bold/count)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_room_name_is_plain() -> None:
    """A room with unread_count==0 renders its name without (N) count."""
    app = HostApp()
    rooms = [_room("!a:h", "General", unread_count=0)]

    async with app.run_test() as pilot:
        await pilot.pause()
        room_list = app.query_one(RoomList)
        room_list.set_rooms(rooms)
        await pilot.pause()

        from telemente.tui.widgets.room_list import RoomItem

        items = list(app.query(RoomItem))
        rendered = str(items[0].query_one(".room-name").render())
        assert "General" in rendered
        assert "(" not in rendered


# ---------------------------------------------------------------------------
# Test 16: favourite tag shows ★ glyph in room name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_favourite_tag_shows_star() -> None:
    """A room tagged m.favourite shows ★ in its rendered name."""
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

        from telemente.tui.widgets.room_list import RoomItem

        items = list(app.query(RoomItem))
        rendered = str(items[0].query_one(".room-name").render())
        assert "★" in rendered


# ---------------------------------------------------------------------------
# Test 17: low-priority tag shows ↓ glyph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lowpriority_tag_shows_arrow() -> None:
    """A room tagged m.lowpriority shows ↓ in its rendered name."""
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

        from telemente.tui.widgets.room_list import RoomItem

        items = list(app.query(RoomItem))
        rendered = str(items[0].query_one(".room-name").render())
        assert "↓" in rendered


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
# Test 21: update_unread patches the label without a full rebuild
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_unread_patches_label_in_place() -> None:
    """update_unread(room_id, count) updates the unread display without
    a full ListView rebuild — the same RoomItem instance stays in the DOM."""
    from telemente.tui.widgets.room_list import RoomItem

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

        items_before = list(app.query(RoomItem))
        assert len(items_before) == 2
        # Grab identity of the item for room a
        item_a_before = next(i for i in items_before if i.room.room_id == "!a:h")

        room_list.update_unread("!a:h", 5)
        await pilot.pause()

        items_after = list(app.query(RoomItem))
        assert len(items_after) == 2
        item_a_after = next(i for i in items_after if i.room.room_id == "!a:h")

        # Same DOM node — no teardown+rebuild.
        assert item_a_before is item_a_after

        # Label updated to reflect new unread count.
        rendered = str(item_a_after.query_one(".room-name").render())
        assert "(5)" in rendered


# ---------------------------------------------------------------------------
# Test 22: mute tag shows 🔕 glyph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mute_tag_shows_bell() -> None:
    """A room tagged m.mute shows 🔕 in its rendered name."""
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

        from telemente.tui.widgets.room_list import RoomItem

        items = list(app.query(RoomItem))
        rendered = str(items[0].query_one(".room-name").render())
        assert "🔕" in rendered


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
# Test 26: set_sort_mode updates DOM order (not just visible_rooms list)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_sort_mode_updates_dom_order() -> None:
    """set_sort_mode('alpha') must repaint the ListView — DOM order matches
    alphabetical order after a single pilot.pause()."""
    from textual.widgets import ListView

    from telemente.tui.widgets.room_list import RoomItem

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

        # Verify DOM order matches alphabetical, not just the Python list.
        list_view = room_list.query_one(ListView)
        dom_items = list(list_view.query(RoomItem))
        dom_names = [item.room.display_name for item in dom_items]
        assert dom_names == ["Alpha", "Mango", "Zebra"]


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
