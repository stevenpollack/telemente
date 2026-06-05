"""Room sorting utilities shared between the TUI widget and tests."""

from __future__ import annotations

from datetime import datetime

from telemente.matrix.models import RoomSummary


def sort_rooms_by_recency(rooms: list[RoomSummary]) -> list[RoomSummary]:
    """Sort rooms newest-activity-first, then A-Z by display_name for the rest."""
    rooms_with_dt = [r for r in rooms if r.last_activity is not None]
    rooms_without_dt = [r for r in rooms if r.last_activity is None]

    def _by_activity(r: RoomSummary) -> datetime:
        assert r.last_activity is not None
        return r.last_activity

    rooms_with_dt.sort(key=_by_activity, reverse=True)
    rooms_without_dt.sort(key=lambda r: r.display_name)
    return rooms_with_dt + rooms_without_dt
