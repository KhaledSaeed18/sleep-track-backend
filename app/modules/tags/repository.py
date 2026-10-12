from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.sleep.models import sleep_session_tags
from app.modules.tags.models import Tag


class TagRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_with_usage(self, user_id: uuid.UUID) -> list[tuple[Tag, int]]:
        # Grouping by the primary key lets PostgreSQL select the remaining tag
        # columns without listing them (functional dependency).
        stmt = (
            select(Tag, func.count(sleep_session_tags.c.session_id))
            .outerjoin(sleep_session_tags, sleep_session_tags.c.tag_id == Tag.id)
            .where(Tag.user_id == user_id)
            .group_by(Tag.id)
            .order_by(func.lower(Tag.name))
        )
        rows = await self.session.execute(stmt)
        return [(tag, int(count)) for tag, count in rows]

    async def usage_count(self, tag_id: uuid.UUID) -> int:
        stmt = select(func.count()).where(sleep_session_tags.c.tag_id == tag_id)
        return int(await self.session.scalar(stmt) or 0)

    async def get(self, user_id: uuid.UUID, tag_id: uuid.UUID) -> Tag | None:
        stmt = select(Tag).where(Tag.id == tag_id, Tag.user_id == user_id)
        return await self.session.scalar(stmt)

    async def get_many(self, user_id: uuid.UUID, tag_ids: Sequence[uuid.UUID]) -> list[Tag]:
        if not tag_ids:
            return []
        stmt = select(Tag).where(Tag.user_id == user_id, Tag.id.in_(tag_ids))
        return list(await self.session.scalars(stmt))

    async def list_all(self, user_id: uuid.UUID) -> list[Tag]:
        stmt = select(Tag).where(Tag.user_id == user_id).order_by(func.lower(Tag.name))
        return list(await self.session.scalars(stmt))

    async def name_exists(
        self, user_id: uuid.UUID, name: str, *, exclude_id: uuid.UUID | None = None
    ) -> bool:
        stmt = select(Tag.id).where(Tag.user_id == user_id, func.lower(Tag.name) == name.lower())
        if exclude_id is not None:
            stmt = stmt.where(Tag.id != exclude_id)
        return await self.session.scalar(stmt.limit(1)) is not None

    def add(self, tag: Tag) -> None:
        self.session.add(tag)

    async def delete(self, tag: Tag) -> None:
        await self.session.delete(tag)
