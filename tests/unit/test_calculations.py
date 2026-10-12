from __future__ import annotations

import math
import uuid
from datetime import date, timedelta

import pytest

from app.core.timeutils import daterange
from app.modules.analytics import calculations as calc
from app.modules.analytics.calculations import GoalTargets, SessionPoint
from tests.unit.helpers import make_night, make_point

MONDAY = date(2026, 3, 2)
GOAL = GoalTargets(duration_minutes=480, bedtime_minutes=23 * 60, wake_minutes=7 * 60)


def _std_for_spread(spread_minutes: float) -> float:
    """Circular std-dev in minutes of two clock times ``spread_minutes`` apart."""
    half_angle = spread_minutes / 2 / calc.MINUTES_PER_DAY * 2 * math.pi
    return math.sqrt(-2 * math.log(math.cos(half_angle))) / (2 * math.pi) * calc.MINUTES_PER_DAY


# =============================================================================== circular
class TestCircularMean:
    def test_empty_is_none(self) -> None:
        assert calc.circular_mean_minutes([]) is None

    def test_single_value(self) -> None:
        assert calc.circular_mean_minutes([1380]) == pytest.approx(1380)

    def test_simple_mean(self) -> None:
        assert calc.circular_mean_minutes([60, 120]) == pytest.approx(90)

    def test_wraps_across_midnight(self) -> None:
        result = calc.circular_mean_minutes([23 * 60 + 30, 30])
        assert result is not None
        assert calc.circular_diff_minutes(result, 0) == pytest.approx(0, abs=1e-6)

    def test_wrapped_mean_stays_in_documented_range(self) -> None:
        result = calc.circular_mean_minutes([23 * 60 + 30, 30])
        assert result is not None
        assert 0 <= result < calc.MINUTES_PER_DAY

    def test_late_evening_values(self) -> None:
        assert calc.circular_mean_minutes([22 * 60, 23 * 60, 0]) == pytest.approx(23 * 60, abs=1e-6)

    def test_values_beyond_one_day_are_normalised(self) -> None:
        assert calc.circular_mean_minutes([1440 + 90]) == pytest.approx(90)

    @pytest.mark.parametrize("values", [[0, 720], [360, 1080], [0, 480, 960]])
    def test_balanced_values_are_undefined(self, values: list[float]) -> None:
        assert calc.circular_mean_minutes(values) is None


class TestCircularStd:
    @pytest.mark.parametrize("values", [[], [600]])
    def test_needs_two_values(self, values: list[float]) -> None:
        assert calc.circular_std_minutes(values) is None

    def test_identical_values_have_zero_spread(self) -> None:
        assert calc.circular_std_minutes([1380, 1380, 1380]) == pytest.approx(0, abs=1e-6)

    def test_matches_formula(self) -> None:
        # Two values 60 minutes apart have mean resultant length cos(pi / 24).
        assert calc.circular_std_minutes([1350, 1410]) == pytest.approx(_std_for_spread(60))
        assert calc.circular_std_minutes([1410, 30]) == pytest.approx(_std_for_spread(60))

    def test_opposite_values_are_clamped(self) -> None:
        expected = math.sqrt(-2 * math.log(1e-12)) / (2 * math.pi) * calc.MINUTES_PER_DAY
        assert calc.circular_std_minutes([0, 720]) == pytest.approx(expected)


class TestCircularDiff:
    @pytest.mark.parametrize(
        ("a", "b", "expected"),
        [
            (100, 100, 0),
            (90, 60, 30),
            (60, 90, -30),
            (30, 1410, 60),
            (1410, 30, -60),
            (1439, 0, -1),
            (0, 1439, 1),
            (720, 0, 720),
            (0, 720, 720),
            (721, 0, -719),
            (1440 + 30, 0, 30),
        ],
    )
    def test_signed_shortest_distance(self, a: float, b: float, expected: float) -> None:
        assert calc.circular_diff_minutes(a, b) == pytest.approx(expected)


