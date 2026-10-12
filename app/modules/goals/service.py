from __future__ import annotations

import uuid
from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutils import local_today, minutes_of_day, parse_hhmm
from app.modules.analytics.cache import AnalyticsCache
from app.modules.analytics.calculations import GoalTargets
from app.modules.goals.models import SleepGoal
from app.modules.goals.recommendations import recommend
from app.modules.goals.schemas import GoalRead, GoalUpdate, RecommendedRange
from app.modules.sleep.scoring import recompute_scores
from app.modules.users.models import User

DEFAULT_GOAL = SleepGoal(
    target_duration_minutes=480,
    target_bedtime=time(23, 0),
    target_wake_time=time(7, 0),
    bedtime_window_minutes=30,
)


def goal_targets(goal: SleepGoal | None) -> GoalTargets:
    goal = goal or DEFAULT_GOAL
    return GoalTargets(
        duration_minutes=goal.target_duration_minutes,
        bedtime_minutes=minutes_of_day(goal.target_bedtime),
        wake_minutes=minutes_of_day(goal.target_wake_time),
        window_minutes=goal.bedtime_window_minutes,
    )


async def load_goal_targets(session: AsyncSession, user_id: uuid.UUID) -> GoalTargets:
    return goal_targets(await session.get(SleepGoal, user_id))


class GoalService:
    def __init__(self, session: AsyncSession, cache: AnalyticsCache) -> None:
        self.session = session
        self.cache = cache

    def _to_read(self, goal: SleepGoal, user: User) -> GoalRead:
        rec = recommend(user.date_of_birth, local_today(user.preferences.timezone))
        return GoalRead(
            target_duration_minutes=goal.target_duration_minutes,
            target_bedtime=goal.target_bedtime.strftime("%H:%M"),
            target_wake_time=goal.target_wake_time.strftime("%H:%M"),
            bedtime_window_minutes=goal.bedtime_window_minutes,
            recommended=RecommendedRange(
                min_hours=rec.min_hours, max_hours=rec.max_hours, age_group=rec.age_group
            ),
        )

    async def get(self, user: User) -> GoalRead:
        goal = await self.session.get(SleepGoal, user.id) or DEFAULT_GOAL
        return self._to_read(goal, user)

    async def update(self, user: User, data: GoalUpdate) -> GoalRead:
        goal = await self.session.get(SleepGoal, user.id)
        if goal is None:
            goal = SleepGoal(user_id=user.id)
            self.session.add(goal)
        goal.target_duration_minutes = data.target_duration_minutes
        goal.target_bedtime = parse_hhmm(data.target_bedtime)
        goal.target_wake_time = parse_hhmm(data.target_wake_time)
        goal.bedtime_window_minutes = data.bedtime_window_minutes
        await self.session.flush()
        # Scores are stored for sorting/filtering, so they follow goal changes.
        await recompute_scores(self.session, user.id, goal_targets(goal))
        await self.session.commit()
        await self.cache.invalidate(user.id)
        return self._to_read(goal, user)
