from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any

import fakeredis
import pytest
from fastapi import FastAPI
from pydantic import BaseModel, TypeAdapter
from redis.exceptions import RedisError
from starlette.requests import Request

from app.modules.analytics.cache import AnalyticsCache, get_analytics_cache


class Summary(BaseModel):
    nights: int
    start: date


ADAPTER = TypeAdapter(Summary)
PARAMS: tuple[object, ...] = (date(2026, 3, 1), date(2026, 3, 7), "UTC")


class Counter:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> Summary:
        self.calls += 1
        return Summary(nights=self.calls, start=date(2026, 3, 1))


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


class TestGetOrCompute:
    async def test_hit_avoids_recompute(
        self, redis: fakeredis.FakeAsyncRedis, user_id: uuid.UUID
    ) -> None:
        cache = AnalyticsCache(redis, ttl_seconds=60)
        compute = Counter()

        first = await cache.get_or_compute(user_id, "overview", PARAMS, ADAPTER, compute)
        second = await cache.get_or_compute(user_id, "overview", PARAMS, ADAPTER, compute)

        assert compute.calls == 1
        assert first == second == Summary(nights=1, start=date(2026, 3, 1))

    async def test_entry_layout_and_ttl(
        self, redis: fakeredis.FakeAsyncRedis, user_id: uuid.UUID
    ) -> None:
        cache = AnalyticsCache(redis, ttl_seconds=60)
        await cache.get_or_compute(user_id, "overview", PARAMS, ADAPTER, Counter())

        key = f"cache:analytics:{user_id}:0:overview:2026-03-01:2026-03-07:UTC"
        assert await redis.get(key) == '{"nights":1,"start":"2026-03-01"}'
        assert 0 < await redis.ttl(key) <= 60

    async def test_name_params_and_user_are_part_of_the_key(
        self, redis: fakeredis.FakeAsyncRedis, user_id: uuid.UUID
    ) -> None:
        cache = AnalyticsCache(redis, ttl_seconds=60)
        compute = Counter()
        await cache.get_or_compute(user_id, "overview", PARAMS, ADAPTER, compute)
        await cache.get_or_compute(user_id, "daily", PARAMS, ADAPTER, compute)
        await cache.get_or_compute(
            user_id, "overview", (*PARAMS[:2], "Asia/Tokyo"), ADAPTER, compute
        )
        await cache.get_or_compute(uuid.uuid4(), "overview", PARAMS, ADAPTER, compute)
        assert compute.calls == 4

    async def test_invalidate_bumps_version(
        self, redis: fakeredis.FakeAsyncRedis, user_id: uuid.UUID
    ) -> None:
        cache = AnalyticsCache(redis, ttl_seconds=60)
        compute = Counter()
        other_user = uuid.uuid4()
        await cache.get_or_compute(user_id, "overview", PARAMS, ADAPTER, compute)
        await cache.get_or_compute(other_user, "overview", PARAMS, ADAPTER, compute)

        await cache.invalidate(user_id)
        refreshed = await cache.get_or_compute(user_id, "overview", PARAMS, ADAPTER, compute)
        untouched = await cache.get_or_compute(other_user, "overview", PARAMS, ADAPTER, compute)

        assert refreshed.nights == 3
        assert untouched.nights == 2
        assert compute.calls == 3
        version_key = f"cache:analytics:{user_id}:version"
        assert await redis.get(version_key) == "1"
        assert 0 < await redis.ttl(version_key) <= 7 * 86400

    async def test_repeated_invalidation(
        self, redis: fakeredis.FakeAsyncRedis, user_id: uuid.UUID
    ) -> None:
        cache = AnalyticsCache(redis, ttl_seconds=60)
        for _ in range(3):
            await cache.invalidate(user_id)
        assert await redis.get(f"cache:analytics:{user_id}:version") == "3"

    @pytest.mark.parametrize("ttl", [0, -1])
    async def test_non_positive_ttl_bypasses_cache(
        self, redis: fakeredis.FakeAsyncRedis, user_id: uuid.UUID, ttl: int
    ) -> None:
        cache = AnalyticsCache(redis, ttl_seconds=ttl)
        compute = Counter()
        for _ in range(3):
            await cache.get_or_compute(user_id, "overview", PARAMS, ADAPTER, compute)
        assert compute.calls == 3
        assert await redis.dbsize() == 0

    async def test_list_values_roundtrip(
        self, redis: fakeredis.FakeAsyncRedis, user_id: uuid.UUID
    ) -> None:
        cache = AnalyticsCache(redis, ttl_seconds=60)
        adapter = TypeAdapter(list[Summary])
        value = [
            Summary(nights=1, start=date(2026, 1, 1)),
            Summary(nights=2, start=date(2026, 1, 2)),
        ]

        async def compute() -> list[Summary]:
            return value

        await cache.get_or_compute(user_id, "weekday", (), adapter, compute)
        cached = await cache.get_or_compute(user_id, "weekday", (), adapter, compute)
        assert cached == value


class TestRedisFailures:
    async def test_read_failure_computes(
        self, broken_redis: fakeredis.FakeAsyncRedis, user_id: uuid.UUID
    ) -> None:
        cache = AnalyticsCache(broken_redis, ttl_seconds=60)
        compute = Counter()
        await cache.get_or_compute(user_id, "overview", PARAMS, ADAPTER, compute)
        result = await cache.get_or_compute(user_id, "overview", PARAMS, ADAPTER, compute)
        assert result.nights == 2

    async def test_store_failure_still_returns_value(
        self,
        redis: fakeredis.FakeAsyncRedis,
        user_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def failing_set(*_: Any, **__: Any) -> None:
            raise RedisError("read-only replica")

        monkeypatch.setattr(redis, "set", failing_set)
        cache = AnalyticsCache(redis, ttl_seconds=60)
        result = await cache.get_or_compute(user_id, "overview", PARAMS, ADAPTER, Counter())
        assert result.nights == 1

    async def test_invalidate_failure_is_swallowed(
        self, broken_redis: fakeredis.FakeAsyncRedis, user_id: uuid.UUID
    ) -> None:
        await AnalyticsCache(broken_redis, ttl_seconds=60).invalidate(user_id)

    async def test_compute_errors_propagate(
        self, redis: fakeredis.FakeAsyncRedis, user_id: uuid.UUID
    ) -> None:
        async def failing() -> Summary:
            raise LookupError("boom")

        cache = AnalyticsCache(redis, ttl_seconds=60)
        with pytest.raises(LookupError):
            await cache.get_or_compute(user_id, "overview", PARAMS, ADAPTER, failing)
        assert await redis.dbsize() == 0


async def test_dependency_uses_app_state(redis: fakeredis.FakeAsyncRedis) -> None:
    app = FastAPI()
    app.state.redis = redis
    app.state.settings = SimpleNamespace(analytics_cache_ttl_seconds=0)
    cache = get_analytics_cache(Request({"type": "http", "app": app, "headers": []}))

    compute = Counter()
    await cache.get_or_compute(uuid.uuid4(), "overview", PARAMS, ADAPTER, compute)
    await cache.get_or_compute(uuid.uuid4(), "overview", PARAMS, ADAPTER, compute)
    assert compute.calls == 2
