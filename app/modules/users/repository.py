from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import OAuthAccount, User


def normalize_email(email: str) -> str:
    return email.strip().lower()


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(func.lower(User.email) == normalize_email(email))
        return await self.session.scalar(stmt)

    async def email_exists(self, email: str) -> bool:
        stmt = select(User.id).where(func.lower(User.email) == normalize_email(email)).limit(1)
        return await self.session.scalar(stmt) is not None

    async def get_oauth_account(self, provider: str, account_id: str) -> OAuthAccount | None:
        stmt = select(OAuthAccount).where(
            OAuthAccount.provider == provider, OAuthAccount.provider_account_id == account_id
        )
        return await self.session.scalar(stmt)

    def add(self, user: User) -> None:
        self.session.add(user)

    async def delete(self, user: User) -> None:
        await self.session.delete(user)
