from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import (
    ColumnElement,
    DateTime,
    Select,
    and_,
    delete,
    exists,
    func,
    literal_column,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import PageParams, SortSpec
from app.modules.analytics.calculations import SessionPoint
from app.modules.sleep.models import SleepSession, SleepStatus, sleep_session_tags

# Running sessions have no end yet; treat them as open-ended for overlap checks.
INFINITY: ColumnElement[datetime] = literal_column(
    "'infinity'::timestamptz", type_=DateTime(timezone=True)
)


@dataclass(frozen=True, slots=True)
class SessionFilters:
    date_from: date | None = None
    date_to: date | None = None
    min_quality: int | None = None
    max_quality: int | None = None
    tag_ids: Sequence[uuid.UUID] = ()
    query: str | None = None
    status: SleepStatus | None = None


SORTABLE_COLUMNS = {
    "night_of": SleepSession.night_of,
    "started_at": SleepSession.started_at,
    "duration_minutes": SleepSession.duration_minutes,
    "quality": SleepSession.quality,
    "score": SleepSession.score,
}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SleepRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _filtered(self, user_id: uuid.UUID, filters: SessionFilters) -> Select[SleepSession]:
        conditions: list[ColumnElement[bool]] = [SleepSession.user_id == user_id]
        if filters.status is not None:
            conditions.append(SleepSession.status == filters.status)
        if filters.date_from is not None:
            conditions.append(SleepSession.night_of >= filters.date_from)
        if filters.date_to is not None:
            conditions.append(SleepSession.night_of <= filters.date_to)
        if filters.min_quality is not None:
            conditions.append(SleepSession.quality >= filters.min_quality)
        if filters.max_quality is not None:
            conditions.append(SleepSession.quality <= filters.max_quality)
        if filters.query:
            conditions.append(
                SleepSession.notes.ilike(f"%{_escape_like(filters.query)}%", escape="\\")
            )
        if filters.tag_ids:
            conditions.append(
                exists().where(
                    sleep_session_tags.c.session_id == SleepSession.id,
                    sleep_session_tags.c.tag_id.in_(filters.tag_ids),
                )
            )
        return select(SleepSession).where(and_(*conditions))

    async def search(
        self, user_id: uuid.UUID, filters: SessionFilters, sort: SortSpec, page: PageParams
    ) -> tuple[list[SleepSession], int]:
        stmt = self._filtered(user_id, filters)
        total = await self.session.scalar(
            select(func.count()).select_from(stmt.order_by(None).subquery())
        )
        column = SORTABLE_COLUMNS[sort.field]
        ordering = column.desc().nulls_last() if sort.descending else column.asc().nulls_last()
        tiebreak = SleepSession.id.desc() if sort.descending else SleepSession.id.asc()
        rows = await self.session.scalars(
            stmt.order_by(ordering, tiebreak).offset(page.offset).limit(page.limit)
        )
        return list(rows), int(total or 0)

    async def get(self, user_id: uuid.UUID, session_id: uuid.UUID) -> SleepSession | None:
        stmt = select(SleepSession).where(
            SleepSession.id == session_id, SleepSession.user_id == user_id
        )
        return await self.session.scalar(stmt)

    async def get_active(
        self, user_id: uuid.UUID, *, for_update: bool = False
    ) -> SleepSession | None:
        stmt = select(SleepSession).where(
            SleepSession.user_id == user_id, SleepSession.status == SleepStatus.IN_PROGRESS
        )
        if for_update:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    async def has_overlap(
        self,
        user_id: uuid.UUID,
        start: datetime,
        end: datetime | None,
        *,
        exclude_id: uuid.UUID | None = None,
    ) -> bool:
        """True when ``[start, end)`` intersects another session; running
        sessions are treated as open-ended."""
        conditions: list[ColumnElement[bool]] = [
            SleepSession.user_id == user_id,
            func.coalesce(SleepSession.ended_at, INFINITY) > start,
        ]
        if end is not None:
            conditions.append(SleepSession.started_at < end)
        if exclude_id is not None:
            conditions.append(SleepSession.id != exclude_id)
        stmt = select(exists().where(and_(*conditions)))
        return bool(await self.session.scalar(stmt))

    async def bulk_delete(self, user_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> int:
        stmt = delete(SleepSession).where(SleepSession.user_id == user_id, SleepSession.id.in_(ids))
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0)

    async def tag_usage(self, tag_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        if not tag_ids:
            return {}
        stmt = (
            select(sleep_session_tags.c.tag_id, func.count())
            .where(sleep_session_tags.c.tag_id.in_(tag_ids))
            .group_by(sleep_session_tags.c.tag_id)
        )
        return {tag_id: int(count) for tag_id, count in (await self.session.execute(stmt))}

    async def completed_points(
        self, user_id: uuid.UUID, date_from: date, date_to: date
    ) -> list[SessionPoint]:
        """Completed sessions in a ``night_of`` range with their tag ids, in one query."""
        tag_ids = func.array_remove(func.array_agg(sleep_session_tags.c.tag_id), None)
        stmt = (
            select(
                SleepSession.id,
                SleepSession.night_of,
                SleepSession.started_at,
                SleepSession.ended_at,
                SleepSession.duration_minutes,
                SleepSession.quality,
                SleepSession.mood_on_wake,
                SleepSession.sleep_latency_minutes,
                SleepSession.awakenings,
                SleepSession.score,
                tag_ids.label("tag_ids"),
            )
            .outerjoin(sleep_session_tags, sleep_session_tags.c.session_id == SleepSession.id)
            .where(
                SleepSession.user_id == user_id,
                SleepSession.status == SleepStatus.COMPLETED,
                SleepSession.night_of >= date_from,
                SleepSession.night_of <= date_to,
            )
            .group_by(SleepSession.id)
            .order_by(SleepSession.started_at)
        )
        rows = await self.session.execute(stmt)
        return [
            SessionPoint(
                id=row.id,
                night_of=row.night_of,
                started_at=row.started_at,
                ended_at=row.ended_at,
                duration_minutes=row.duration_minutes,
                quality=row.quality,
                mood_on_wake=row.mood_on_wake,
                sleep_latency_minutes=row.sleep_latency_minutes,
                awakenings=row.awakenings,
                score=row.score,
                tag_ids=frozenset(row.tag_ids or ()),
            )
            for row in rows
        ]

    async def list_all(self, user_id: uuid.UUID) -> list[SleepSession]:
        stmt = (
            select(SleepSession)
            .where(SleepSession.user_id == user_id)
            .order_by(SleepSession.started_at)
        )
        return list(await self.session.scalars(stmt))
