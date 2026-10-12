"""Seed the database with showcase accounts.

    python -m app.scripts.seed

Creates (or recreates) ``demo@sleeptrack.app`` / ``DemoPass123`` with ~120
nights of tagged history, ``admin@sleeptrack.app`` / ``AdminPass123`` and a
handful of sample users so the admin dashboard has something to show.
Seeded accounts are deleted and rebuilt on every run, so data always ends
last night.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import date, time, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import generate_token, get_password_hasher
from app.core.timeutils import local_today, utcnow
from app.db.session import create_engine, create_session_factory
from app.modules.goals.models import SleepGoal
from app.modules.goals.service import goal_targets
from app.modules.sleep.demo_data import build_history
from app.modules.tags.models import Tag
from app.modules.users.models import User, UserRole
from app.modules.users.provisioning import provision_user

DEMO_EMAIL = "demo@sleeptrack.app"
DEMO_PASSWORD = "DemoPass123"  # noqa: S105 - documented showcase credentials
ADMIN_EMAIL = "admin@sleeptrack.app"
ADMIN_PASSWORD = "AdminPass123"  # noqa: S105 - documented showcase credentials
SAMPLE_DOMAIN = "sample.sleeptrack.app"


@dataclass(frozen=True, slots=True)
class SampleUser:
    first_name: str
    last_name: str
    timezone: str
    nights: int
    signed_up_days_ago: int


SAMPLE_USERS = (
    SampleUser("Amira", "Haddad", "Asia/Beirut", 45, 28),
    SampleUser("Lucas", "Martin", "Europe/Paris", 30, 25),
    SampleUser("Sofia", "Rossi", "Europe/Rome", 60, 22),
    SampleUser("Kenji", "Tanaka", "Asia/Tokyo", 20, 19),
    SampleUser("Maya", "Patel", "Europe/London", 14, 15),
    SampleUser("Omar", "Khalil", "Asia/Dubai", 35, 12),
    SampleUser("Emma", "Johnson", "America/New_York", 10, 9),
    SampleUser("Noah", "Schmidt", "Europe/Berlin", 7, 6),
    SampleUser("Lina", "Nasser", "Asia/Beirut", 5, 3),
    SampleUser("Diego", "Garcia", "America/Mexico_City", 3, 1),
)


async def _reset(session: AsyncSession, emails: list[str]) -> None:
    await session.execute(delete(User).where(User.email.in_(emails)))
    await session.execute(delete(User).where(User.email.like(f"%@{SAMPLE_DOMAIN}")))
    await session.flush()


async def _add_history(
    session: AsyncSession, user: User, *, nights: int, rng: random.Random, goal: SleepGoal | None
) -> int:
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
        goal=goal_targets(goal),
    )
    session.add_all(history)
    await session.flush()
    return len(history)


async def seed() -> None:
    settings = get_settings()
    hasher = get_password_hasher(settings)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    rng = random.Random(20261012)

    async with factory() as session:
        await _reset(session, [DEMO_EMAIL, ADMIN_EMAIL])

        demo = await provision_user(
            session,
            email=DEMO_EMAIL,
            first_name="Demo",
            last_name="User",
            password_hash=hasher.hash(DEMO_PASSWORD),
            timezone="Asia/Beirut",
            is_email_verified=True,
            date_of_birth=date(1998, 5, 1),
        )
        demo.created_at = utcnow() - timedelta(days=135)
        demo_goal = SleepGoal(
            user_id=demo.id,
            target_duration_minutes=480,
            target_bedtime=time(23, 0),
            target_wake_time=time(7, 0),
            bedtime_window_minutes=30,
        )
        session.add(demo_goal)
        await session.flush()
        demo_nights = await _add_history(session, demo, nights=130, rng=rng, goal=demo_goal)

        admin = await provision_user(
            session,
            email=ADMIN_EMAIL,
            first_name="Admin",
            last_name="User",
            password_hash=hasher.hash(ADMIN_PASSWORD),
            timezone="Asia/Beirut",
            role=UserRole.ADMIN,
            is_email_verified=True,
        )
        admin.created_at = utcnow() - timedelta(days=130)
        await _add_history(session, admin, nights=30, rng=rng, goal=None)

        for sample in SAMPLE_USERS:
            user = await provision_user(
                session,
                email=f"{sample.first_name}.{sample.last_name}@{SAMPLE_DOMAIN}".lower(),
                first_name=sample.first_name,
                last_name=sample.last_name,
                # Unusable random password: sample accounts are not meant for sign-in.
                password_hash=hasher.hash(generate_token()),
                timezone=sample.timezone,
                is_email_verified=True,
            )
            user.created_at = utcnow() - timedelta(days=sample.signed_up_days_ago)
            user.last_login_at = utcnow() - timedelta(days=rng.randint(0, 10))
            await _add_history(
                session,
                user,
                nights=min(sample.nights, sample.signed_up_days_ago),
                rng=rng,
                goal=None,
            )

        await session.commit()

    await engine.dispose()
    print(f"Seeded {DEMO_EMAIL} / {DEMO_PASSWORD} ({demo_nights} nights)")
    print(f"Seeded {ADMIN_EMAIL} / {ADMIN_PASSWORD} (admin)")
    print(f"Seeded {len(SAMPLE_USERS)} sample users")


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
