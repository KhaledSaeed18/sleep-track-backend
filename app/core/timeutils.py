"""Time helpers shared across modules.

All persisted instants are timezone-aware UTC; local dates and clock times are
derived on demand from an IANA timezone.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Sessions that start before this local hour belong to the previous night.
NIGHT_BOUNDARY_HOUR = 12


def utcnow() -> datetime:
    return datetime.now(UTC)


@lru_cache(maxsize=512)
def get_zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def is_valid_timezone(name: str) -> bool:
    try:
        get_zone(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def validate_timezone(name: str) -> str:
    if not is_valid_timezone(name):
        raise ValueError(f"unknown timezone '{name}'")
    return name


def to_local(instant: datetime, tz: str) -> datetime:
    return instant.astimezone(get_zone(tz))


def night_of(started_at: datetime, tz: str) -> date:
    """Local calendar date on which the night began."""
    local = to_local(started_at, tz)
    if local.hour < NIGHT_BOUNDARY_HOUR:
        return local.date() - timedelta(days=1)
    return local.date()


def local_today(tz: str) -> date:
    return utcnow().astimezone(get_zone(tz)).date()


def minutes_of_day(value: datetime | time) -> float:
    return value.hour * 60 + value.minute + value.second / 60


def format_hhmm(minutes: float | None) -> str | None:
    if minutes is None:
        return None
    total = round(minutes) % 1440
    return f"{total // 60:02d}:{total % 60:02d}"


def parse_hhmm(value: str) -> time:
    hours, _, minutes = value.partition(":")
    return time(int(hours), int(minutes))


def daterange(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
