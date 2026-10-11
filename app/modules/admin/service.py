from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import Date, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.core.deps import ClientInfo
from app.core.errors import ForbiddenError, NotFoundError
from app.core.pagination import PageParams
from app.core.timeutils import daterange, utcnow
from app.modules.admin.schemas import (
    AdminStats,
    AdminUser,
    AdminUserPage,
    AdminUserUpdate,
    DailyCount,
)
from app.modules.auth.audit import record_audit
from app.modules.auth.models import AuditEvent, AuthSession
from app.modules.auth.repository import AuthRepository
from app.modules.auth.revocation import TokenRevocationStore
from app.modules.sleep.models import SleepSession
from app.modules.users.models import User, UserRole
from app.modules.users.schemas import UserBase

SIGNUP_SERIES_DAYS = 30


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class AdminService:
    def __init__(self, session: AsyncSession, revocations: TokenRevocationStore) -> None:
        self.session = session
        self.revocations = revocations

    async def stats(self) -> AdminStats:
        now = utcnow()
        week_ago = now - timedelta(days=7)
        series_start = (now - timedelta(days=SIGNUP_SERIES_DAYS - 1)).date()

        users_total = await self.session.scalar(select(func.count()).select_from(User))
        users_new = await self.session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= week_ago)
        )
        users_active = await self.session.scalar(
            select(func.count(func.distinct(AuthSession.user_id))).where(
                AuthSession.last_used_at >= week_ago
            )
        )
        sessions_total = await self.session.scalar(select(func.count()).select_from(SleepSession))
        sessions_week = await self.session.scalar(
            select(func.count())
            .select_from(SleepSession)
            .where(SleepSession.created_at >= week_ago)
        )
        signup_day = cast(func.timezone("UTC", User.created_at), Date)
        rows = await self.session.execute(
            select(signup_day, func.count()).where(signup_day >= series_start).group_by(signup_day)
        )
        counts = {day: int(count) for day, count in rows.tuples()}
        return AdminStats(
            users_total=int(users_total or 0),
            users_active_7d=int(users_active or 0),
            users_new_7d=int(users_new or 0),
            sessions_total=int(sessions_total or 0),
            sessions_7d=int(sessions_week or 0),
            signups_daily=[
                DailyCount(date=day, count=counts.get(day, 0))
                for day in daterange(series_start, now.date())
            ],
        )

    @staticmethod
    def _to_admin(user: User, sessions_count: int) -> AdminUser:
        base = UserBase.model_validate(user).model_dump()
        return AdminUser(**base, is_active=user.is_active, sessions_count=sessions_count)

    async def _sessions_count(self, user_id: uuid.UUID) -> int:
        stmt = select(func.count()).where(SleepSession.user_id == user_id)
        return int(await self.session.scalar(stmt) or 0)

    async def list_users(
        self,
        page: PageParams,
        *,
        query: str | None,
        role: UserRole | None,
        is_active: bool | None,
    ) -> AdminUserPage:
        conditions = []
        if query:
            pattern = f"%{_escape_like(query)}%"
            conditions.append(
                or_(
                    User.email.ilike(pattern, escape="\\"),
                    User.first_name.ilike(pattern, escape="\\"),
                    User.last_name.ilike(pattern, escape="\\"),
                )
            )
        if role is not None:
            conditions.append(User.role == role)
        if is_active is not None:
            conditions.append(User.is_active == is_active)

        total = await self.session.scalar(select(func.count()).select_from(User).where(*conditions))
        counts = (
            select(SleepSession.user_id, func.count().label("sessions_count"))
            .group_by(SleepSession.user_id)
            .subquery()
        )
        stmt = (
            select(User, func.coalesce(counts.c.sessions_count, 0))
            .outerjoin(counts, counts.c.user_id == User.id)
            .where(*conditions)
            .options(noload(User.preferences))
            .order_by(User.created_at.desc(), User.id)
            .offset(page.offset)
            .limit(page.limit)
        )
        rows = (await self.session.execute(stmt)).tuples()
        items = [self._to_admin(user, int(count)) for user, count in rows]
        return AdminUserPage.build(items, total=int(total or 0), params=page)

    async def update_user(
        self, admin: User, user_id: uuid.UUID, data: AdminUserUpdate, client: ClientInfo
    ) -> AdminUser:
        user = await self.session.get(User, user_id)
        if user is None:
            raise NotFoundError("User not found.")
        if user.id == admin.id and (data.is_active is False or data.role is UserRole.USER):
            raise ForbiddenError("You cannot disable or demote your own account.")

        changes: dict[str, object] = {}
        revoked: list[uuid.UUID] = []
        if data.role is not None and data.role is not user.role:
            changes["role"] = data.role.value
            user.role = data.role
        if data.is_active is not None and data.is_active != user.is_active:
            changes["is_active"] = data.is_active
            user.is_active = data.is_active
            if not data.is_active:
                revoked = await AuthRepository(self.session).revoke_sessions(
                    user.id, reason="disabled_by_admin", now=utcnow()
                )
        if changes:
            record_audit(
                self.session,
                AuditEvent.ACCOUNT_UPDATED_BY_ADMIN,
                user_id=user.id,
                client=client,
                metadata={"admin_id": str(admin.id), "changes": changes},
            )
        await self.session.commit()
        await self.revocations.revoke_sessions(revoked)
        return self._to_admin(user, await self._sessions_count(user.id))