# =============================================================================== components
@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (480, 1.0),
        (540, 1.0),
        (600, 1 - 60 / 180),
        (660, 1 - 120 / 180),
        (720, 0.0),
        (900, 0.0),
        (470, 1 - 10 / 240),
        (360, 0.5),
        (240, 0.0),
        (0, 0.0),
    ],
)
def test_duration_component(duration: int, expected: float) -> None:
    assert calc.duration_component(duration, 480) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("quality", "expected"), [(1, 0.0), (2, 0.25), (3, 0.5), (4, 0.75), (5, 1.0), (0, 0.0)]
)
def test_quality_component(quality: int, expected: float) -> None:
    assert calc.quality_component(quality) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("latency", "expected"),
    [(0, 1.0), (15, 1.0), (16, 1 - 1 / 45), (30, 1 - 15 / 45), (60, 0.0), (120, 0.0)],
)
def test_latency_component(latency: int, expected: float) -> None:
    assert calc.latency_component(latency) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("awakenings", "expected"),
    [(0, 1.0), (1, 1.0), (2, 0.8), (3, 0.6), (5, 0.2), (6, 0.0), (12, 0.0)],
)
def test_awakenings_component(awakenings: int, expected: float) -> None:
    assert calc.awakenings_component(awakenings) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("bedtime", "expected"),
    [
        (1380, 1.0),
        (1410, 1.0),
        (1350, 1.0),
        (1411, 1 - 1 / 90),
        (1455, 0.5),
        (1305, 0.5),
        (0, 1 - 30 / 90),
        (1500, 0.0),
        (1260, 0.0),
        (720, 0.0),
    ],
)
def test_bedtime_component(bedtime: float, expected: float) -> None:
    assert calc.bedtime_component(bedtime, 1380, 30) == pytest.approx(expected)


def test_bedtime_component_window_wraps_midnight() -> None:
    assert calc.bedtime_component(15, 23 * 60 + 45, 30) == 1.0
    assert calc.bedtime_component(23 * 60 + 50, 10, 30) == 1.0


# =============================================================================== score
class TestSleepScore:
    def test_perfect_night(self) -> None:
        score = calc.sleep_score(
            duration_minutes=480,
            bedtime_minutes=1380,
            goal=GOAL,
            quality=5,
            sleep_latency_minutes=10,
            awakenings=0,
        )
        assert score == 100

    def test_worst_night(self) -> None:
        score = calc.sleep_score(
            duration_minutes=60,
            bedtime_minutes=12 * 60,
            goal=GOAL,
            quality=1,
            sleep_latency_minutes=90,
            awakenings=10,
        )
        assert score == 0

    def test_weighted_sum_with_every_input(self) -> None:
        # duration 0.5 * 0.40 + bedtime 0.5 * 0.10 + quality 0.75 * 0.25
        # + latency (2/3) * 0.15 + awakenings 0.6 * 0.10 = 0.5975
        score = calc.sleep_score(
            duration_minutes=360,
            bedtime_minutes=1455,
            goal=GOAL,
            quality=4,
            sleep_latency_minutes=30,
            awakenings=3,
        )
        assert score == 60

    def test_missing_inputs_are_reweighted(self) -> None:
        # (0.5 * 0.40 + 1.0 * 0.10) / 0.50 = 0.6, not 0.3
        assert calc.sleep_score(duration_minutes=360, bedtime_minutes=1380, goal=GOAL) == 60

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            # duration 1.0, bedtime 0 and quality 0 over a total weight of 0.75
            ({"duration_minutes": 480, "bedtime_minutes": 1500, "quality": 1}, 53),
            # duration 0, bedtime 1.0 and latency 0 over a total weight of 0.65
            ({"duration_minutes": 240, "bedtime_minutes": 1380, "sleep_latency_minutes": 60}, 15),
            # duration 1.0, bedtime 1.0 and awakenings 0 over a total weight of 0.60
            ({"duration_minutes": 480, "bedtime_minutes": 1380, "awakenings": 6}, 83),
        ],
    )
    def test_single_optional_input(self, kwargs: dict[str, int], expected: int) -> None:
        assert calc.sleep_score(goal=GOAL, **kwargs) == expected

    def test_uses_goal_targets(self) -> None:
        goal = GoalTargets(duration_minutes=420, bedtime_minutes=22 * 60, window_minutes=0)
        assert calc.sleep_score(duration_minutes=420, bedtime_minutes=22 * 60, goal=goal) == 100
        assert calc.sleep_score(duration_minutes=420, bedtime_minutes=22 * 60 + 45, goal=goal) == 90

    def test_weights_sum_to_one(self) -> None:
        assert sum(calc.SCORE_WEIGHTS.values()) == pytest.approx(1.0)


