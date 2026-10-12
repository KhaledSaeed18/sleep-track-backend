from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import (
    ActionToken,
    ActionTokenPurpose,
    AuditLog,
    AuthSession,
    RefreshToken,
)


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ sessions
    def add(self, obj: AuthSession | RefreshToken | ActionToken | AuditLog) -> None:
        self.session.add(obj)

    async def get_refresh_token_for_update(self, token_hash: str) -> RefreshToken | None:
        # Row lock serialises concurrent refreshes of the same token so exactly
        # one rotation wins and the loser takes the reuse path.
        stmt = (
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .with_for_update(of=RefreshToken)
        )
        return await self.session.scalar(stmt)

    async def get_session(self, user_id: uuid.UUID, session_id: uuid.UUID) -> AuthSession | None:
        stmt = select(AuthSession).where(
            AuthSession.id == session_id, AuthSession.user_id == user_id
        )
        return await self.session.scalar(stmt)

    async def list_active_sessions(self, user_id: uuid.UUID, now: datetime) -> list[AuthSession]:
        stmt = (
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
            .order_by(AuthSession.last_used_at.desc())
        )
        return list(await self.session.scalars(stmt))

    async def revoke_sessions(
        self,
        user_id: uuid.UUID,
        *,
        reason: str,
        now: datetime,
        except_session_id: uuid.UUID | None = None,
    ) -> list[uuid.UUID]:
        stmt = (
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason=reason)
            .returning(AuthSession.id)
        )
        if except_session_id is not None:
            stmt = stmt.where(AuthSession.id != except_session_id)
        result = await self.session.execute(stmt.execution_options(synchronize_session=False))
        return list(result.scalars())

    async def delete_expired_sessions(self, before: datetime) -> int:
        stmt = delete(AuthSession).where(
            or_(AuthSession.expires_at < before, AuthSession.revoked_at < before)
        )
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0)

    # ------------------------------------------------------------------ action tokens
    async def get_action_token_for_update(
        self, token_hash: str, purpose: ActionTokenPurpose
    ) -> ActionToken | None:
        stmt = (
            select(ActionToken)
            .where(ActionToken.token_hash == token_hash, ActionToken.purpose == purpose)
            .with_for_update()
        )
        return await self.session.scalar(stmt)

    async def invalidate_action_tokens(
        self, user_id: uuid.UUID, purpose: ActionTokenPurpose, now: datetime
    ) -> None:
        stmt = (
            update(ActionToken)
            .where(
                ActionToken.user_id == user_id,
                ActionToken.purpose == purpose,
                ActionToken.used_at.is_(None),
            )
            .values(used_at=now)
        )
        await self.session.execute(stmt.execution_options(synchronize_session=False))

    async def delete_expired_action_tokens(self, before: datetime) -> int:
        stmt = delete(ActionToken).where(
            or_(ActionToken.expires_at < before, ActionToken.used_at < before)
        )
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0)

    # ------------------------------------------------------------------ audit
    async def list_audit_logs(
        self, user_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[list[AuditLog], int]:
        base = select(AuditLog).where(AuditLog.user_id == user_id)
        total = await self.session.scalar(select(func.count()).select_from(base.subquery()))
        stmt = base.order_by(AuditLog.created_at.desc(), AuditLog.id).offset(offset).limit(limit)
        return list(await self.session.scalars(stmt)), int(total or 0)
