from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.errors import problem_responses
from app.db.session import DbSession
from app.modules.analytics.cache import AnalyticsCacheDep
from app.modules.auth.dependencies import CurrentUser
from app.modules.goals.schemas import GoalRead, GoalUpdate
from app.modules.goals.service import GoalService

router = APIRouter(prefix="/goals", tags=["goals"], responses=problem_responses(401))


def get_goal_service(session: DbSession, cache: AnalyticsCacheDep) -> GoalService:
    return GoalService(session, cache)


GoalServiceDep = Annotated[GoalService, Depends(get_goal_service)]


@router.get("", summary="Get sleep goals")
async def get_goals(user: CurrentUser, service: GoalServiceDep) -> GoalRead:
    """Defaults (8 h, 23:00-07:00, ±30 min) apply until goals are saved."""
    return await service.get(user)


@router.put("", summary="Replace sleep goals", responses=problem_responses(422))
async def update_goals(data: GoalUpdate, user: CurrentUser, service: GoalServiceDep) -> GoalRead:
    """Also re-scores every stored session against the new goals."""
    return await service.update(user, data)
