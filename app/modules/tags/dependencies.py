from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.db.session import DbSession
from app.modules.analytics.cache import AnalyticsCacheDep
from app.modules.tags.service import TagService


def get_tag_service(session: DbSession, cache: AnalyticsCacheDep) -> TagService:
    return TagService(session, cache)


TagServiceDep = Annotated[TagService, Depends(get_tag_service)]
