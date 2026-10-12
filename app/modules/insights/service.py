from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import Depends, Request

from app.modules.analytics import builders
from app.modules.analytics.service import AnalyticsService, AnalyticsServiceDep
from app.modules.goals.service import load_goal_targets
from app.modules.insights.providers import InsightContext, InsightProvider
from app.modules.insights.schemas import Insight
from app.modules.users.models import User


class InsightService:
    def __init__(self, analytics: AnalyticsService, provider: InsightProvider) -> None:
        self.analytics = analytics
        self.provider = provider

    async def build_context(self, user: User, start: date, end: date) -> InsightContext:
        tz = user.preferences.timezone
        points = await self.analytics.points(user, start, end)
        return InsightContext(
            overview=await self.analytics.overview(user, start, end),
            weekday=builders.build_weekday(points, tz),
            tags=await self.analytics.compute_tags(user, points),
            chronotype=builders.build_chronotype(points, tz),
            goal=await load_goal_targets(self.analytics.session, user.id),
        )

    async def insights(self, user: User, start: date, end: date) -> list[Insight]:
        return await self.provider.generate(await self.build_context(user, start, end))


def get_insight_service(request: Request, analytics: AnalyticsServiceDep) -> InsightService:
    return InsightService(analytics, request.app.state.insight_provider)


InsightServiceDep = Annotated[InsightService, Depends(get_insight_service)]
