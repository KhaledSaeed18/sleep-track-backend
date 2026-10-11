"""Bridges stored sleep sessions and the pure scoring function."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.analytics.calculations import GoalTargets, local_minutes, sleep_score
from app.modules.sleep.models import SleepSession, SleepStatus


def score_session(session: SleepSession, goal: GoalTargets) -> int | None:
    if session.ended_at is None or session.duration_minutes is None:
        return None
    return sleep_score(
        duration_minutes=session.duration_minutes,
        bedtime_minutes=local_minutes(session.started_at, session.timezone),
        goal=goal,
        quality=session.quality,
        sleep_latency_minutes=session.sleep_latency_minutes,
        awakenings=session.awakenings,
    )


async def recompute_scores(db: AsyncSession, user_id: uuid.UUID, goal: GoalTargets) -> int:
    """Re-score every completed session for a user using a single bulk UPDATE."""
    rows = await db.execute(
        select(
            SleepSession.id,
            SleepSession.started_at,
            SleepSession.timezone,
            SleepSession.duration_minutes,
            SleepSession.quality,
            SleepSession.sleep_latency_minutes,
            SleepSession.awakenings,
        ).where(SleepSession.user_id == user_id, SleepSession.status == SleepStatus.COMPLETED)
    )
    params = [
        {
            "id": row.id,
            "score": sleep_score(
                duration_minutes=row.duration_minutes,
                bedtime_minutes=local_minutes(row.started_at, row.timezone),
                goal=goal,
                quality=row.quality,
                sleep_latency_minutes=row.sleep_latency_minutes,
                awakenings=row.awakenings,
            ),
        }
        for row in rows
    ]
    if params:
        await db.execute(update(SleepSession), params)
    return len(params)
