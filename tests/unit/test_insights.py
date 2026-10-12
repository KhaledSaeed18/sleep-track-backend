from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime

import pytest

from app.modules.analytics.calculations import GoalTargets
from app.modules.analytics.schemas import (
    AnalyticsOverview,
    Chronotype,
    DateRange,
    OverviewAverages,
    PreviousPeriod,
    TagImpactRead,
    WeekdayStat,
)
from app.modules.insights.providers import (
    DEFAULT_RULES,
    InsightContext,
    RuleBasedInsightProvider,
    build_insight_provider,
    format_duration,
)
from app.modules.insights.schemas import Insight
from app.modules.tags.schemas import TagRead

GOAL = GoalTargets(duration_minutes=480, bedtime_minutes=23 * 60, window_minutes=30)
FALLBACK = "tip-consistent-wake-time"


def make_context(
    *,
    nights: int = 10,
    duration: int | None = 460,
    quality: float | None = 3.5,
    score: int | None = 70,
    latency: int | None = 15,
    bedtime: str | None = "23:00",
    consistency: int | None = 70,
    attainment: float | None = 0.6,
    debt: int = 100,
    streak: int = 2,
    previous: PreviousPeriod | None = None,
    jetlag: int | None = 30,
    weekday: list[WeekdayStat] | None = None,
    tags: list[TagImpactRead] | None = None,
    goal: GoalTargets = GOAL,
) -> InsightContext:
    """A context in which only the fallback tip fires unless a value is overridden."""
    overview = AnalyticsOverview(
        range=DateRange(from_=date(2026, 9, 13), to=date(2026, 10, 12), days=30),
        nights_tracked=nights,
        total_sleep_minutes=(duration or 0) * nights,
        averages=OverviewAverages(
            duration_minutes=duration,
            quality=quality,
            score=score,
            sleep_latency_minutes=latency,
            awakenings=1.0,
            mood_on_wake=3.5,
            bedtime=bedtime,
            wake_time="07:00",
        ),
        consistency_score=consistency,
        goal_attainment_rate=attainment,
        sleep_debt_minutes=debt,
        current_streak=streak,
        longest_streak=max(streak, 3),
        previous_period=previous,
    )
    return InsightContext(
        overview=overview,
        weekday=weekday or [],
        tags=tags or [],
        chronotype=Chronotype(
            chronotype="intermediate",
            mid_sleep="03:30",
            social_jetlag_minutes=jetlag,
            nights_analyzed=nights,
        ),
        goal=goal,
    )


def make_tag_impact(
    name: str,
    *,
    nights_with: int = 4,
    quality_impact: float | None = None,
    duration_impact: int | None = None,
) -> TagImpactRead:
    return TagImpactRead(
        tag=TagRead(
            id=uuid.uuid4(),
            name=name,
            color="#f59e0b",
            icon=None,
            is_system=False,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        nights_with=nights_with,
        avg_quality_with=3.5 + quality_impact if quality_impact is not None else None,
        avg_quality_without=3.5 if quality_impact is not None else None,
        avg_duration_with=450 + duration_impact if duration_impact is not None else None,
        avg_duration_without=450 if duration_impact is not None else None,
        quality_impact=quality_impact,
        duration_impact_minutes=duration_impact,
    )


def make_weekday(weekday_minutes: int | None, weekend_minutes: int | None) -> list[WeekdayStat]:
    return [
        WeekdayStat(
            weekday=day,
            nights=2,
            avg_duration_minutes=weekend_minutes if day in (4, 5) else weekday_minutes,
            avg_quality=None,
            avg_bedtime=None,
            avg_wake_time=None,
        )
        for day in range(7)
    ]


async def generate(context: InsightContext, limit: int = 12) -> list[Insight]:
    return await RuleBasedInsightProvider(limit=limit).generate(context)


async def insight_ids(context: InsightContext) -> list[str]:
    return [insight.id for insight in await generate(context)]


async def find(context: InsightContext, insight_id: str) -> Insight | None:
    return next((i for i in await generate(context) if i.id == insight_id), None)


# =============================================================================== format
@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (0, "0m"),
        (0.4, "0m"),
        (59, "59m"),
        (60, "1h"),
        (61, "1h 1m"),
        (89.6, "1h 30m"),
        (-90, "1h 30m"),
        (480, "8h"),
        (605, "10h 5m"),
    ],
)
def test_format_duration(minutes: float, expected: str) -> None:
    assert format_duration(minutes) == expected


