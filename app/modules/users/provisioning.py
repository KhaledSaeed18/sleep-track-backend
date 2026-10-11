"""Creates fully initialised accounts (profile, preferences, default tags).

Shared by registration, OAuth sign-up, demo accounts and the seed scripts so
every entry point produces the same shape of account.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutils import utcnow
from app.modules.tags.defaults import build_default_tags
from app.modules.users.models import User, UserPreferences, UserRole
from app.modules.users.repository import normalize_email


async def provision_user(
    session: AsyncSession,
    *,
    email: str,
    first_name: str,
    last_name: str = "",
    password_hash: str | None = None,
    timezone: str = "UTC",
    role: UserRole = UserRole.USER,
    is_email_verified: bool = False,
    is_demo: bool = False,
    date_of_birth: date | None = None,
    avatar_url: str | None = None,
) -> User:
    user_id = uuid.uuid4()
    now = utcnow()
    user = User(
        id=user_id,
        email=normalize_email(email),
        first_name=first_name,
        last_name=last_name,
        password_hash=password_hash,
        role=role,
        is_active=True,
        is_email_verified=is_email_verified,
        email_verified_at=now if is_email_verified else None,
        is_demo=is_demo,
        date_of_birth=date_of_birth,
        avatar_url=avatar_url,
        created_at=now,
        updated_at=now,
        oauth_accounts=[],
        preferences=UserPreferences(user_id=user_id, timezone=timezone),
    )
    session.add(user)
    # Flush now: rows that reference the user (tags, auth sessions, audit
    # entries) have no ORM relationship to it, so the unit of work cannot
    # order their INSERTs after the user's on its own.
    await session.flush()
    session.add_all(build_default_tags(user_id))
    return user
