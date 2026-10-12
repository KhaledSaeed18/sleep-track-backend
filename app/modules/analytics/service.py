from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated

from fastapi import Depends, Query
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailedError
from app.core.timeutils import local_today
from app.db.session import DbSession
from app.modules.analytics import builders
from app.modules.analytics.cache import AnalyticsCache, AnalyticsCacheDep
from app.modules.analytics.calculations import SessionPoint, tag_impacts
from app.modules.analytics.schemas import (
    AnalyticsOverview,
    Chronotype,
    DailyPoint,
    Distribution,
    TagImpactRead,
    WeekdayStat,
)
from app.modules.goals.service import load_goal_targets
from app.modules.sleep.repository import SleepRepository
from app.modules.tags.repository import TagRepository
from app.modules.tags.service import to_read as tag_to_read
from app.modules.users.models import User

DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 400
# Outer bounds for any requested date. Ranges are shifted backwards for the
# previous-period comparison, which overflows near ``date.min``/``date.max``.
EARLIEST_DATE = date(1900, 1, 1)
LATEST_DATE = date(2999, 12, 31)


def ensure_supported_date(value: date | None, field: str) -> None:
    if value is not None and not EARLIEST_DATE <= value <= LATEST_DATE:
        raise ValidationFailedError.for_field(
            field, f"must be between {EARLIEST_DATE} and {LATEST_DATE}"
        )


_OVERVIEW = TypeAdapter(AnalyticsOverview)
_DAILY = TypeAdapter(list[DailyPoint])
_WEEKDAY = TypeAdapter(list[WeekdayStat])
_TAGS = TypeAdapter(list[TagImpactRead])
_DISTRIBUTION = TypeAdapter(Distribution)
_CHRONOTYPE = TypeAdapter(Chronotype)


@dataclass(frozen=True, slots=True)
class RangeQuery:
    date_from: date | None
    date_to: date | None

    def resolve(self, tz: str) -> tuple[date, date]:
        ensure_supported_date(self.date_from, "from")
        ensure_supported_date(self.date_to, "to")
        end = self.date_to or local_today(tz)
        start = self.date_from or max(EARLIEST_DATE, end - timedelta(days=DEFAULT_RANGE_DAYS - 1))
        if start > end:
            raise ValidationFailedError.for_field("from", "must be on or before 'to'")
        if (end - start).days + 1 > MAX_RANGE_DAYS:
            raise ValidationFailedError.for_field(
                "from", f"date range cannot exceed {MAX_RANGE_DAYS} days"
            )
        return start, end


def range_query(
    date_from: Annotated[
        date | None, Query(alias="from", description="Start date (inclusive), local YYYY-MM-DD")
    ] = None,
    date_to: Annotated[
        date | None, Query(alias="to", description="End date (inclusive); defaults to today")
    ] = None,
) -> RangeQuery:
    return RangeQuery(date_from, date_to)


RangeQueryDep = Annotated[RangeQuery, Depends(range_query)]


class AnalyticsService:
    def __init__(self, session: AsyncSession, cache: AnalyticsCache) -> None:
        self.session = session
        self.cache = cache
        self.sleep = SleepRepository(session)

    async def points(self, user: User, start: date, end: date) -> list[SessionPoint]:
        return await self.sleep.completed_points(user.id, start, end)

    async def overview(self, user: User, start: date, end: date) -> AnalyticsOverview:
        async def compute() -> AnalyticsOverview:
            return await self.compute_overview(user, start, end)

        return await self.cache.get_or_compute(
            user.id, "overview", (start, end), _OVERVIEW, compute
        )

    async def compute_overview(self, user: User, start: date, end: date) -> AnalyticsOverview:
        tz = user.preferences.timezone
        prev_start, prev_end = builders.previous_range(start, end)
        return builders.build_overview(
            points=await self.points(user, start, end),
            previous_points=await self.points(user, prev_start, prev_end),
            start=start,
            end=end,
            goal=await load_goal_targets(self.session, user.id),
            tz=tz,
            today=local_today(tz),
        )

    async def daily(self, user: User, start: date, end: date) -> list[DailyPoint]:
        async def compute() -> list[DailyPoint]:
            points = await self.points(user, start, end)
            return builders.build_daily(points, start, end, user.preferences.timezone)

        return await self.cache.get_or_compute(user.id, "daily", (start, end), _DAILY, compute)

    async def weekday(self, user: User, start: date, end: date) -> list[WeekdayStat]:
        async def compute() -> list[WeekdayStat]:
            points = await self.points(user, start, end)
            return builders.build_weekday(points, user.preferences.timezone)

        return await self.cache.get_or_compute(user.id, "weekday", (start, end), _WEEKDAY, compute)

    async def tags(self, user: User, start: date, end: date) -> list[TagImpactRead]:
        async def compute() -> list[TagImpactRead]:
            return await self.compute_tags(user, await self.points(user, start, end))

        return await self.cache.get_or_compute(user.id, "tags", (start, end), _TAGS, compute)

    async def compute_tags(self, user: User, points: list[SessionPoint]) -> list[TagImpactRead]:
        tags = {tag.id: tag for tag in await TagRepository(self.session).list_all(user.id)}
        usage = await self.sleep.tag_usage(list(tags))
        return [
            TagImpactRead(
                tag=tag_to_read(tags[impact.tag_id], usage.get(impact.tag_id, 0)),
                nights_with=impact.nights_with,
                avg_quality_with=impact.avg_quality_with,
                avg_quality_without=impact.avg_quality_without,
                avg_duration_with=impact.avg_duration_with,
                avg_duration_without=impact.avg_duration_without,
                quality_impact=impact.quality_impact,
                duration_impact_minutes=impact.duration_impact_minutes,
            )
            for impact in tag_impacts(points, tags)
        ]

    async def distribution(self, user: User, start: date, end: date) -> Distribution:
        async def compute() -> Distribution:
            points = await self.points(user, start, end)
            return builders.build_distribution(points, user.preferences.timezone)

        return await self.cache.get_or_compute(
            user.id, "distribution", (start, end), _DISTRIBUTION, compute
        )

    async def chronotype(self, user: User, start: date, end: date) -> Chronotype:
        async def compute() -> Chronotype:
            points = await self.points(user, start, end)
            return builders.build_chronotype(points, user.preferences.timezone)

        return await self.cache.get_or_compute(
            user.id, "chronotype", (start, end), _CHRONOTYPE, compute
        )


def get_analytics_service(session: DbSession, cache: AnalyticsCacheDep) -> AnalyticsService:
    return AnalyticsService(session, cache)


AnalyticsServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]
