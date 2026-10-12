"""Per-user analytics cache with version-based invalidation.

Every write that can change a user's analytics bumps a per-user version
counter; cache keys embed that version, so stale entries simply stop being
read and expire on their own. This avoids scanning for keys to delete.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
from fastapi import Depends, Request
from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)

_VERSION_TTL_SECONDS = 7 * 86400


class AnalyticsCache:
    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    @staticmethod
    def _version_key(user_id: uuid.UUID) -> str:
        return f"cache:analytics:{user_id}:version"

    async def invalidate(self, user_id: uuid.UUID) -> None:
        try:
            key = self._version_key(user_id)
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, _VERSION_TTL_SECONDS)
                await pipe.execute()
        except RedisError:
            logger.warning("analytics_cache_invalidate_failed", user_id=str(user_id))

    async def get_or_compute[T](
        self,
        user_id: uuid.UUID,
        name: str,
        params: tuple[object, ...],
        adapter: TypeAdapter[T],
        compute: Callable[[], Awaitable[T]],
    ) -> T:
        if self._ttl <= 0:
            return await compute()
        try:
            version = await self._redis.get(self._version_key(user_id)) or "0"
            key = f"cache:analytics:{user_id}:{version}:{name}:" + ":".join(map(str, params))
            cached = await self._redis.get(key)
        except RedisError:
            logger.warning("analytics_cache_unavailable", user_id=str(user_id))
            return await compute()
        if cached is not None:
            return adapter.validate_json(cached)
        value = await compute()
        try:
            await self._redis.set(key, adapter.dump_json(value), ex=self._ttl)
        except RedisError:
            logger.warning("analytics_cache_store_failed", user_id=str(user_id))
        return value


def get_analytics_cache(request: Request) -> AnalyticsCache:
    return AnalyticsCache(
        request.app.state.redis, request.app.state.settings.analytics_cache_ttl_seconds
    )


AnalyticsCacheDep = Annotated[AnalyticsCache, Depends(get_analytics_cache)]
