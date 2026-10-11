"""Pure sleep-analytics functions.

Everything here is deterministic and free of I/O so it can be unit-tested
exhaustively. Clock times are handled as *minutes after local midnight*
(0 <= m < 1440) and averaged on the circle, so 23:30 and 00:30 average to
00:00 rather than to noon. See ``docs/analytics.md`` for the formulas.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import fmean
from typing import Literal

from app.core.timeutils import daterange, minutes_of_day, to_local

MINUTES_PER_DAY = 1440
GOAL_TOLERANCE_MINUTES = 15
SLEEP_DEBT_WINDOW_DAYS = 14
CONSISTENCY_ZERO_AT_STD_MINUTES = 120
CHRONOTYPE_MIN_NIGHTS = 7
FREE_NIGHT_WEEKDAYS = frozenset({4, 5})  # Friday and Saturday nights

SCORE_WEIGHTS = {
    "duration": 0.40,
    "quality": 0.25,
    "latency": 0.15,
    "awakenings": 0.10,
    "bedtime": 0.10,
}

DurationLabel = Literal["<5h", "5-6h", "6-7h", "7-8h", "8-9h", "9h+"]
ChronotypeLabel = Literal["early_bird", "intermediate", "night_owl", "unknown"]

DURATION_BUCKETS: tuple[tuple[DurationLabel, int, int | None], ...] = (
    ("<5h", 0, 300),
    ("5-6h", 300, 360),
    ("6-7h", 360, 420),
    ("7-8h", 420, 480),
    ("8-9h", 480, 540),
    ("9h+", 540, None),
)


# =============================================================================== data
@dataclass(frozen=True, slots=True)
class GoalTargets:
    duration_minutes: int = 480
    bedtime_minutes: float = 23 * 60
    wake_minutes: float = 7 * 60
    window_minutes: int = 30


@dataclass(frozen=True, slots=True)
class SessionPoint:
    """A completed session reduced to the values analytics needs."""

    id: uuid.UUID
    night_of: date
    started_at: datetime
    ended_at: datetime
    duration_minutes: int
    quality: int | None = None
    mood_on_wake: int | None = None
    sleep_latency_minutes: int | None = None
    awakenings: int | None = None
    score: int | None = None
    tag_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class NightSummary:
    """All sessions that belong to one ``night_of`` (main sleep plus naps)."""

    night_of: date
    duration_minutes: int
    quality: float | None
    score: float | None
    bedtime_minutes: float
    wake_minutes: float
    sessions: int


# =============================================================================== circular
def _to_angle(minutes: float) -> float:
    return (minutes % MINUTES_PER_DAY) / MINUTES_PER_DAY * 2 * math.pi


def _resultant(values: Sequence[float]) -> tuple[float, float, float]:
    sin_sum = sum(math.sin(_to_angle(v)) for v in values)
    cos_sum = sum(math.cos(_to_angle(v)) for v in values)
    length = math.hypot(sin_sum, cos_sum) / len(values)
    return sin_sum, cos_sum, length


def circular_mean_minutes(values: Sequence[float]) -> float | None:
    """Mean clock time in minutes after midnight, or ``None`` if undefined."""
    if not values:
        return None
    sin_sum, cos_sum, length = _resultant(values)
    if length < 1e-9:  # perfectly balanced around the clock
        return None
    angle = math.atan2(sin_sum, cos_sum) % (2 * math.pi)
    return angle / (2 * math.pi) * MINUTES_PER_DAY


def circular_std_minutes(values: Sequence[float]) -> float | None:
    """Circular standard deviation ``sqrt(-2 ln R)`` expressed in minutes."""
    if len(values) < 2:
        return None
    _, _, length = _resultant(values)
    length = min(max(length, 1e-12), 1.0)
    return math.sqrt(-2 * math.log(length)) / (2 * math.pi) * MINUTES_PER_DAY


def circular_diff_minutes(a: float, b: float) -> float:
    """Signed shortest distance from ``b`` to ``a`` on the clock, in (-720, 720]."""
    diff = (a - b) % MINUTES_PER_DAY
    return diff - MINUTES_PER_DAY if diff > MINUTES_PER_DAY / 2 else diff


# =============================================================================== score
def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def duration_component(duration: float, target: float) -> float:
    """1.0 at target (or up to an hour over); linear to 0 at half the target or 4h over."""
    if duration >= target:
        excess = duration - target
        return 1.0 if excess <= 60 else _clamp(1 - (excess - 60) / 180)
    return _clamp(1 - (target - duration) / (target / 2))


def quality_component(quality: int) -> float:
    return _clamp((quality - 1) / 4)


def latency_component(latency: int) -> float:
    """Falling asleep within 15 minutes is ideal; 60+ minutes scores zero."""
    if latency <= 15:
        return 1.0
    return _clamp(1 - (latency - 15) / 45)


def awakenings_component(awakenings: int) -> float:
    """One awakening is normal; each additional one costs 20 %."""
    if awakenings <= 1:
        return 1.0
    return _clamp(1 - (awakenings - 1) / 5)


def bedtime_component(bedtime: float, target: float, window: int) -> float:
    """Full marks inside the goal window; linear to 0 at 90 minutes beyond it."""
    deviation = abs(circular_diff_minutes(bedtime, target))
    if deviation <= window:
        return 1.0
    return _clamp(1 - (deviation - window) / 90)


def sleep_score(
    *,
    duration_minutes: int,
    bedtime_minutes: float,
    goal: GoalTargets,
    quality: int | None = None,
    sleep_latency_minutes: int | None = None,
    awakenings: int | None = None,
) -> int:
    """Weighted 0-100 score; missing optional inputs are re-weighted away."""
    components: dict[str, float] = {
        "duration": duration_component(duration_minutes, goal.duration_minutes),
        "bedtime": bedtime_component(bedtime_minutes, goal.bedtime_minutes, goal.window_minutes),
    }
    if quality is not None:
        components["quality"] = quality_component(quality)
    if sleep_latency_minutes is not None:
        components["latency"] = latency_component(sleep_latency_minutes)
    if awakenings is not None:
        components["awakenings"] = awakenings_component(awakenings)
    total_weight = sum(SCORE_WEIGHTS[name] for name in components)
    weighted = sum(SCORE_WEIGHTS[name] * value for name, value in components.items())
    return round(weighted / total_weight * 100)


# =============================================================================== helpers
def mean_or_none(values: Iterable[float | None], digits: int | None = 1) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    result = fmean(present)
    return round(result, digits) if digits is not None else result


def mean_int(values: Iterable[float | None]) -> int | None:
    result = mean_or_none(values, digits=None)
    return round(result) if result is not None else None


def local_minutes(instant: datetime, tz: str) -> float:
    return minutes_of_day(to_local(instant, tz))


def meets_goal(duration_minutes: float, target: int) -> bool:
    return duration_minutes >= target - GOAL_TOLERANCE_MINUTES


# =============================================================================== nights
def summarize_nights(points: Sequence[SessionPoint], tz: str) -> dict[date, NightSummary]:
    grouped: dict[date, list[SessionPoint]] = defaultdict(list)
    for point in points:
        grouped[point.night_of].append(point)
    nights: dict[date, NightSummary] = {}
    for night, sessions in grouped.items():
        main = max(sessions, key=lambda s: s.duration_minutes)
        nights[night] = NightSummary(
            night_of=night,
            duration_minutes=sum(s.duration_minutes for s in sessions),
            quality=mean_or_none(s.quality for s in sessions),
            score=mean_or_none((s.score for s in sessions), digits=None),
            bedtime_minutes=local_minutes(main.started_at, tz),
            wake_minutes=local_minutes(main.ended_at, tz),
            sessions=len(sessions),
        )
    return nights


def consistency_score(bedtimes: Sequence[float], wake_times: Sequence[float]) -> int | None:
    """100 minus the scaled mean circular std-dev of bedtime and wake time."""
    bed_std = circular_std_minutes(bedtimes)
    wake_std = circular_std_minutes(wake_times)
    if bed_std is None or wake_std is None:
        return None
    spread = (bed_std + wake_std) / 2
    return round(_clamp(1 - spread / CONSISTENCY_ZERO_AT_STD_MINUTES) * 100)


def goal_attainment_rate(nights: Iterable[NightSummary], target: int) -> float | None:
    durations = [n.duration_minutes for n in nights]
    if not durations:
        return None
    return round(sum(meets_goal(d, target) for d in durations) / len(durations), 2)


def sleep_debt_minutes(nights: dict[date, NightSummary], end: date, target: int) -> int:
    """Net shortfall versus the goal over the trailing 14 days (never negative).

    Surplus nights offset earlier deficits, mirroring how recovery sleep repays
    debt; untracked nights are ignored rather than counted as zero sleep.
    """
    start = end - timedelta(days=SLEEP_DEBT_WINDOW_DAYS - 1)
    balance = sum(
        target - night.duration_minutes for day, night in nights.items() if start <= day <= end
    )
    return max(0, round(balance))


def streaks(
    nights: dict[date, NightSummary], start: date, end: date, target: int, today: date
) -> tuple[int, int]:
    """(current, longest) runs of consecutive nights meeting the duration goal.

    Tonight cannot be logged yet, so an unlogged night at ``end`` is skipped
    when ``end`` is today or later instead of breaking the current streak.
    """
    met = {day: meets_goal(n.duration_minutes, target) for day, n in nights.items()}
    longest = run = 0
    for day in daterange(start, end):
        run = run + 1 if met.get(day) else 0
        longest = max(longest, run)

    current = 0
    cursor = end
    if cursor not in met and cursor >= today - timedelta(days=1):
        cursor -= timedelta(days=1)
    while cursor >= start and met.get(cursor):
        current += 1
        cursor -= timedelta(days=1)
    return current, longest


# =============================================================================== chronotype
@dataclass(frozen=True, slots=True)
class ChronotypeResult:
    chronotype: ChronotypeLabel
    mid_sleep_minutes: float | None
    social_jetlag_minutes: int | None
    nights_analyzed: int


def _signed_from_midnight(minutes: float) -> float:
    """Map clock minutes to (-360, 1080] so late-evening values sort before 6 a.m."""
    return minutes - MINUTES_PER_DAY if minutes > 1080 else minutes


def classify_chronotype(corrected_mid_sleep: float) -> ChronotypeLabel:
    signed = _signed_from_midnight(corrected_mid_sleep)
    if signed < 3 * 60:
        return "early_bird"
    if signed > 5 * 60:
        return "night_owl"
    return "intermediate"


def chronotype(points: Sequence[SessionPoint], tz: str) -> ChronotypeResult:
    """MCTQ-style chronotype from mid-sleep on free (Fri/Sat) vs work nights.

    Classification uses mid-sleep on free days corrected for oversleep
    (MSFsc = MSF - (SDf - SDweek) / 2) when free nights run longer.
    """
    free = [p for p in points if p.night_of.weekday() in FREE_NIGHT_WEEKDAYS]
    work = [p for p in points if p.night_of.weekday() not in FREE_NIGHT_WEEKDAYS]

    def mid(point: SessionPoint) -> float:
        return local_minutes(point.started_at, tz) + point.duration_minutes / 2

    if len(points) < CHRONOTYPE_MIN_NIGHTS:
        overall = circular_mean_minutes([mid(p) for p in points]) if points else None
        return ChronotypeResult("unknown", overall, None, len(points))

    msf = circular_mean_minutes([mid(p) for p in free]) if len(free) >= 2 else None
    msw = circular_mean_minutes([mid(p) for p in work]) if len(work) >= 2 else None
    reference = msf if msf is not None else circular_mean_minutes([mid(p) for p in points])
    if reference is None:
        return ChronotypeResult("unknown", None, None, len(points))

    corrected = reference
    if msf is not None and work:
        sd_free = fmean(p.duration_minutes for p in free)
        sd_work = fmean(p.duration_minutes for p in work)
        if sd_free > sd_work:
            sd_week = (5 * sd_work + 2 * sd_free) / 7
            corrected = (msf - (sd_free - sd_week) / 2) % MINUTES_PER_DAY

    jetlag = (
        round(abs(circular_diff_minutes(msf, msw))) if msf is not None and msw is not None else None
    )
    return ChronotypeResult(classify_chronotype(corrected), reference, jetlag, len(points))


# =============================================================================== tags
@dataclass(frozen=True, slots=True)
class TagImpact:
    tag_id: uuid.UUID
    nights_with: int
    avg_quality_with: float | None
    avg_quality_without: float | None
    avg_duration_with: int | None
    avg_duration_without: int | None
    quality_impact: float | None
    duration_impact_minutes: int | None


def tag_impacts(points: Sequence[SessionPoint], tag_ids: Iterable[uuid.UUID]) -> list[TagImpact]:
    """Compare sessions with and without each tag; sorted by |quality impact|."""
    results: list[TagImpact] = []
    for tag_id in tag_ids:
        with_tag = [p for p in points if tag_id in p.tag_ids]
        if not with_tag:
            continue
        without = [p for p in points if tag_id not in p.tag_ids]
        q_with = mean_or_none((p.quality for p in with_tag), digits=None)
        q_without = mean_or_none((p.quality for p in without), digits=None)
        d_with = mean_or_none((p.duration_minutes for p in with_tag), digits=None)
        d_without = mean_or_none((p.duration_minutes for p in without), digits=None)
        results.append(
            TagImpact(
                tag_id=tag_id,
                nights_with=len(with_tag),
                avg_quality_with=round(q_with, 1) if q_with is not None else None,
                avg_quality_without=round(q_without, 1) if q_without is not None else None,
                avg_duration_with=round(d_with) if d_with is not None else None,
                avg_duration_without=round(d_without) if d_without is not None else None,
                quality_impact=(
                    round(q_with - q_without, 2)
                    if q_with is not None and q_without is not None
                    else None
                ),
                duration_impact_minutes=(
                    round(d_with - d_without)
                    if d_with is not None and d_without is not None
                    else None
                ),
            )
        )
    results.sort(key=lambda r: (-abs(r.quality_impact or 0), -r.nights_with))
    return results


# =============================================================================== distribution
def duration_bucket(minutes: int) -> DurationLabel:
    for label, low, high in DURATION_BUCKETS:
        if minutes >= low and (high is None or minutes < high):
            return label
    return DURATION_BUCKETS[0][0]


def distribution(
    points: Sequence[SessionPoint], tz: str
) -> tuple[dict[DurationLabel, int], dict[int, int], dict[int, int]]:
    durations: dict[DurationLabel, int] = {label: 0 for label, _, _ in DURATION_BUCKETS}
    qualities = dict.fromkeys(range(1, 6), 0)
    hours = dict.fromkeys(range(24), 0)
    for point in points:
        durations[duration_bucket(point.duration_minutes)] += 1
        if point.quality is not None:
            qualities[point.quality] += 1
        hours[to_local(point.started_at, tz).hour] += 1
    return durations, qualities, hours
