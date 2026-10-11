"""Insight generation.

``InsightProvider`` is the extension point (a statistical or remote model
could implement it); the default ``RuleBasedInsightProvider`` is a small,
deterministic rule engine whose every output can be traced back to a number
the user can see elsewhere in the app.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from app.core.timeutils import format_hhmm
from app.modules.analytics.calculations import GoalTargets, circular_diff_minutes
from app.modules.analytics.schemas import (
    AnalyticsOverview,
    Chronotype,
    TagImpactRead,
    WeekdayStat,
)
from app.modules.insights.schemas import Insight, InsightMetric

MIN_NIGHTS = 3
MIN_TAG_SAMPLE = 3
MAX_TAG_INSIGHTS = 3


@dataclass(frozen=True, slots=True)
class InsightContext:
    overview: AnalyticsOverview
    weekday: list[WeekdayStat]
    tags: list[TagImpactRead]
    chronotype: Chronotype
    goal: GoalTargets


class InsightProvider(Protocol):
    name: str

    async def generate(self, context: InsightContext) -> list[Insight]: ...


def format_duration(minutes: float) -> str:
    total = round(abs(minutes))
    hours, mins = divmod(total, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    return f"{hours}h" if hours else f"{mins}m"


def _signed_duration(minutes: float) -> str:
    return f"{'+' if minutes >= 0 else '-'}{format_duration(minutes)}"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


Rule = Callable[[InsightContext], Iterable[Insight]]


def _not_enough_data(ctx: InsightContext) -> Iterable[Insight]:
    nights = ctx.overview.nights_tracked
    if nights < MIN_NIGHTS:
        yield Insight(
            id="not-enough-data",
            kind="info",
            category="habit",
            title="Keep logging to unlock insights",
            message=(
                f"You've logged {nights} night{'s' if nights != 1 else ''} in this period. "
                f"After {MIN_NIGHTS} nights we can start spotting patterns."
            ),
            priority=90,
        )


def _duration_vs_goal(ctx: InsightContext) -> Iterable[Insight]:
    average = ctx.overview.averages.duration_minutes
    if average is None:
        return
    gap = average - ctx.goal.duration_minutes
    metric = InsightMetric(label="Average vs goal", value=_signed_duration(gap))
    if gap <= -30:
        yield Insight(
            id="duration-below-goal",
            kind="warning",
            category="duration",
            title="You're sleeping less than your goal",
            message=(
                f"You average {format_duration(average)} a night, {format_duration(gap)} "
                f"short of your {format_duration(ctx.goal.duration_minutes)} goal."
            ),
            metric=metric,
            priority=80,
        )
    elif gap >= -15:
        yield Insight(
            id="duration-meets-goal",
            kind="positive",
            category="duration",
            title="You're meeting your sleep goal",
            message=f"You average {format_duration(average)} of sleep a night. Keep it up!",
            metric=metric,
            priority=50,
        )


def _goal_attainment(ctx: InsightContext) -> Iterable[Insight]:
    rate = ctx.overview.goal_attainment_rate
    if rate is None or ctx.overview.nights_tracked < 7:
        return
    if rate < 0.4:
        yield Insight(
            id="goal-attainment-low",
            kind="warning",
            category="goal",
            title="Most nights fall short of your goal",
            message=(
                f"Only {round(rate * 100)}% of tracked nights reached your duration goal. "
                "Try moving bedtime 15 minutes earlier for a week."
            ),
            metric=InsightMetric(label="Goal attainment", value=f"{round(rate * 100)}%"),
            priority=72,
        )


def _sleep_debt(ctx: InsightContext) -> Iterable[Insight]:
    debt = ctx.overview.sleep_debt_minutes
    if debt >= 300:
        yield Insight(
            id="sleep-debt-high",
            kind="warning",
            category="duration",
            title="Sleep debt is building up",
            message=(
                f"Over the last two weeks you're {format_duration(debt)} behind your goal. "
                "A couple of earlier nights will help you catch up."
            ),
            metric=InsightMetric(label="Sleep debt", value=format_duration(debt)),
            priority=85,
        )


def _consistency(ctx: InsightContext) -> Iterable[Insight]:
    score = ctx.overview.consistency_score
    if score is None or ctx.overview.nights_tracked < MIN_NIGHTS:
        return
    metric = InsightMetric(label="Consistency", value=f"{score}/100")
    if score < 60:
        yield Insight(
            id="consistency-low",
            kind="warning",
            category="consistency",
            title="Your sleep schedule is irregular",
            message=(
                "Your bedtime and wake time vary a lot from night to night. A steady wake time, "
                "even at weekends, is the fastest way to stabilise your rhythm."
            ),
            metric=metric,
            priority=75,
        )
    elif score >= 85:
        yield Insight(
            id="consistency-high",
            kind="positive",
            category="consistency",
            title="Your schedule is very consistent",
            message="You go to bed and wake up at similar times. Your body clock thanks you.",
            metric=metric,
            priority=45,
        )


def _bedtime_vs_target(ctx: InsightContext) -> Iterable[Insight]:
    bedtime = ctx.overview.averages.bedtime
    if bedtime is None or ctx.overview.nights_tracked < MIN_NIGHTS:
        return
    hours, minutes = (int(part) for part in bedtime.split(":"))
    deviation = circular_diff_minutes(hours * 60 + minutes, ctx.goal.bedtime_minutes)
    if abs(deviation) <= ctx.goal.window_minutes + 15:
        return
    later = deviation > 0
    target = format_hhmm(ctx.goal.bedtime_minutes)
    yield Insight(
        id="bedtime-later-than-target" if later else "bedtime-earlier-than-target",
        kind="warning" if later else "info",
        category="timing",
        title="You go to bed later than planned" if later else "You go to bed earlier than planned",
        message=(
            f"Your average bedtime is {bedtime}, about {format_duration(deviation)} "
            f"{'after' if later else 'before'} your {target} target."
        ),
        metric=InsightMetric(label="Average bedtime", value=bedtime),
        priority=60,
    )


def _latency(ctx: InsightContext) -> Iterable[Insight]:
    latency = ctx.overview.averages.sleep_latency_minutes
    if latency is not None and latency > 30:
        yield Insight(
            id="latency-high",
            kind="tip",
            category="quality",
            title="It takes you a while to fall asleep",
            message=(
                f"On average you need {latency} minutes to drift off. Dimming screens an hour "
                "before bed and keeping the bedroom cool can shorten that."
            ),
            metric=InsightMetric(label="Avg. time to fall asleep", value=f"{latency} min"),
            priority=55,
        )


def _quality(ctx: InsightContext) -> Iterable[Insight]:
    quality = ctx.overview.averages.quality
    if quality is None or ctx.overview.nights_tracked < MIN_NIGHTS:
        return
    if quality >= 4.0:
        yield Insight(
            id="quality-high",
            kind="positive",
            category="quality",
            title="You rate your sleep highly",
            message=f"Your average sleep quality is {quality:.1f} out of 5.",
            metric=InsightMetric(label="Average quality", value=f"{quality:.1f}"),
            priority=42,
        )
    elif quality < 2.8:
        yield Insight(
            id="quality-low",
            kind="warning",
            category="quality",
            title="Your sleep quality is low",
            message=(
                f"Your average sleep quality is {quality:.1f} out of 5. "
                "Check the tag insights to see what might be getting in the way."
            ),
            metric=InsightMetric(label="Average quality", value=f"{quality:.1f}"),
            priority=68,
        )


def _streak(ctx: InsightContext) -> Iterable[Insight]:
    streak = ctx.overview.current_streak
    if streak >= 5:
        yield Insight(
            id="goal-streak",
            kind="positive",
            category="habit",
            title=f"{streak}-night streak!",
            message=f"You've met your sleep goal {streak} nights in a row.",
            metric=InsightMetric(label="Current streak", value=f"{streak} nights"),
            priority=40,
        )


def _trend(ctx: InsightContext) -> Iterable[Insight]:
    previous = ctx.overview.previous_period
    current = ctx.overview.averages.score
    if previous is None or previous.score is None or current is None:
        return
    delta = current - previous.score
    metric = InsightMetric(label="Score change", value=f"{delta:+d}")
    if delta >= 5:
        yield Insight(
            id="score-improving",
            kind="positive",
            category="habit",
            title="Your sleep score is improving",
            message=(
                f"Your average score rose from {previous.score} to {current} "
                "versus the previous period."
            ),
            metric=metric,
            priority=70,
        )
    elif delta <= -5:
        yield Insight(
            id="score-declining",
            kind="warning",
            category="habit",
            title="Your sleep score dropped",
            message=(
                f"Your average score fell from {previous.score} to {current} "
                "versus the previous period."
            ),
            metric=metric,
            priority=70,
        )


def _social_jetlag(ctx: InsightContext) -> Iterable[Insight]:
    jetlag = ctx.chronotype.social_jetlag_minutes
    if jetlag is not None and jetlag >= 60:
        yield Insight(
            id="social-jetlag",
            kind="tip",
            category="timing",
            title="Your weekends shift your body clock",
            message=(
                f"Your mid-sleep moves by {format_duration(jetlag)} between work nights and "
                "weekends, a bit like flying across time zones every week."
            ),
            metric=InsightMetric(label="Social jetlag", value=format_duration(jetlag)),
            priority=58,
        )


def _weekend_catch_up(ctx: InsightContext) -> Iterable[Insight]:
    weekend = [
        d.avg_duration_minutes
        for d in ctx.weekday
        if d.weekday in (4, 5) and d.avg_duration_minutes
    ]
    weekdays = [
        d.avg_duration_minutes
        for d in ctx.weekday
        if d.weekday not in (4, 5) and d.avg_duration_minutes
    ]
    if not weekend or not weekdays:
        return
    gap = sum(weekend) / len(weekend) - sum(weekdays) / len(weekdays)
    if gap >= 60:
        yield Insight(
            id="weekend-catch-up",
            kind="info",
            category="duration",
            title="You catch up on sleep at weekends",
            message=(
                f"Friday and Saturday nights run {format_duration(gap)} longer than the rest of "
                "the week, a sign you're short on sleep during the week."
            ),
            metric=InsightMetric(label="Weekend extra", value=_signed_duration(gap)),
            priority=35,
        )


def _tag_impacts(ctx: InsightContext) -> Iterable[Insight]:
    nights = ctx.overview.nights_tracked
    candidates: list[Insight] = []
    for impact in ctx.tags:
        if impact.nights_with < MIN_TAG_SAMPLE or nights - impact.nights_with < MIN_TAG_SAMPLE:
            continue
        name = impact.tag.name
        slug = _slug(name)
        q_impact = impact.quality_impact
        if q_impact is not None and abs(q_impact) >= 0.4:
            negative = q_impact < 0
            candidates.append(
                Insight(
                    id=f"tag-impact-{slug}",
                    kind="warning" if negative else "positive",
                    category="tags",
                    title=(
                        f"{name} lowers your sleep quality"
                        if negative
                        else f"{name} improves your sleep quality"
                    ),
                    message=(
                        f"On nights tagged {name} your quality averages "
                        f"{impact.avg_quality_with:.1f} vs "
                        f"{impact.avg_quality_without:.1f} otherwise."
                    ),
                    metric=InsightMetric(label="Quality impact", value=f"{q_impact:+.1f}"),
                    priority=min(95, 62 + round(abs(q_impact) * 15)),
                )
            )
            continue
        d_impact = impact.duration_impact_minutes
        if d_impact is not None and d_impact <= -30:
            candidates.append(
                Insight(
                    id=f"tag-duration-{slug}",
                    kind="warning",
                    category="tags",
                    title=f"{name} shortens your sleep",
                    message=(
                        f"Nights tagged {name} are {format_duration(d_impact)} shorter on average."
                    ),
                    metric=InsightMetric(label="Duration impact", value=_signed_duration(d_impact)),
                    priority=min(90, 58 + abs(d_impact) // 10),
                )
            )
    candidates.sort(key=lambda insight: -insight.priority)
    yield from candidates[:MAX_TAG_INSIGHTS]


def _fallback_tip(_: InsightContext) -> Iterable[Insight]:
    yield Insight(
        id="tip-consistent-wake-time",
        kind="tip",
        category="habit",
        title="Anchor your wake time",
        message=(
            "Waking at the same time every day, weekends included, is one of the most "
            "effective ways to sleep better."
        ),
        priority=10,
    )


DEFAULT_RULES: tuple[Rule, ...] = (
    _not_enough_data,
    _sleep_debt,
    _duration_vs_goal,
    _consistency,
    _goal_attainment,
    _trend,
    _quality,
    _tag_impacts,
    _bedtime_vs_target,
    _social_jetlag,
    _latency,
    _streak,
    _weekend_catch_up,
    _fallback_tip,
)


class RuleBasedInsightProvider:
    name = "rules"

    def __init__(self, rules: Iterable[Rule] = DEFAULT_RULES, limit: int = 12) -> None:
        self._rules = tuple(rules)
        self._limit = limit

    async def generate(self, context: InsightContext) -> list[Insight]:
        seen: set[str] = set()
        insights: list[Insight] = []
        for rule in self._rules:
            for insight in rule(context):
                if insight.id not in seen:
                    seen.add(insight.id)
                    insights.append(insight)
        insights.sort(key=lambda insight: (-insight.priority, insight.id))
        return insights[: self._limit]


def build_insight_provider() -> InsightProvider:
    return RuleBasedInsightProvider()