# =============================================================================== helpers
def test_mean_or_none() -> None:
    assert calc.mean_or_none([]) is None
    assert calc.mean_or_none([None, None]) is None
    assert calc.mean_or_none([1, None, 2]) == 1.5
    assert calc.mean_or_none([1, 2, 2]) == 1.7
    assert calc.mean_or_none([1, 2, 2], digits=None) == pytest.approx(5 / 3)


def test_mean_int() -> None:
    assert calc.mean_int([]) is None
    assert calc.mean_int([400, 401]) == 400
    assert calc.mean_int([400, None, 403]) == 402


@pytest.mark.parametrize(
    ("duration", "expected"), [(480, True), (466, True), (465, True), (464, False), (600, True)]
)
def test_meets_goal_tolerance(duration: int, expected: bool) -> None:
    assert calc.meets_goal(duration, 480) is expected


def test_local_minutes_uses_timezone() -> None:
    point = make_point(MONDAY, "23:15", 480, tz="America/New_York")
    assert calc.local_minutes(point.started_at, "America/New_York") == 23 * 60 + 15
    assert calc.local_minutes(point.started_at, "UTC") == 4 * 60 + 15


# =============================================================================== nights
class TestSummarizeNights:
    def test_empty(self) -> None:
        assert calc.summarize_nights([], "UTC") == {}

    def test_groups_sessions_and_uses_longest_as_main(self) -> None:
        nap = make_point(MONDAY, "15:00", 30, score=60)
        main = make_point(MONDAY, "23:00", 450, quality=4, score=80)
        other = make_point(MONDAY + timedelta(days=1), "22:30", 420, quality=2)

        nights = calc.summarize_nights([nap, main, other], "UTC")

        assert set(nights) == {MONDAY, MONDAY + timedelta(days=1)}
        night = nights[MONDAY]
        assert night.duration_minutes == 480
        assert night.sessions == 2
        assert night.quality == 4.0
        assert night.score == 70.0
        assert night.bedtime_minutes == 23 * 60
        assert night.wake_minutes == 6 * 60 + 30
        assert nights[MONDAY + timedelta(days=1)].score is None

    def test_quality_mean_is_rounded(self) -> None:
        points = [
            make_point(MONDAY, "13:00", 20, quality=5),
            make_point(MONDAY, "16:00", 30, quality=4),
            make_point(MONDAY, "23:00", 400, quality=4),
        ]
        assert calc.summarize_nights(points, "UTC")[MONDAY].quality == 4.3

    def test_clock_times_are_local(self) -> None:
        point = make_point(MONDAY, "23:30", 465, tz="Asia/Tokyo")
        night = calc.summarize_nights([point], "Asia/Tokyo")[MONDAY]
        assert night.bedtime_minutes == 23 * 60 + 30
        assert night.wake_minutes == 7 * 60 + 15


# =============================================================================== consistency
class TestConsistencyScore:
    def test_needs_two_nights(self) -> None:
        assert calc.consistency_score([1380], [420]) is None
        assert calc.consistency_score([1380, 1380], [420]) is None

    def test_identical_schedule_is_perfect(self) -> None:
        assert calc.consistency_score([1380] * 4, [420] * 4) == 100

    def test_hand_computed_spread(self) -> None:
        # Both series are 60 minutes apart: std ~30.04 min, 1 - 30.04 / 120 = 0.7496.
        assert _std_for_spread(60) == pytest.approx(30.04, abs=0.01)
        assert calc.consistency_score([1350, 1410], [390, 450]) == 75

    def test_wrapping_bedtimes_do_not_look_irregular(self) -> None:
        assert calc.consistency_score([1410, 30], [390, 450]) == 75

    def test_large_spread_clamps_to_zero(self) -> None:
        assert calc.consistency_score([0, 720], [300, 1020]) == 0