# =============================================================================== baseline
async def test_neutral_context_only_yields_fallback_tip() -> None:
    insights = await generate(make_context())
    assert [i.id for i in insights] == [FALLBACK]
    assert insights[0].kind == "tip"
    assert insights[0].priority == 10


def test_build_insight_provider() -> None:
    provider = build_insight_provider()
    assert isinstance(provider, RuleBasedInsightProvider)
    assert provider.name == "rules"


# =============================================================================== rules
class TestNotEnoughData:
    @pytest.mark.parametrize(
        ("nights", "phrase"), [(0, "logged 0 nights"), (1, "logged 1 night "), (2, "2 nights")]
    )
    async def test_fires_below_three_nights(self, nights: int, phrase: str) -> None:
        insight = await find(make_context(nights=nights), "not-enough-data")
        assert insight is not None
        assert phrase in insight.message
        assert insight.priority == 90

    async def test_silent_at_three_nights(self) -> None:
        assert "not-enough-data" not in await insight_ids(make_context(nights=3))

    async def test_low_data_suppresses_gated_rules(self) -> None:
        context = make_context(nights=2, consistency=10, quality=1.5, bedtime="02:00")
        assert await insight_ids(context) == ["not-enough-data", FALLBACK]


class TestSleepDebt:
    async def test_fires_at_five_hours(self) -> None:
        insight = await find(make_context(debt=300), "sleep-debt-high")
        assert insight is not None
        assert insight.kind == "warning"
        assert insight.metric is not None
        assert insight.metric.value == "5h"
        assert "5h behind your goal" in insight.message

    async def test_silent_below_threshold(self) -> None:
        assert "sleep-debt-high" not in await insight_ids(make_context(debt=299))


class TestDurationVsGoal:
    async def test_below_goal(self) -> None:
        insight = await find(make_context(duration=450), "duration-below-goal")
        assert insight is not None
        assert insight.message == "You average 7h 30m a night, 30m short of your 8h goal."
        assert insight.metric is not None
        assert insight.metric.value == "-30m"

    @pytest.mark.parametrize(("duration", "metric"), [(465, "-15m"), (480, "+0m"), (500, "+20m")])
    async def test_meets_goal(self, duration: int, metric: str) -> None:
        insight = await find(make_context(duration=duration), "duration-meets-goal")
        assert insight is not None
        assert insight.kind == "positive"
        assert insight.metric is not None
        assert insight.metric.value == metric

    @pytest.mark.parametrize("duration", [451, 464, None])
    async def test_gap_between_thresholds_is_silent(self, duration: int | None) -> None:
        ids = await insight_ids(make_context(duration=duration))
        assert "duration-below-goal" not in ids
        assert "duration-meets-goal" not in ids


class TestConsistency:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (59, "consistency-low"),
            (60, None),
            (84, None),
            (85, "consistency-high"),
            (None, None),
        ],
    )
    async def test_thresholds(self, score: int | None, expected: str | None) -> None:
        ids = await insight_ids(make_context(consistency=score))
        fired = [i for i in ids if i.startswith("consistency-")]
        assert fired == ([expected] if expected else [])

    async def test_metric(self) -> None:
        insight = await find(make_context(consistency=40), "consistency-low")
        assert insight is not None
        assert insight.metric is not None
        assert insight.metric.value == "40/100"


class TestGoalAttainment:
    async def test_fires_with_enough_nights(self) -> None:
        insight = await find(make_context(nights=7, attainment=0.39), "goal-attainment-low")
        assert insight is not None
        assert insight.message.startswith("Only 39% of tracked nights")
        assert insight.metric is not None
        assert insight.metric.value == "39%"

    @pytest.mark.parametrize(("nights", "rate"), [(7, 0.4), (6, 0.1), (10, None)])
    async def test_silent(self, nights: int, rate: float | None) -> None:
        context = make_context(nights=nights, attainment=rate)
        assert "goal-attainment-low" not in await insight_ids(context)


