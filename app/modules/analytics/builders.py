"""Turn session points into analytics response models (pure, no I/O)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta

from app.core.timeutils import daterange, format_hhmm
from app.modules.analytics import calculations as calc
from app.modules.analytics.calculations import GoalTargets, NightSummary, SessionPoint
from app.modules.analytics.schemas import (
    AnalyticsOverview,
    BedtimeBucket,
    Chronotype,
    DailyPoint,
    DateRange,
    Distribution,
    DurationBucket,
    OverviewAverages,
    PreviousPeriod,
    QualityBucket,
    WeekdayStat,
)


def date_range(start: date, end: date) -> DateRange:
    return DateRange(from_=start, to=end, days=(end - start).days + 1)


def previous_range(start: date, end: date) -> tuple[date, date]:
    span = (end - start).days + 1
    return start - timedelta(days=span), start - timedelta(days=1)


def _averages(points: Sequence[SessionPoint], nights: dict[date, NightSummary]) -> OverviewAverages:
    night_list = list(nights.values())
    return OverviewAverages(
        duration_minutes=calc.mean_int(n.duration_minutes for n in night_list),
        quality=calc.mean_or_none(p.quality for p in points),
        score=calc.mean_int(p.score for p in points),
        sleep_latency_minutes=calc.mean_int(p.sleep_latency_minutes for p in points),
        awakenings=calc.mean_or_none(p.awakenings for p in points),
        mood_on_wake=calc.mean_or_none(p.mood_on_wake for p in points),
        bedtime=format_hhmm(calc.circular_mean_minutes([n.bedtime_minutes for n in night_list])),
        wake_time=format_hhmm(calc.circular_mean_minutes([n.wake_minutes for n in night_list])),
    )


def _consistency(nights: dict[date, NightSummary]) -> int | None:
    values = list(nights.values())
    return calc.consistency_score(
        [n.bedtime_minutes for n in values], [n.wake_minutes for n in values]
    )


def build_overview(
    *,
    points: Sequence[SessionPoint],
    previous_points: Sequence[SessionPoint],
    start: date,
    end: date,
    goal: GoalTargets,
    tz: str,
    today: date,
) -> AnalyticsOverview:
    nights = calc.summarize_nights(points, tz)
    current_streak, longest_streak = calc.streaks(nights, start, end, goal.duration_minutes, today)

    previous: PreviousPeriod | None = None
    if previous_points:
        prev_nights = calc.summarize_nights(previous_points, tz)
        prev_avg = _averages(previous_points, prev_nights)
        previous = PreviousPeriod(
            duration_minutes=prev_avg.duration_minutes,
            quality=prev_avg.quality,
            score=prev_avg.score,
            consistency_score=_consistency(prev_nights),
        )

    return AnalyticsOverview(
        range=date_range(start, end),
        nights_tracked=len(nights),
        total_sleep_minutes=sum(n.duration_minutes for n in nights.values()),
        averages=_averages(points, nights),
        consistency_score=_consistency(nights),
        goal_attainment_rate=calc.goal_attainment_rate(nights.values(), goal.duration_minutes),
        sleep_debt_minutes=calc.sleep_debt_minutes(nights, end, goal.duration_minutes),
        current_streak=current_streak,
        longest_streak=longest_streak,
        previous_period=previous,
    )


def build_daily(
    points: Sequence[SessionPoint], start: date, end: date, tz: str
) -> list[DailyPoint]:
    nights = calc.summarize_nights(points, tz)
    series: list[DailyPoint] = []
    for day in daterange(start, end):
        night = nights.get(day)
        if night is None:
            series.append(
                DailyPoint(
                    date=day,
                    duration_minutes=None,
                    quality=None,
                    score=None,
                    bedtime=None,
                    wake_time=None,
                    sessions=0,
                )
            )
            continue
        series.append(
            DailyPoint(
                date=day,
                duration_minutes=night.duration_minutes,
                quality=round(night.quality, 1) if night.quality is not None else None,
                score=round(night.score) if night.score is not None else None,
                bedtime=format_hhmm(night.bedtime_minutes),
                wake_time=format_hhmm(night.wake_minutes),
                sessions=night.sessions,
            )
        )
    return series


def build_weekday(points: Sequence[SessionPoint], tz: str) -> list[WeekdayStat]:
    nights = calc.summarize_nights(points, tz)
    by_weekday: dict[int, list[NightSummary]] = defaultdict(list)
    for night in nights.values():
        by_weekday[night.night_of.weekday()].append(night)
    stats: list[WeekdayStat] = []
    for weekday in range(7):
        group = by_weekday.get(weekday, [])
        stats.append(
            WeekdayStat(
                weekday=weekday,
                nights=len(group),
                avg_duration_minutes=calc.mean_int(n.duration_minutes for n in group),
                avg_quality=calc.mean_or_none(n.quality for n in group),
                avg_bedtime=format_hhmm(
                    calc.circular_mean_minutes([n.bedtime_minutes for n in group])
                ),
                avg_wake_time=format_hhmm(
                    calc.circular_mean_minutes([n.wake_minutes for n in group])
                ),
            )
        )
    return stats


def build_distribution(points: Sequence[SessionPoint], tz: str) -> Distribution:
    durations, qualities, hours = calc.distribution(points, tz)
    return Distribution(
        duration=[DurationBucket(bucket=label, count=count) for label, count in durations.items()],
        quality=[QualityBucket(rating=rating, count=count) for rating, count in qualities.items()],
        bedtime=[BedtimeBucket(hour=hour, count=count) for hour, count in hours.items()],
    )


def build_chronotype(points: Sequence[SessionPoint], tz: str) -> Chronotype:
    result = calc.chronotype(points, tz)
    return Chronotype(
        chronotype=result.chronotype,
        mid_sleep=format_hhmm(result.mid_sleep_minutes),
        social_jetlag_minutes=result.social_jetlag_minutes,
        nights_analyzed=result.nights_analyzed,
    )
