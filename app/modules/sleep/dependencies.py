from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import Depends, Query

from app.core.errors import ValidationFailedError
from app.db.session import DbSession
from app.modules.analytics.cache import AnalyticsCacheDep
from app.modules.sleep.models import SleepStatus
from app.modules.sleep.repository import SessionFilters
from app.modules.sleep.service import SleepService
from app.modules.tags.dependencies import TagServiceDep


def get_sleep_service(
    session: DbSession, tags: TagServiceDep, cache: AnalyticsCacheDep
) -> SleepService:
    return SleepService(session, tags, cache)


SleepServiceDep = Annotated[SleepService, Depends(get_sleep_service)]


def _parse_tag_ids(raw: str | None) -> list[uuid.UUID]:
    if not raw:
        return []
    try:
        ids = [uuid.UUID(part.strip()) for part in raw.split(",") if part.strip()]
    except ValueError as exc:
        raise ValidationFailedError.for_field(
            "tag_ids", "must be a comma-separated list of UUIDs"
        ) from exc
    if len(ids) > 20:
        raise ValidationFailedError.for_field("tag_ids", "at most 20 tags can be combined")
    return ids


def session_filters(
    date_from: Annotated[
        date | None, Query(alias="from", description="Earliest `night_of` (inclusive)")
    ] = None,
    date_to: Annotated[
        date | None, Query(alias="to", description="Latest `night_of` (inclusive)")
    ] = None,
    min_quality: Annotated[int | None, Query(ge=1, le=5)] = None,
    max_quality: Annotated[int | None, Query(ge=1, le=5)] = None,
    tag_ids: Annotated[
        str | None,
        Query(description="Comma-separated tag ids; matches sessions with any of them"),
    ] = None,
    q: Annotated[
        str | None, Query(min_length=1, max_length=100, description="Search in notes")
    ] = None,
    status: Annotated[SleepStatus | None, Query()] = None,
) -> SessionFilters:
    if date_from and date_to and date_from > date_to:
        raise ValidationFailedError.for_field("from", "must be on or before 'to'")
    if min_quality and max_quality and min_quality > max_quality:
        raise ValidationFailedError.for_field("min_quality", "must not exceed max_quality")
    return SessionFilters(
        date_from=date_from,
        date_to=date_to,
        min_quality=min_quality,
        max_quality=max_quality,
        tag_ids=_parse_tag_ids(tag_ids),
        query=q.strip() if q else None,
        status=status,
    )


SessionFiltersDep = Annotated[SessionFilters, Depends(session_filters)]
