from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.modules.analytics.cache import AnalyticsCache
from app.modules.tags.models import Tag
from app.modules.tags.repository import TagRepository
from app.modules.tags.schemas import TagCreate, TagRead, TagUpdate


def to_read(tag: Tag, usage_count: int = 0) -> TagRead:
    return TagRead(
        id=tag.id,
        name=tag.name,
        color=tag.color,
        icon=tag.icon,
        is_system=tag.is_system,
        usage_count=usage_count,
        created_at=tag.created_at,
    )


class TagService:
    def __init__(self, session: AsyncSession, cache: AnalyticsCache) -> None:
        self.session = session
        self.repo = TagRepository(session)
        self.cache = cache

    async def list_tags(self, user_id: uuid.UUID) -> list[TagRead]:
        return [to_read(tag, count) for tag, count in await self.repo.list_with_usage(user_id)]

    async def create(self, user_id: uuid.UUID, data: TagCreate) -> TagRead:
        if await self.repo.name_exists(user_id, data.name):
            raise ConflictError(f"A tag named '{data.name}' already exists.")
        tag = Tag(
            user_id=user_id, name=data.name, color=data.color, icon=data.icon, is_system=False
        )
        self.repo.add(tag)
        await self._commit(data.name)
        return to_read(tag)

    async def update(self, user_id: uuid.UUID, tag_id: uuid.UUID, data: TagUpdate) -> TagRead:
        tag = await self._get(user_id, tag_id)
        changes = data.model_dump(exclude_unset=True)
        if changes.get("name") and await self.repo.name_exists(
            user_id, changes["name"], exclude_id=tag.id
        ):
            raise ConflictError(f"A tag named '{changes['name']}' already exists.")
        for field in ("name", "color"):
            if changes.get(field) is not None:
                setattr(tag, field, changes[field])
        if "icon" in changes:
            tag.icon = changes["icon"]
        await self._commit(tag.name)
        await self.cache.invalidate(user_id)
        return to_read(tag, await self.repo.usage_count(tag.id))

    async def delete(self, user_id: uuid.UUID, tag_id: uuid.UUID) -> None:
        tag = await self._get(user_id, tag_id)
        await self.repo.delete(tag)
        await self.session.commit()
        await self.cache.invalidate(user_id)

    async def resolve(self, user_id: uuid.UUID, tag_ids: Sequence[uuid.UUID] | None) -> list[Tag]:
        """Load the user's tags by id, rejecting ids that are unknown or foreign."""
        if not tag_ids:
            return []
        unique_ids = list(dict.fromkeys(tag_ids))
        tags = await self.repo.get_many(user_id, unique_ids)
        if len(tags) != len(unique_ids):
            found = {tag.id for tag in tags}
            missing = ", ".join(str(tag_id) for tag_id in unique_ids if tag_id not in found)
            raise ValidationFailedError.for_field("tag_ids", f"unknown tag ids: {missing}")
        return tags

    async def _commit(self, name: str) -> None:
        # The unique index closes the race between the existence check and insert.
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(f"A tag named '{name}' already exists.") from exc

    async def _get(self, user_id: uuid.UUID, tag_id: uuid.UUID) -> Tag:
        tag = await self.repo.get(user_id, tag_id)
        if tag is None:
            raise NotFoundError("Tag not found.")
        return tag
