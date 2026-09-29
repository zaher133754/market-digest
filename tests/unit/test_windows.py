from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from market_digest.services.windows import (
    due_boundaries,
    exact_24_hour_window,
    latest_boundary,
    previous_boundary,
)

TZ = ZoneInfo("Europe/Samara")


def test_latest_boundary_before_and_after_release_time() -> None:
    before = datetime(2026, 8, 27, 14, 59, tzinfo=TZ)
    after = datetime(2026, 8, 27, 15, 0, tzinfo=TZ)
    assert latest_boundary(before, hour=15, minute=0, timezone=TZ) == datetime(
        2026, 8, 26, 15, 0, tzinfo=TZ
    )
    assert latest_boundary(after, hour=15, minute=0, timezone=TZ) == after


def test_previous_boundary_defines_open_closed_window() -> None:
    current = datetime(2026, 8, 27, 15, 0, tzinfo=TZ)
    assert previous_boundary(current, hour=15, minute=0, timezone=TZ) == datetime(
        2026, 8, 26, 15, 0, tzinfo=TZ
    )


def test_due_boundaries_catch_up_in_order() -> None:
    values = due_boundaries(
        last_completed=datetime(2026, 8, 24, 15, 0, tzinfo=TZ),
        now=datetime(2026, 8, 27, 16, 0, tzinfo=TZ),
        hour=15,
        minute=0,
        timezone=TZ,
    )
    assert [value.day for value in values] == [25, 26, 27]


def test_exact_24_hour_window_preserves_open_closed_endpoints() -> None:
    end = datetime(2026, 9, 15, 12, 34, 56, 789000, tzinfo=TZ)

    start, returned_end = exact_24_hour_window(end)

    assert start == datetime(2026, 9, 14, 12, 34, 56, 789000, tzinfo=TZ)
    assert returned_end is end
    assert returned_end.astimezone(UTC) - start.astimezone(UTC) == timedelta(hours=24)


def test_exact_24_hour_window_rejects_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        exact_24_hour_window(datetime(2026, 9, 15, 12, 0))


def test_previous_scheduled_boundary_is_exactly_24_elapsed_hours_across_dst() -> None:
    berlin = ZoneInfo("Europe/Berlin")
    boundary = datetime(2026, 3, 30, 15, 0, tzinfo=berlin)

    previous = previous_boundary(boundary, hour=15, minute=0, timezone=berlin)

    assert boundary.astimezone(UTC) - previous.astimezone(UTC) == timedelta(hours=24)