class TestTrend:
    @staticmethod
    def _previous(score: int | None) -> PreviousPeriod:
        return PreviousPeriod(duration_minutes=450, quality=3.5, score=score, consistency_score=70)

    async def test_improving(self) -> None:
        insight = await find(make_context(score=75, previous=self._previous(70)), "score-improving")
        assert insight is not None
        assert insight.metric is not None
        assert insight.metric.value == "+5"
        assert "rose from 70 to 75" in insight.message

    async def test_declining(self) -> None:
        insight = await find(make_context(score=65, previous=self._previous(70)), "score-declining")
        assert insight is not None
        assert insight.kind == "warning"
        assert insight.metric is not None
        assert insight.metric.value == "-5"

    @pytest.mark.parametrize(("current", "before"), [(74, 70), (66, 70), (None, 70), (70, None)])
    async def test_silent(self, current: int | None, before: int | None) -> None:
        ids = await insight_ids(make_context(score=current, previous=self._previous(before)))
        assert not {"score-improving", "score-declining"} & set(ids)

    async def test_no_previous_period(self) -> None:
        ids = await insight_ids(make_context(score=95, previous=None))
        assert "score-improving" not in ids


class TestQuality:
    @pytest.mark.parametrize(
        ("quality", "expected"),
        [(4.0, "quality-high"), (3.99, None), (2.8, None), (2.79, "quality-low"), (None, None)],
    )
    async def test_thresholds(self, quality: float | None, expected: str | None) -> None:
        ids = await insight_ids(make_context(quality=quality))
        fired = [i for i in ids if i.startswith("quality-")]
        assert fired == ([expected] if expected else [])

    async def test_metric_formatting(self) -> None:
        insight = await find(make_context(quality=4.25), "quality-high")
        assert insight is not None
        assert insight.metric is not None
        assert insight.metric.value == "4.2"


class TestBedtime:
    async def test_later_than_target(self) -> None:
        insight = await find(make_context(bedtime="23:46"), "bedtime-later-than-target")
        assert insight is not None
        assert insight.kind == "warning"
        assert (
            insight.message == "Your average bedtime is 23:46, about 46m after your 23:00 target."
        )
        assert insight.metric is not None
        assert insight.metric.value == "23:46"

    async def test_earlier_than_target(self) -> None:
        insight = await find(make_context(bedtime="22:14"), "bedtime-earlier-than-target")
        assert insight is not None
        assert insight.kind == "info"
        assert "about 46m before your 23:00 target" in insight.message

    async def test_wraps_past_midnight(self) -> None:
        insight = await find(make_context(bedtime="00:30"), "bedtime-later-than-target")
        assert insight is not None
        assert "1h 30m after" in insight.message

    @pytest.mark.parametrize("bedtime", ["23:45", "22:15", "23:00", None])
    async def test_within_window_plus_margin(self, bedtime: str | None) -> None:
        ids = await insight_ids(make_context(bedtime=bedtime))
        assert not [i for i in ids if i.startswith("bedtime-")]

    async def test_respects_goal_window(self) -> None:
        wide = GoalTargets(bedtime_minutes=23 * 60, window_minutes=60)
        assert not [
            i
            for i in await insight_ids(make_context(bedtime="00:15", goal=wide))
            if i.startswith("bedtime-")
        ]
        assert "bedtime-later-than-target" in await insight_ids(
            make_context(bedtime="00:16", goal=wide)
        )


class TestSocialJetlag:
    async def test_fires_at_one_hour(self) -> None:
        insight = await find(make_context(jetlag=60), "social-jetlag")
        assert insight is not None
        assert insight.metric is not None
        assert insight.metric.value == "1h"

    @pytest.mark.parametrize("jetlag", [59, None])
    async def test_silent(self, jetlag: int | None) -> None:
        assert "social-jetlag" not in await insight_ids(make_context(jetlag=jetlag))


class TestLatency:
    async def test_fires_above_thirty_minutes(self) -> None:
        insight = await find(make_context(latency=31), "latency-high")
        assert insight is not None
        assert insight.kind == "tip"
        assert insight.metric is not None
        assert insight.metric.value == "31 min"

    @pytest.mark.parametrize("latency", [30, None])
    async def test_silent(self, latency: int | None) -> None:
        assert "latency-high" not in await insight_ids(make_context(latency=latency))


class TestStreak:
    async def test_fires_at_five_nights(self) -> None:
        insight = await find(make_context(streak=5), "goal-streak")
        assert insight is not None
        assert insight.title == "5-night streak!"
        assert insight.metric is not None
        assert insight.metric.value == "5 nights"

    async def test_silent_below_five(self) -> None:
        assert "goal-streak" not in await insight_ids(make_context(streak=4))


