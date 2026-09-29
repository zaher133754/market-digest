"""Calendar-safe digest boundary calculations."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def boundary_for_date(day: date, *, hour: int, minute: int, timezone: ZoneInfo) -> datetime:
    return datetime.combine(day, time(hour=hour, minute=minute), tzinfo=timezone)


def latest_boundary(now: datetime, *, hour: int, minute: int, timezone: ZoneInfo) -> datetime:
    """Return the latest scheduled boundary at or before ``now``."""

    local_now = now.astimezone(timezone)
    candidate = boundary_for_date(local_now.date(), hour=hour, minute=minute, timezone=timezone)
    if local_now < candidate:
        candidate = boundary_for_date(
            local_now.date() - timedelta(days=1),
            hour=hour,
            minute=minute,
            timezone=timezone,
        )
    return candidate


def previous_boundary(
    boundary: datetime, *, hour: int, minute: int, timezone: ZoneInfo
) -> datetime:
    """Return the start of the scheduled report's exact 24-hour window."""

    del hour, minute
    local_boundary = boundary.astimezone(timezone)
    start, _ = exact_24_hour_window(local_boundary)
    return start


def exact_24_hour_window(end: datetime) -> tuple[datetime, datetime]:
    """Return ``(end - 24 elapsed hours, end)`` for the open-closed report window."""

    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("Report window end must be timezone-aware")
    start_utc = end.astimezone(UTC) - timedelta(hours=24)
    return start_utc.astimezone(end.tzinfo), end


def due_boundaries(
    *,
    last_completed: datetime,
    now: datetime,
    hour: int,
    minute: int,
    timezone: ZoneInfo,
    maximum: int = 31,
) -> list[datetime]:
    """Return missed daily boundaries in chronological order.

    The cap prevents an accidental years-long catch-up loop. The caller should
    invoke this function again after each successfully persisted batch.
    """

    latest = latest_boundary(now, hour=hour, minute=minute, timezone=timezone)
    current = last_completed.astimezone(timezone)
    result: list[datetime] = []
    while len(result) < maximum:
        next_value = boundary_for_date(
            current.date() + timedelta(days=1),
            hour=hour,
            minute=minute,
            timezone=timezone,
        )
        if next_value > latest:
            break
        result.append(next_value)
        current = next_value
    return result
