from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.core.timeutils import daterange
from app.modules.analytics import builders
from app.modules.analytics.calculations import FREE_NIGHT_WEEKDAYS, GoalTargets, SessionPoint
from app.modules.analytics.schemas import PreviousPeriod
from tests.unit.helpers import make_point

START = date(2026, 3, 2)  # Monday
END = date(2026, 3, 8)
TODAY = date(2026, 3, 9)
GOAL = GoalTargets()


def _week() -> list[SessionPoint]:
    return [
        make_point(
            date(2026, 3, 2),
            "23:00",
            480,
            quality=4,
            score=80,
            latency=10,
            awakenings=1,
            mood_on_wake=4,
        ),
        make_point(
            date(2026, 3, 3),
            "23:30",
            420,
            quality=3,
            score=70,
            latency=20,
            awakenings=2,
            mood_on_wake=3,
        ),
        make_point(date(2026, 3, 4), "15:00", 32),
        make_point(
            date(2026, 3, 4), "23:00", 480, quality=5, score=90, awakenings=0, mood_on_wake=5
        ),
        make_point(date(2026, 3, 7), "23:30", 480, quality=4, score=85, latency=15, awakenings=1),
    ]


# =============================================================================== ranges
def test_date_range() -> None:
    result = builders.date_range(START, END)
    assert result.from_ == START
    assert result.to == END
    assert result.days == 7
    assert result.model_dump(by_alias=True) == {"from": START, "to": END, "days": 7}


def test_date_range_single_day() -> None:
    assert builders.date_range(START, START).days == 1


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (START, END, (date(2026, 2, 23), date(2026, 3, 1))),
        (START, START, (date(2026, 3, 1), date(2026, 3, 1))),
        (date(2026, 3, 1), date(2026, 3, 31), (date(2026, 1, 29), date(2026, 2, 28))),
    ],
)
def test_previous_range(start: date, end: date, expected: tuple[date, date]) -> None:
    previous = builders.previous_range(start, end)
    assert previous == expected
    assert (previous[1] - previous[0]) == (end - start)


# =============================================================================== overview
class TestBuildOverview:
    def test_full_period(self) -> None:
        overview = builders.build_overview(
            points=_week(),
            previous_points=[],
            start=START,
            end=END,
            goal=GOAL,
            tz="UTC",
            today=TODAY,
        )

        assert overview.range.days == 7
        assert overview.nights_tracked == 4
        assert overview.total_sleep_minutes == 480 + 420 + 512 + 480
        averages = overview.averages
        assert averages.duration_minutes == 473  # mean over nights, not sessions
        assert averages.quality == 4.0  # mean over rated sessions
        assert averages.score == 81
        assert averages.sleep_latency_minutes == 15
        assert averages.awakenings == 1.0
        assert averages.mood_on_wake == 4.0
        assert averages.bedtime == "23:15"
        assert averages.wake_time == "07:00"
        assert overview.consistency_score == 85
        assert overview.goal_attainment_rate == 0.75
        assert overview.sleep_debt_minutes == 28
        assert (overview.current_streak, overview.longest_streak) == (1, 1)
        assert overview.previous_period is None

    def test_previous_period_present(self) -> None:
        previous_points = [
            make_point(date(2026, 2, 24), "23:00", 420, quality=3, score=60),
            make_point(date(2026, 2, 25), "23:00", 440, quality=4, score=70),
        ]
        overview = builders.build_overview(
            points=_week(),
            previous_points=previous_points,
            start=START,
            end=END,
            goal=GOAL,
            tz="UTC",
            today=TODAY,
        )
        assert overview.previous_period == PreviousPeriod(
            duration_minutes=430, quality=3.5, score=65, consistency_score=96
        )

    def test_previous_period_with_single_night_has_no_consistency(self) -> None:
        overview = builders.build_overview(
            points=[],
            previous_points=[make_point(date(2026, 2, 24), "23:00", 420)],
            start=START,
            end=END,
            goal=GOAL,
            tz="UTC",
            today=TODAY,
        )
        assert overview.previous_period == PreviousPeriod(
            duration_minutes=420, quality=None, score=None, consistency_score=None
        )

    def test_empty_period(self) -> None:
        overview = builders.build_overview(
            points=[], previous_points=[], start=START, end=END, goal=GOAL, tz="UTC", today=TODAY
        )
        assert overview.nights_tracked == 0
        assert overview.total_sleep_minutes == 0
        assert all(value is None for value in overview.averages.model_dump().values())
        assert overview.consistency_score is None
        assert overview.goal_attainment_rate is None
        assert overview.sleep_debt_minutes == 0
        assert overview.current_streak == overview.longest_streak == 0
        assert overview.previous_period is None