class TestWeekendCatchUp:
    async def test_fires_at_one_hour(self) -> None:
        insight = await find(make_context(weekday=make_weekday(420, 480)), "weekend-catch-up")
        assert insight is not None
        assert insight.metric is not None
        assert insight.metric.value == "+1h"
        assert "run 1h longer" in insight.message

    @pytest.mark.parametrize(("weekday", "weekend"), [(420, 479), (420, None), (None, 540)])
    async def test_silent(self, weekday: int | None, weekend: int | None) -> None:
        context = make_context(weekday=make_weekday(weekday, weekend))
        assert "weekend-catch-up" not in await insight_ids(context)

    async def test_untracked_weekdays_are_ignored(self) -> None:
        stats = make_weekday(420, 500)
        stats[0] = stats[0].model_copy(update={"avg_duration_minutes": None})
        stats[1] = stats[1].model_copy(update={"avg_duration_minutes": 0})
        stats[2] = stats[2].model_copy(update={"avg_duration_minutes": 360})
        # Remaining weekdays (Wed, Thu, Sun) average 400 minutes, so the gap is 100 minutes.
        insight = await find(make_context(weekday=stats), "weekend-catch-up")
        assert insight is not None
        assert insight.metric is not None
        assert insight.metric.value == "+1h 40m"


class TestTagImpacts:
    async def tag_insights(self, context: InsightContext) -> list[Insight]:
        return [i for i in await generate(context) if i.category == "tags"]

    async def test_negative_quality_impact(self) -> None:
        context = make_context(
            tags=[make_tag_impact("Caffeine", nights_with=3, quality_impact=-0.4)]
        )
        (insight,) = await self.tag_insights(context)
        assert insight.id == "tag-impact-caffeine"
        assert insight.kind == "warning"
        assert insight.title == "Caffeine lowers your sleep quality"
        assert (
            insight.message
            == "On nights tagged Caffeine your quality averages 3.1 vs 3.5 otherwise."
        )
        assert insight.metric is not None
        assert insight.metric.value == "-0.4"
        assert insight.priority == 68

    async def test_positive_quality_impact(self) -> None:
        context = make_context(tags=[make_tag_impact("Exercise", quality_impact=0.5)])
        (insight,) = await self.tag_insights(context)
        assert insight.kind == "positive"
        assert insight.title == "Exercise improves your sleep quality"
        assert insight.metric is not None
        assert insight.metric.value == "+0.5"
        assert insight.priority == 70

    async def test_small_quality_impact_is_ignored(self) -> None:
        context = make_context(tags=[make_tag_impact("Reading", quality_impact=0.39)])
        assert await self.tag_insights(context) == []

    async def test_duration_impact(self) -> None:
        context = make_context(tags=[make_tag_impact("Late meal", duration_impact=-30)])
        (insight,) = await self.tag_insights(context)
        assert insight.id == "tag-duration-late-meal"
        assert insight.title == "Late meal shortens your sleep"
        assert insight.message == "Nights tagged Late meal are 30m shorter on average."
        assert insight.metric is not None
        assert insight.metric.value == "-30m"
        assert insight.priority == 61

    @pytest.mark.parametrize("impact", [-29, 45, None])
    async def test_duration_impact_threshold(self, impact: int | None) -> None:
        context = make_context(tags=[make_tag_impact("Nap", duration_impact=impact)])
        assert await self.tag_insights(context) == []

    async def test_quality_takes_precedence_over_duration(self) -> None:
        impact = make_tag_impact("Alcohol", quality_impact=-0.5, duration_impact=-60)
        assert [i.id for i in await self.tag_insights(make_context(tags=[impact]))] == [
            "tag-impact-alcohol"
        ]

    @pytest.mark.parametrize(
        ("nights", "nights_with", "fires"),
        [(10, 2, False), (10, 3, True), (10, 7, True), (10, 8, False), (5, 3, False), (6, 3, True)],
    )
    async def test_minimum_sample_on_both_sides(
        self, nights: int, nights_with: int, fires: bool
    ) -> None:
        impact = make_tag_impact("Stress", nights_with=nights_with, quality_impact=-1.0)
        insights = await self.tag_insights(make_context(nights=nights, tags=[impact]))
        assert bool(insights) is fires

    async def test_priority_is_capped(self) -> None:
        context = make_context(
            tags=[
                make_tag_impact("Travel", quality_impact=-3.0),
                make_tag_impact("Jet lag", duration_impact=-600),
            ]
        )
        priorities = {i.id: i.priority for i in await self.tag_insights(context)}
        assert priorities == {"tag-impact-travel": 95, "tag-duration-jet-lag": 90}

    async def test_at_most_three_by_priority(self) -> None:
        context = make_context(
            tags=[
                make_tag_impact("Small", quality_impact=-0.4),
                make_tag_impact("Medium", quality_impact=-0.6),
                make_tag_impact("Large", quality_impact=-0.8),
                make_tag_impact("Huge", quality_impact=-1.0),
            ]
        )
        insights = await self.tag_insights(context)
        assert [(i.id, i.priority) for i in insights] == [
            ("tag-impact-huge", 77),
            ("tag-impact-large", 74),
            ("tag-impact-medium", 71),
        ]

    async def test_slug(self) -> None:
        context = make_context(tags=[make_tag_impact("  Screen time!! ", quality_impact=-1.0)])
        (insight,) = await self.tag_insights(context)
        assert insight.id == "tag-impact-screen-time"

    async def test_non_ascii_tag_names_keep_distinct_insights(self) -> None:
        context = make_context(
            tags=[
                make_tag_impact("قهوة", quality_impact=-1.0),
                make_tag_impact("رياضة", quality_impact=0.8),
            ]
        )
        insights = await self.tag_insights(context)
        assert len({i.id for i in insights}) == 2


