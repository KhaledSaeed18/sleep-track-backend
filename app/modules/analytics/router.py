from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import problem_responses
from app.modules.analytics.schemas import (
    AnalyticsOverview,
    Chronotype,
    DailyPoint,
    Distribution,
    TagImpactRead,
    WeekdayStat,
)
from app.modules.analytics.service import AnalyticsServiceDep, RangeQueryDep
from app.modules.auth.dependencies import CurrentUser

router = APIRouter(prefix="/analytics", tags=["analytics"], responses=problem_responses(401, 422))


@router.get("/overview", summary="Headline metrics for a date range")
async def get_overview(
    user: CurrentUser, service: AnalyticsServiceDep, period: RangeQueryDep
) -> AnalyticsOverview:
    """Defaults to the last 30 days. Bedtime and wake averages are circular means;
    `previous_period` covers the same number of days immediately before."""
    start, end = period.resolve(user.preferences.timezone)
    return await service.overview(user, start, end)


@router.get("/daily", summary="One entry per day in the range")
async def get_daily(
    user: CurrentUser, service: AnalyticsServiceDep, period: RangeQueryDep
) -> list[DailyPoint]:
    start, end = period.resolve(user.preferences.timezone)
    return await service.daily(user, start, end)


@router.get("/weekday", summary="Averages per weekday (0 = Monday)")
async def get_weekday(
    user: CurrentUser, service: AnalyticsServiceDep, period: RangeQueryDep
) -> list[WeekdayStat]:
    start, end = period.resolve(user.preferences.timezone)
    return await service.weekday(user, start, end)


@router.get("/tags", summary="How each tag relates to quality and duration")
async def get_tag_impact(
    user: CurrentUser, service: AnalyticsServiceDep, period: RangeQueryDep
) -> list[TagImpactRead]:
    start, end = period.resolve(user.preferences.timezone)
    return await service.tags(user, start, end)


@router.get("/distribution", summary="Histograms of duration, quality and bedtime hour")
async def get_distribution(
    user: CurrentUser, service: AnalyticsServiceDep, period: RangeQueryDep
) -> Distribution:
    start, end = period.resolve(user.preferences.timezone)
    return await service.distribution(user, start, end)


@router.get("/chronotype", summary="Chronotype and social jetlag")
async def get_chronotype(
    user: CurrentUser, service: AnalyticsServiceDep, period: RangeQueryDep
) -> Chronotype:
    """Based on mid-sleep on free (Friday/Saturday) nights versus work nights."""
    start, end = period.resolve(user.preferences.timezone)
    return await service.chronotype(user, start, end)