# =============================================================================== goal attainment
class TestGoalAttainmentRate:
    def test_no_nights(self) -> None:
        assert calc.goal_attainment_rate([], 480) is None

    def test_fifteen_minute_tolerance(self) -> None:
        nights = [make_night(MONDAY + timedelta(days=i), d) for i, d in enumerate([465, 464, 480])]
        nights.append(make_night(MONDAY + timedelta(days=3), 300))
        assert calc.goal_attainment_rate(nights, 480) == 0.5

    def test_rounded_to_two_decimals(self) -> None:
        nights = [make_night(MONDAY + timedelta(days=i), d) for i, d in enumerate([480, 300, 300])]
        assert calc.goal_attainment_rate(nights, 480) == 0.33


# =============================================================================== sleep debt
class TestSleepDebt:
    END = date(2026, 3, 14)

    def _nights(self, durations: dict[date, int]) -> dict[date, calc.NightSummary]:
        return {day: make_night(day, minutes) for day, minutes in durations.items()}

    def test_no_nights(self) -> None:
        assert calc.sleep_debt_minutes({}, self.END, 480) == 0

    def test_only_fourteen_day_window_counts(self) -> None:
        nights = self._nights(
            {
                date(2026, 2, 28): 0,  # one day before the window
                date(2026, 3, 1): 420,  # first day of the window
                date(2026, 3, 14): 360,  # end of the window
                date(2026, 3, 15): 0,  # after the end
            }
        )
        assert calc.sleep_debt_minutes(nights, self.END, 480) == 180

    def test_surplus_offsets_deficit(self) -> None:
        nights = self._nights(
            {date(2026, 3, 1): 420, date(2026, 3, 5): 600, date(2026, 3, 14): 360}
        )
        assert calc.sleep_debt_minutes(nights, self.END, 480) == 60

    def test_never_negative(self) -> None:
        nights = self._nights({date(2026, 3, 10): 600, date(2026, 3, 11): 540})
        assert calc.sleep_debt_minutes(nights, self.END, 480) == 0

    def test_untracked_nights_are_ignored(self) -> None:
        nights = self._nights({date(2026, 3, 13): 450})
        assert calc.sleep_debt_minutes(nights, self.END, 480) == 30


# =============================================================================== streaks
class TestStreaks:
    START = date(2026, 3, 1)
    END = date(2026, 3, 7)

    def _nights(self, durations: dict[int, int]) -> dict[date, calc.NightSummary]:
        """Durations keyed by day-of-month in March 2026."""
        return {
            date(2026, 3, day): make_night(date(2026, 3, day), minutes)
            for day, minutes in durations.items()
        }

    def test_empty(self) -> None:
        assert calc.streaks({}, self.START, self.END, 480, today=date(2026, 3, 8)) == (0, 0)

    def test_current_and_longest(self) -> None:
        nights = self._nights({1: 480, 2: 470, 3: 500, 4: 300, 5: 480, 6: 480, 7: 480})
        assert calc.streaks(nights, self.START, self.END, 480, today=date(2026, 3, 20)) == (3, 3)

    def test_unlogged_last_night_is_skipped_when_recent(self) -> None:
        nights = self._nights({1: 480, 2: 480, 3: 480, 4: 300, 5: 480, 6: 480})
        for today in (date(2026, 3, 7), date(2026, 3, 8)):
            assert calc.streaks(nights, self.START, self.END, 480, today=today) == (2, 3)

    def test_unlogged_end_breaks_streak_when_in_the_past(self) -> None:
        nights = self._nights({5: 480, 6: 480})
        assert calc.streaks(nights, self.START, self.END, 480, today=date(2026, 3, 9)) == (0, 2)

    def test_only_one_night_is_skipped(self) -> None:
        nights = self._nights({4: 480, 5: 480})
        assert calc.streaks(nights, self.START, self.END, 480, today=date(2026, 3, 8)) == (0, 2)

    def test_logged_end_that_misses_goal_resets_current(self) -> None:
        nights = self._nights({5: 480, 6: 480, 7: 400})
        assert calc.streaks(nights, self.START, self.END, 480, today=date(2026, 3, 8)) == (0, 2)

    def test_current_stops_at_range_start(self) -> None:
        nights = {
            day: make_night(day, 480) for day in daterange(date(2026, 2, 20), date(2026, 3, 7))
        }
        assert calc.streaks(nights, self.START, self.END, 480, today=date(2026, 3, 8)) == (7, 7)

    def test_tolerance_applies(self) -> None:
        nights = self._nights({6: 465, 7: 466})
        assert calc.streaks(nights, self.START, self.END, 480, today=date(2026, 3, 8)) == (2, 2)


