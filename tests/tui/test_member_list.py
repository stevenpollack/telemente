"""Tests for the MemberList widget (plan 0008).

All tests inject FakeMatrixClient — no real network.
A minimal host App mounts MemberList with the fake client.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

import fakes as fakes_module
from telemente.matrix.models import Member
from telemente.tui.widgets.member_list import MemberList

FakeMatrixClient = fakes_module.FakeMatrixClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _member(user_id: str, display_name: str, power_level: int = 0) -> Member:
    return Member(user_id=user_id, display_name=display_name, power_level=power_level)


def _rendered_text(widget: MemberList) -> str:
    """Return all rendered member text joined together."""
    from textual.widgets import Label

    labels = widget.query(Label)
    return "\n".join(str(label.render()) for label in labels)


# ---------------------------------------------------------------------------
# Host app
# ---------------------------------------------------------------------------


class HostApp(App[None]):
    """Minimal app that mounts a MemberList with a FakeMatrixClient."""

    def __init__(self, client: FakeMatrixClient) -> None:
        super().__init__()
        self._client = client

    def compose(self) -> ComposeResult:
        yield MemberList(self._client, id="members-panel")


# ---------------------------------------------------------------------------
# Test 1: load_room renders members and updates member_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_room_renders_members() -> None:
    fake = FakeMatrixClient()
    fake._members["!room:s"] = [
        _member("@alice:s", "Alice"),
        _member("@bob:s", "Bob"),
        _member("@carol:s", "Carol"),
    ]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        ml = app.query_one(MemberList)
        ml.load_room("!room:s")
        await pilot.pause()

        assert ml.member_count == 3
        rendered = _rendered_text(ml)
        assert "Alice" in rendered
        assert "Bob" in rendered
        assert "Carol" in rendered


# ---------------------------------------------------------------------------
# Test 2: members sorted by power level desc then display name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sorted_by_power_then_name() -> None:
    fake = FakeMatrixClient()
    fake._members["!room:s"] = [
        _member("@charlie:s", "Charlie", power_level=0),
        _member("@alice:s", "Alice", power_level=100),
        _member("@bob:s", "Bob", power_level=50),
        _member("@dave:s", "Dave", power_level=50),
    ]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        ml = app.query_one(MemberList)
        ml.load_room("!room:s")
        await pilot.pause()

        rendered = _rendered_text(ml)
        # Admin first
        alice_pos = rendered.index("Alice")
        bob_pos = rendered.index("Bob")
        dave_pos = rendered.index("Dave")
        charlie_pos = rendered.index("Charlie")

        assert alice_pos < bob_pos
        assert alice_pos < dave_pos
        # Bob before Dave (same power level, alphabetical)
        assert bob_pos < dave_pos
        # Both mods before Charlie (power 0)
        assert bob_pos < charlie_pos
        assert dave_pos < charlie_pos


# ---------------------------------------------------------------------------
# Test 3: power level marker rendered for admin (>=100) and mod (>=50)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_power_level_marker() -> None:
    fake = FakeMatrixClient()
    fake._members["!room:s"] = [
        _member("@admin:s", "AdminUser", power_level=100),
        _member("@mod:s", "ModUser", power_level=50),
        _member("@user:s", "RegularUser", power_level=0),
    ]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        ml = app.query_one(MemberList)
        ml.load_room("!room:s")
        await pilot.pause()

        rendered = _rendered_text(ml)
        # Admin marker
        assert "~ AdminUser" in rendered
        # Mod marker
        assert "+ ModUser" in rendered
        # Regular user — no marker
        assert "RegularUser" in rendered
        assert "~ RegularUser" not in rendered
        assert "+ RegularUser" not in rendered


# ---------------------------------------------------------------------------
# Test 4: switching rooms updates the list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switching_rooms_updates_list() -> None:
    fake = FakeMatrixClient()
    fake._members["!roomA:s"] = [
        _member("@alice:s", "Alice"),
        _member("@bob:s", "Bob"),
    ]
    fake._members["!roomB:s"] = [
        _member("@carol:s", "Carol"),
        _member("@dave:s", "Dave"),
        _member("@eve:s", "Eve"),
        _member("@frank:s", "Frank"),
    ]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        ml = app.query_one(MemberList)

        ml.load_room("!roomA:s")
        await pilot.pause()
        assert ml.member_count == 2

        ml.load_room("!roomB:s")
        await pilot.pause()
        assert ml.member_count == 4

        rendered = _rendered_text(ml)
        assert "Carol" in rendered
        assert "Dave" in rendered
        assert "Eve" in rendered
        assert "Frank" in rendered
        assert "Alice" not in rendered
        assert "Bob" not in rendered


# ---------------------------------------------------------------------------
# Test 5: set_members updates the render and count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_members_updates_render() -> None:
    fake = FakeMatrixClient()
    fake._members["!room:s"] = [
        _member("@alice:s", "Alice"),
        _member("@bob:s", "Bob"),
    ]

    app = HostApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause()
        ml = app.query_one(MemberList)
        ml.load_room("!room:s")
        await pilot.pause()
        assert ml.member_count == 2

        # Simulate a join event — new member arrives
        new_members = [
            _member("@alice:s", "Alice"),
            _member("@bob:s", "Bob"),
            _member("@newbie:s", "Newbie"),
        ]
        ml.set_members(new_members)
        await pilot.pause()

        assert ml.member_count == 3
        rendered = _rendered_text(ml)
        assert "Newbie" in rendered
        assert "Alice" in rendered
        assert "Bob" in rendered
