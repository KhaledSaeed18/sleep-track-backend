from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import problem_responses
from app.modules.analytics.service import RangeQueryDep
from app.modules.auth.dependencies import CurrentUser
from app.modules.insights.schemas import Insight
from app.modules.insights.service import InsightServiceDep

router = APIRouter(prefix="/insights", tags=["insights"], responses=problem_responses(401, 422))


@router.get("", summary="Personalised insights for a date range")
async def list_insights(
    user: CurrentUser, service: InsightServiceDep, period: RangeQueryDep
) -> list[Insight]:
    """Sorted by priority (highest first). Defaults to the last 30 days."""
    start, end = period.resolve(user.preferences.timezone)
    return await service.insights(user, start, end)