# =============================================================================== chronotype
def _two_weeks(
    work: tuple[str, int], free: tuple[str, int], start: date = MONDAY
) -> list[SessionPoint]:
    """Fourteen nights; ``work``/``free`` are (bedtime, duration) for each kind of night."""
    points = []
    for night in daterange(start, start + timedelta(days=13)):
        bedtime, duration = free if night.weekday() in calc.FREE_NIGHT_WEEKDAYS else work
        points.append(make_point(night, bedtime, duration))
    return points


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (0, "early_bird"),
        (23 * 60, "early_bird"),
        (1081, "early_bird"),
        (179, "early_bird"),
        (180, "intermediate"),
        (240, "intermediate"),
        (300, "intermediate"),
        (301, "night_owl"),
        (600, "night_owl"),
        (1080, "night_owl"),
    ],
)
def test_classify_chronotype(minutes: float, expected: str) -> None:
    assert calc.classify_chronotype(minutes) == expected


class TestChronotype:
    def test_no_points(self) -> None:
        result = calc.chronotype([], "UTC")
        assert result == calc.ChronotypeResult("unknown", None, None, 0)

    def test_unknown_below_minimum_nights(self) -> None:
        points = [make_point(MONDAY + timedelta(days=i), "23:00", 480) for i in range(6)]
        result = calc.chronotype(points, "UTC")
        assert result.chronotype == "unknown"
        assert result.mid_sleep_minutes == pytest.approx(180)
        assert result.social_jetlag_minutes is None
        assert result.nights_analyzed == 6

    def test_seven_nights_is_enough(self) -> None:
        points = [make_point(MONDAY + timedelta(days=i), "23:00", 480) for i in range(7)]
        assert calc.chronotype(points, "UTC").chronotype != "unknown"

    def test_intermediate_with_social_jetlag(self) -> None:
        result = calc.chronotype(_two_weeks(work=("23:00", 480), free=("01:00", 480)), "UTC")
        assert result.chronotype == "intermediate"
        assert result.mid_sleep_minutes == pytest.approx(300)
        assert result.social_jetlag_minutes == 120
        assert result.nights_analyzed == 14

    def test_early_bird(self) -> None:
        result = calc.chronotype(_two_weeks(work=("22:00", 480), free=("22:00", 480)), "UTC")
        assert result.chronotype == "early_bird"
        assert result.mid_sleep_minutes == pytest.approx(120)
        assert result.social_jetlag_minutes == 0

    def test_night_owl(self) -> None:
        result = calc.chronotype(_two_weeks(work=("01:00", 480), free=("02:00", 480)), "UTC")
        assert result.chronotype == "night_owl"
        assert result.mid_sleep_minutes == pytest.approx(360)
        assert result.social_jetlag_minutes == 60

    def test_oversleep_correction_moves_classification(self) -> None:
        # MSF = 00:40 + 5h = 05:40 (night owl); SDweek = (5*480 + 2*600) / 7 so
        # MSFsc = 340 - (600 - 514.29) / 2 = 297.1 minutes, which is intermediate.
        result = calc.chronotype(_two_weeks(work=("23:00", 480), free=("00:40", 600)), "UTC")
        assert result.chronotype == "intermediate"
        assert result.mid_sleep_minutes == pytest.approx(340)
        assert result.social_jetlag_minutes == 160

    def test_no_correction_when_free_nights_are_shorter(self) -> None:
        result = calc.chronotype(_two_weeks(work=("23:00", 480), free=("01:20", 420)), "UTC")
        assert result.mid_sleep_minutes == pytest.approx(290)
        assert result.chronotype == "intermediate"

    def test_too_few_free_nights_uses_overall_mean(self) -> None:
        work_nights = [
            *daterange(MONDAY, MONDAY + timedelta(days=3)),
            *daterange(MONDAY + timedelta(days=6), MONDAY + timedelta(days=8)),
        ]
        points = [make_point(night, "23:00", 480) for night in work_nights]
        points.append(make_point(MONDAY + timedelta(days=4), "03:00", 480))
        result = calc.chronotype(points, "UTC")
        assert result.social_jetlag_minutes is None
        assert result.mid_sleep_minutes is not None
        assert 180 < result.mid_sleep_minutes < 420
        assert result.chronotype == "intermediate"

    def test_too_few_work_nights_has_no_jetlag(self) -> None:
        free_nights = [
            night
            for night in daterange(MONDAY, MONDAY + timedelta(days=27))
            if night.weekday() in calc.FREE_NIGHT_WEEKDAYS
        ][:7]
        points = [make_point(night, "01:00", 480) for night in free_nights]
        points.append(make_point(MONDAY, "23:00", 480))
        result = calc.chronotype(points, "UTC")
        assert result.social_jetlag_minutes is None
        assert result.mid_sleep_minutes == pytest.approx(300)

    def test_undefined_mean_is_unknown(self) -> None:
        work_nights = [
            night
            for night in daterange(MONDAY, MONDAY + timedelta(days=10))
            if night.weekday() not in calc.FREE_NIGHT_WEEKDAYS
        ][:8]
        points = [
            make_point(night, "20:00" if i % 2 == 0 else "08:00", 480)
            for i, night in enumerate(work_nights)
        ]
        assert calc.chronotype(points, "UTC") == calc.ChronotypeResult("unknown", None, None, 8)

    def test_uses_local_clock_times(self) -> None:
        points = [
            make_point(night, "23:00", 480, tz="Asia/Kolkata")
            for night in daterange(MONDAY, MONDAY + timedelta(days=6))
        ]
        local = calc.chronotype(points, "Asia/Kolkata")
        assert local.mid_sleep_minutes == pytest.approx(180)
        # 23:00 IST is 17:30 UTC, so mid-sleep is 21:30 UTC.
        assert calc.chronotype(points, "UTC").mid_sleep_minutes == pytest.approx(21 * 60 + 30)


