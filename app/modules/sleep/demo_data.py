"""Synthetic but plausible sleep history for demo and seed accounts.

Tags have consistent, realistic effects (caffeine delays sleep onset and lowers
quality, exercise and meditation help, weekends run later and longer) so the
analytics and insights built on top of the data have something to say.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutils import get_zone, local_today
from app.modules.analytics.calculations import GoalTargets, sleep_score
from app.modules.sleep.models import SleepSession, SleepSource, SleepStatus
from app.modules.tags.models import Tag
from app.modules.users.models import User


@dataclass(frozen=True, slots=True)
class TagEffect:
    weekday_probability: float
    weekend_probability: float
    bedtime: float = 0.0
    latency: float = 0.0
    duration: float = 0.0
    awakenings: float = 0.0
    quality: float = 0.0


TAG_EFFECTS: dict[str, TagEffect] = {
    "Caffeine": TagEffect(0.35, 0.15, bedtime=10, latency=18, duration=-25, quality=-1.0),
    "Alcohol": TagEffect(
        0.08, 0.40, bedtime=25, latency=-4, duration=-20, awakenings=2, quality=-0.8
    ),
    "Exercise": TagEffect(0.45, 0.30, latency=-4, duration=12, quality=0.5),
    "Late meal": TagEffect(0.12, 0.25, bedtime=15, latency=6, awakenings=1, quality=-0.4),
    "Stress": TagEffect(
        0.25, 0.08, bedtime=15, latency=16, duration=-20, awakenings=1, quality=-0.7
    ),
    "Screen time": TagEffect(0.35, 0.45, bedtime=22, latency=9, duration=-10, quality=-0.3),
    "Nap": TagEffect(0.06, 0.15, latency=8, duration=-15),
    "Travel": TagEffect(0.03, 0.06, bedtime=40, latency=10, duration=-35, quality=-0.5),
    "Medication": TagEffect(0.04, 0.04, latency=-5, quality=0.1),
    "Reading": TagEffect(0.30, 0.25, latency=-5, quality=0.3),
    "Meditation": TagEffect(0.18, 0.12, latency=-7, duration=5, quality=0.4),
}

NOTES = (
    "Fell asleep quickly after a long walk.",
    "Neighbours were loud around midnight.",
    "Woke up before the alarm feeling rested.",
    "Late coffee with friends, took a while to drift off.",
    "Room was too warm, kept waking up.",
    "Read a few chapters before bed.",
    "Big deadline tomorrow, mind was racing.",
    "Tried a guided meditation, felt calmer.",
    "Dinner was later than usual.",
    "Slept like a rock.",
    "Weird dreams but solid sleep overall.",
    "Phone notifications woke me up once.",
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _night_values(
    night: date, tags: list[str], rng: random.Random, progress: float
) -> tuple[float, int, int, int, int, int]:
    """Return (bedtime minutes after night-of midnight, latency, duration,
    awakenings, quality, mood) for one night. ``progress`` in [0, 1] adds a
    gentle improvement trend towards the present."""
    weekend = night.weekday() in (4, 5)
    bedtime = (24 * 60 + 20 if weekend else 23 * 60 + 10) + rng.gauss(0, 30 if weekend else 22)
    latency = 12 + rng.gauss(0, 5)
    duration = (8 * 60 + 15 if weekend else 7 * 60 + 5) + rng.gauss(0, 35)
    awakenings = rng.choices((0, 1, 2, 3), weights=(35, 40, 18, 7))[0]
    quality = 3.4 + rng.gauss(0, 0.45) + progress * 0.4
    duration += progress * 15

    for name in tags:
        effect = TAG_EFFECTS[name]
        bedtime += effect.bedtime
        latency += effect.latency
        duration += effect.duration
        awakenings += round(effect.awakenings)
        quality += effect.quality
    quality -= max(0.0, latency - 25) / 40

    quality_int = round(_clamp(quality, 1, 5))
    mood = round(_clamp(quality_int + rng.choice((-1, 0, 0, 0, 1)), 1, 5))
    return (
        round(bedtime),
        round(_clamp(latency, 1, 120)),
        round(_clamp(duration, 4 * 60, 11 * 60)),
        int(_clamp(awakenings, 0, 8)),
        quality_int,
        mood,
    )


def build_history(
    *,
    user_id: uuid.UUID,
    tags: dict[str, Tag],
    timezone: str,
    end: date,
    nights: int,
    rng: random.Random,
    goal: GoalTargets | None = None,
    skip_probability: float = 0.08,
) -> list[SleepSession]:
    goal = goal or GoalTargets()
    zone = get_zone(timezone)
    sessions: list[SleepSession] = []
    start = end - timedelta(days=nights - 1)
    for offset in range(nights):
        night = start + timedelta(days=offset)
        if rng.random() < skip_probability:
            continue
        weekend = night.weekday() in (4, 5)
        chosen = [
            name
            for name, effect in TAG_EFFECTS.items()
            if name in tags
            and rng.random()
            < (effect.weekend_probability if weekend else effect.weekday_probability)
        ]
        bedtime, latency, duration, awakenings, quality, mood = _night_values(
            night, chosen, rng, progress=offset / max(1, nights - 1)
        )
        local_start = datetime.combine(night, time(0, 0), tzinfo=zone) + timedelta(minutes=bedtime)
        started_at = local_start.astimezone(get_zone("UTC"))
        ended_at = started_at + timedelta(minutes=duration)
        bedtime_minutes = (local_start.hour * 60 + local_start.minute) % 1440
        sessions.append(
            SleepSession(
                user_id=user_id,
                status=SleepStatus.COMPLETED,
                started_at=started_at,
                ended_at=ended_at,
                timezone=timezone,
                night_of=night,
                duration_minutes=duration,
                quality=quality,
                mood_on_wake=mood,
                sleep_latency_minutes=latency,
                awakenings=awakenings,
                notes=rng.choice(NOTES) if rng.random() < 0.22 else None,
                score=sleep_score(
                    duration_minutes=duration,
                    bedtime_minutes=bedtime_minutes,
                    goal=goal,
                    quality=quality,
                    sleep_latency_minutes=latency,
                    awakenings=awakenings,
                ),
                source=SleepSource.MANUAL,
                tags=[tags[name] for name in chosen],
                # Logged on waking, which keeps "sessions this week" stats honest.
                created_at=ended_at,
                updated_at=ended_at,
            )
        )
    return sessions


async def populate_demo_history(
    session: AsyncSession, user: User, *, nights: int, rng: random.Random
) -> int:
    """Stage ``nights`` of history ending last night for ``user`` (flushed, not committed)."""
    tags = {
        tag.name: tag for tag in await session.scalars(select(Tag).where(Tag.user_id == user.id))
    }
    timezone = user.preferences.timezone
    history = build_history(
        user_id=user.id,
        tags=tags,
        timezone=timezone,
        end=local_today(timezone) - timedelta(days=1),
        nights=nights,
        rng=rng,
    )
    session.add_all(history)
    await session.flush()
    return len(history)
