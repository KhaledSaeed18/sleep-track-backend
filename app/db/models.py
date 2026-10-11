"""Imports every ORM model so ``Base.metadata`` is complete (Alembic, tests)."""

from app.db.base import Base
from app.modules.auth.models import ActionToken, AuditLog, AuthSession, RefreshToken
from app.modules.goals.models import SleepGoal
from app.modules.sleep.models import SleepSession, sleep_session_tags
from app.modules.tags.models import Tag
from app.modules.users.models import OAuthAccount, User, UserPreferences

__all__ = [
    "ActionToken",
    "AuditLog",
    "AuthSession",
    "Base",
    "OAuthAccount",
    "RefreshToken",
    "SleepGoal",
    "SleepSession",
    "Tag",
    "User",
    "UserPreferences",
    "sleep_session_tags",
]