# =============================================================================== tags
class TestTagImpacts:
    A = uuid.uuid4()
    B = uuid.uuid4()
    UNUSED = uuid.uuid4()

    def _points(self) -> list[SessionPoint]:
        day = MONDAY
        return [
            make_point(day, "23:00", 400, quality=2, tags=[self.A]),
            make_point(day + timedelta(days=1), "23:00", 420, quality=3, tags=[self.A]),
            make_point(day + timedelta(days=2), "23:00", 480, quality=5),
            make_point(day + timedelta(days=3), "23:00", 500, quality=4, tags=[self.B]),
            make_point(day + timedelta(days=4), "23:00", 460, tags=[self.B]),
        ]

    def test_compares_with_and_without(self) -> None:
        impacts = calc.tag_impacts(self._points(), [self.B, self.A, self.UNUSED])
        assert [impact.tag_id for impact in impacts] == [self.A, self.B]

        a, b = impacts
        assert a == calc.TagImpact(
            tag_id=self.A,
            nights_with=2,
            avg_quality_with=2.5,
            avg_quality_without=4.5,
            avg_duration_with=410,
            avg_duration_without=480,
            quality_impact=-2.0,
            duration_impact_minutes=-70,
        )
        # Quality is averaged over rated sessions only; duration over all of them.
        assert b.nights_with == 2
        assert b.avg_quality_with == 4.0
        assert b.avg_quality_without == 3.3
        assert b.quality_impact == 0.67
        assert b.avg_duration_with == 480
        assert b.avg_duration_without == 433
        assert b.duration_impact_minutes == 47

    def test_tag_on_every_session_has_no_comparison(self) -> None:
        points = [make_point(MONDAY, "23:00", 480, quality=3, tags=[self.A])]
        (impact,) = calc.tag_impacts(points, [self.A])
        assert impact.avg_quality_without is None
        assert impact.avg_duration_without is None
        assert impact.quality_impact is None
        assert impact.duration_impact_minutes is None

    def test_unrated_tagged_sessions(self) -> None:
        points = [
            make_point(MONDAY, "23:00", 480, tags=[self.A]),
            make_point(MONDAY + timedelta(days=1), "23:00", 420, quality=4),
        ]
        (impact,) = calc.tag_impacts(points, [self.A])
        assert impact.avg_quality_with is None
        assert impact.quality_impact is None
        assert impact.duration_impact_minutes == 60

    def test_ties_sorted_by_sample_size(self) -> None:
        points = [
            make_point(MONDAY, "23:00", 480, tags=[self.A]),
            make_point(MONDAY + timedelta(days=1), "23:00", 480, tags=[self.B]),
            make_point(MONDAY + timedelta(days=2), "23:00", 480, tags=[self.B]),
        ]
        impacts = calc.tag_impacts(points, [self.A, self.B])
        assert [impact.tag_id for impact in impacts] == [self.B, self.A]

    def test_no_tags(self) -> None:
        assert calc.tag_impacts(self._points(), []) == []


