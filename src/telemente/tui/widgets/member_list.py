"""MemberList widget — scrollable member list for the selected room (plan 0008).

Displays the members of a Matrix room sorted by power level (descending) then
display name, with ASCII markers for elevated power levels.
The widget never imports nio directly — all data access goes through the
injected client protocol.
"""

from __future__ import annotations

import logging
from typing import ClassVar, Protocol

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Label, Static

from telemente.matrix.models import Member
from telemente.tui.colors import sender_color

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Client protocol
# ---------------------------------------------------------------------------


class _MemberListClient(Protocol):
    """Structural protocol for the subset of MatrixClient used by MemberList."""

    def members(self, room_id: str) -> list[Member]: ...


# ---------------------------------------------------------------------------
# Power level helpers
# ---------------------------------------------------------------------------

_ADMIN_THRESHOLD = 100
_MOD_THRESHOLD = 50


def _power_marker(power_level: int) -> str:
    """Return an ASCII marker string for the given power level."""
    if power_level >= _ADMIN_THRESHOLD:
        return "~ "
    if power_level >= _MOD_THRESHOLD:
        return "+ "
    return ""


def _sort_key(member: Member) -> tuple[int, str]:
    """Sort key: power level descending, then display name ascending."""
    return (-member.power_level, member.display_name.casefold())


def _format_member(member: Member) -> str:
    """Format a member for display: optional marker + display name."""
    return f"{_power_marker(member.power_level)}{member.display_name}"


# ---------------------------------------------------------------------------
# MemberList widget
# ---------------------------------------------------------------------------


class MemberList(Widget):
    """Right panel: scrollable member list for the current room.

    Public API
    ----------
    load_room(room_id)          Pull members from client and render.
    set_members(members)        Replace the member list and re-render.
    member_count                Number of members currently displayed.
    """

    BINDINGS: ClassVar[list[BindingType]] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(
        self,
        client: _MemberListClient,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._client = client
        self._current_room_id: str | None = None
        self._members: list[Member] = []

    def compose(self) -> ComposeResult:
        yield Static("Members — 0", id="member-list-header")
        with VerticalScroll(id="member-list-scroll"):
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_room(self, room_id: str) -> None:
        """Pull members from the client for *room_id* and render them."""
        logger.info("load_room: room_id=%s", room_id)
        self._current_room_id = room_id
        members = self._client.members(room_id)
        logger.debug("Loaded %d members for room %s", len(members), room_id)
        self.set_members(members)

    def set_members(self, members: list[Member]) -> None:
        """Replace the member list and re-render."""
        self._members = sorted(members, key=_sort_key)
        self._refresh()

    @property
    def member_count(self) -> int:
        """Number of members currently displayed."""
        return len(self._members)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """Rebuild the member list DOM to match _members."""
        # Update header
        header = self.query_one("#member-list-header", Static)
        header.update(f"Members — {len(self._members)}")

        # Rebuild member entries
        scroll = self.query_one("#member-list-scroll", VerticalScroll)
        for label in scroll.query(Label):
            label.remove()
        for member in self._members:
            color = sender_color(member.user_id)
            text = f"[{color}]{_format_member(member)}[/{color}]"
            scroll.mount(Label(text, classes="member-entry", markup=True))
