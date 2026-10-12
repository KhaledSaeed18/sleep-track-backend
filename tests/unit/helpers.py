"""Builders shared by the unit tests."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.timeutils import NIGHT_BOUNDARY_HOUR, parse_hhmm
from app.modules.analytics.calculations import NightSummary, SessionPoint


def make_point(
    night: date,
    bedtime: str,
    duration: int,
    *,
    tz: str = "UTC",
    quality: int | None = None,
    mood_on_wake: int | None = None,
    latency: int | None = None,
    awakenings: int | None = None,
    score: int | None = None,
    tags: Iterable[uuid.UUID] = (),
) -> SessionPoint:
    """A session for ``night`` starting at local ``bedtime`` (HH:MM) in ``tz``.

    Bedtimes before noon are placed on the following calendar day, matching the
    ``night_of`` rule used by the application.
    """
    clock = parse_hhmm(bedtime)
    day = night if clock.hour >= NIGHT_BOUNDARY_HOUR else night + timedelta(days=1)
    started = datetime.combine(day, clock, tzinfo=ZoneInfo(tz)).astimezone(UTC)
    return SessionPoint(
        id=uuid.uuid4(),
        night_of=night,
        started_at=started,
        ended_at=started + timedelta(minutes=duration),
        duration_minutes=duration,
        quality=quality,
        mood_on_wake=mood_on_wake,
        sleep_latency_minutes=latency,
        awakenings=awakenings,
        score=score,
        tag_ids=frozenset(tags),
    )


def make_night(
    night: date,
    duration: int,
    *,
    quality: float | None = None,
    score: float | None = None,
    bedtime_minutes: float = 23 * 60,
    wake_minutes: float = 7 * 60,
    sessions: int = 1,
) -> NightSummary:
    return NightSummary(
        night_of=night,
        duration_minutes=duration,
        quality=quality,
        score=score,
        bedtime_minutes=bedtime_minutes,
        wake_minutes=wake_minutes,
        sessions=sessions,
    )


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def at(day: date, clock: str, tz: str = "UTC") -> datetime:
    """Aware datetime for a local wall-clock time on ``day``."""
    parsed: time = parse_hhmm(clock)
    return datetime.combine(day, parsed, tzinfo=ZoneInfo(tz))