# =============================================================================== distribution
@pytest.mark.parametrize(
    ("minutes", "label"),
    [
        (-5, "<5h"),
        (0, "<5h"),
        (299, "<5h"),
        (300, "5-6h"),
        (359, "5-6h"),
        (360, "6-7h"),
        (419, "6-7h"),
        (420, "7-8h"),
        (479, "7-8h"),
        (480, "8-9h"),
        (539, "8-9h"),
        (540, "9h+"),
        (1200, "9h+"),
    ],
)
def test_duration_bucket(minutes: int, label: str) -> None:
    assert calc.duration_bucket(minutes) == label


def test_distribution_counts() -> None:
    points = [
        make_point(MONDAY, "23:30", 480, quality=4, tz="Asia/Tokyo"),
        make_point(MONDAY + timedelta(days=1), "23:10", 290, quality=4, tz="Asia/Tokyo"),
        make_point(MONDAY + timedelta(days=2), "01:00", 600, tz="Asia/Tokyo"),
    ]
    durations, qualities, hours = calc.distribution(points, "Asia/Tokyo")

    assert list(durations) == ["<5h", "5-6h", "6-7h", "7-8h", "8-9h", "9h+"]
    assert durations == {"<5h": 1, "5-6h": 0, "6-7h": 0, "7-8h": 0, "8-9h": 1, "9h+": 1}
    assert qualities == {1: 0, 2: 0, 3: 0, 4: 2, 5: 0}
    assert set(hours) == set(range(24))
    assert hours[23] == 2
    assert hours[1] == 1
    assert sum(hours.values()) == 3

    _, _, utc_hours = calc.distribution(points, "UTC")
    assert utc_hours[14] == 2
    assert utc_hours[16] == 1


def test_distribution_empty() -> None:
    durations, qualities, hours = calc.distribution([], "UTC")
    assert sum(durations.values()) == sum(qualities.values()) == sum(hours.values()) == 0
    assert len(durations) == 6
    assert len(qualities) == 5
    assert len(hours) == 24