# =============================================================================== daily
class TestBuildDaily:
    def test_one_entry_per_day(self) -> None:
        series = builders.build_daily(_week(), START, END, "UTC")
        assert [entry.date for entry in series] == daterange(START, END)

    def test_tracked_and_untracked_days(self) -> None:
        series = {entry.date: entry for entry in builders.build_daily(_week(), START, END, "UTC")}

        tracked = series[date(2026, 3, 4)]
        assert tracked.duration_minutes == 512
        assert tracked.sessions == 2
        assert tracked.quality == 5.0
        assert tracked.score == 90
        assert tracked.bedtime == "23:00"
        assert tracked.wake_time == "07:00"

        empty = series[date(2026, 3, 5)]
        assert empty.model_dump(exclude={"date"}) == {
            "duration_minutes": None,
            "quality": None,
            "score": None,
            "bedtime": None,
            "wake_time": None,
            "sessions": 0,
        }

    def test_night_score_is_rounded(self) -> None:
        points = [
            make_point(START, "14:00", 60, score=80),
            make_point(START, "23:00", 420, score=86),
        ]
        (entry,) = builders.build_daily(points, START, START, "UTC")
        assert entry.score == 83

    def test_points_outside_range_are_ignored(self) -> None:
        points = [make_point(START - timedelta(days=1), "23:00", 480)]
        series = builders.build_daily(points, START, END, "UTC")
        assert all(entry.sessions == 0 for entry in series)


# =============================================================================== weekday
def test_build_weekday() -> None:
    points = [
        make_point(date(2026, 3, 2), "23:00", 480, quality=4),
        make_point(date(2026, 3, 9), "23:30", 420, quality=3),
        make_point(date(2026, 3, 6), "00:30", 540, quality=5),
    ]
    stats = builders.build_weekday(points, "UTC")

    assert [stat.weekday for stat in stats] == list(range(7))
    monday, tuesday, *_ = stats
    assert monday.nights == 2
    assert monday.avg_duration_minutes == 450
    assert monday.avg_quality == 3.5
    assert monday.avg_bedtime == "23:15"
    assert monday.avg_wake_time == "06:45"
    assert tuesday.model_dump(exclude={"weekday"}) == {
        "nights": 0,
        "avg_duration_minutes": None,
        "avg_quality": None,
        "avg_bedtime": None,
        "avg_wake_time": None,
    }
    friday = stats[4]
    assert friday.nights == 1
    assert friday.avg_bedtime == "00:30"
    assert friday.avg_wake_time == "09:30"


# =============================================================================== distribution
def test_build_distribution() -> None:
    distribution = builders.build_distribution(_week(), "UTC")
    assert [bucket.bucket for bucket in distribution.duration] == [
        "<5h",
        "5-6h",
        "6-7h",
        "7-8h",
        "8-9h",
        "9h+",
    ]
    assert {b.bucket: b.count for b in distribution.duration} == {
        "<5h": 1,
        "5-6h": 0,
        "6-7h": 0,
        "7-8h": 1,
        "8-9h": 3,
        "9h+": 0,
    }
    assert {b.rating: b.count for b in distribution.quality} == {1: 0, 2: 0, 3: 1, 4: 2, 5: 1}
    assert [b.hour for b in distribution.bedtime] == list(range(24))
    assert {b.hour: b.count for b in distribution.bedtime if b.count} == {15: 1, 23: 4}


# =============================================================================== chronotype
def test_build_chronotype_unknown() -> None:
    chronotype = builders.build_chronotype(_week(), "UTC")
    assert chronotype.chronotype == "unknown"
    assert chronotype.nights_analyzed == 5
    assert chronotype.social_jetlag_minutes is None
    assert chronotype.mid_sleep is not None


def test_build_chronotype_empty() -> None:
    chronotype = builders.build_chronotype([], "UTC")
    assert chronotype.model_dump() == {
        "chronotype": "unknown",
        "mid_sleep": None,
        "social_jetlag_minutes": None,
        "nights_analyzed": 0,
    }


def test_build_chronotype_classified() -> None:
    points = [
        make_point(night, "01:00" if night.weekday() in FREE_NIGHT_WEEKDAYS else "23:00", 480)
        for night in daterange(START, START + timedelta(days=13))
    ]
    chronotype = builders.build_chronotype(points, "UTC")
    assert chronotype.model_dump() == {
        "chronotype": "intermediate",
        "mid_sleep": "05:00",
        "social_jetlag_minutes": 120,
        "nights_analyzed": 14,
    }