# =============================================================================== provider
def _insight(insight_id: str, priority: int) -> Insight:
    return Insight(
        id=insight_id,
        kind="info",
        category="habit",
        title=insight_id,
        message="-",
        priority=priority,
    )


class TestProvider:
    async def test_duplicate_ids_keep_first(self) -> None:
        def first(_: InsightContext) -> Iterable[Insight]:
            yield _insight("shared", 50)

        def second(_: InsightContext) -> Iterable[Insight]:
            yield _insight("shared", 99)
            yield _insight("other", 20)

        insights = await RuleBasedInsightProvider(rules=[first, second]).generate(make_context())
        assert [(i.id, i.priority) for i in insights] == [("shared", 50), ("other", 20)]

    async def test_sorted_by_priority_then_id(self) -> None:
        def rule(_: InsightContext) -> Iterable[Insight]:
            yield _insight("b", 50)
            yield _insight("a", 50)
            yield _insight("c", 80)

        insights = await RuleBasedInsightProvider(rules=[rule]).generate(make_context())
        assert [i.id for i in insights] == ["c", "a", "b"]

    async def test_limit(self) -> None:
        context = make_context(
            nights=8,
            duration=400,
            debt=600,
            consistency=30,
            attainment=0.1,
            quality=2.0,
            latency=45,
            bedtime="01:00",
            jetlag=90,
        )
        everything = await generate(context, limit=100)
        limited = await generate(context, limit=3)
        assert len(everything) > 3
        assert limited == everything[:3]

    async def test_default_limit_is_twelve(self) -> None:
        def many(_: InsightContext) -> Iterable[Insight]:
            for index in range(20):
                yield _insight(f"rule-{index:02d}", 50)

        insights = await RuleBasedInsightProvider(rules=[many]).generate(make_context())
        assert len(insights) == 12

    async def test_poor_sleep_scenario_order(self) -> None:
        context = make_context(
            nights=8,
            duration=400,
            debt=600,
            consistency=30,
            attainment=0.1,
            quality=2.0,
            latency=45,
            bedtime="01:00",
            jetlag=90,
            score=55,
            previous=PreviousPeriod(
                duration_minutes=420, quality=3.0, score=65, consistency_score=50
            ),
            tags=[make_tag_impact("Caffeine", quality_impact=-1.2)],
            weekday=make_weekday(380, 470),
        )
        insights = await generate(context, limit=100)
        assert [(i.id, i.priority) for i in insights] == [
            ("sleep-debt-high", 85),
            ("duration-below-goal", 80),
            ("tag-impact-caffeine", 80),
            ("consistency-low", 75),
            ("goal-attainment-low", 72),
            ("score-declining", 70),
            ("quality-low", 68),
            ("bedtime-later-than-target", 60),
            ("social-jetlag", 58),
            ("latency-high", 55),
            ("weekend-catch-up", 35),
            (FALLBACK, 10),
        ]

    def test_every_default_rule_is_registered_once(self) -> None:
        assert len(set(DEFAULT_RULES)) == len(DEFAULT_RULES) == 14
