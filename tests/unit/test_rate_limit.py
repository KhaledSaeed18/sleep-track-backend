from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import fakeredis
import httpx
import pytest
from fastapi import FastAPI
from redis.exceptions import RedisError
from starlette.requests import Request

from app.core import rate_limit as rate_limit_module
from app.core.config import Settings
from app.core.errors import RateLimitedError, register_exception_handlers
from app.core.rate_limit import (
    RateLimit,
    RateLimiter,
    RateLimitResult,
    client_ip,
    get_rate_limiter,
    rate_limit,
)

WINDOW_START = 1_800_000_000.0  # an exact multiple of 60 seconds


class FakeClock:
    def __init__(self, now: float = WINDOW_START + 1) -> None:
        self.now = now

    def time(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(rate_limit_module, "time", SimpleNamespace(time=fake.time))
    return fake


def make_settings(*, enabled: bool = True) -> Settings:
    return Settings(_env_file=None, rate_limit_enabled=enabled)


@pytest.fixture
def limiter(redis: fakeredis.FakeAsyncRedis) -> RateLimiter:
    return RateLimiter(redis, make_settings())


# =============================================================================== parse
@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("10/minute", RateLimit(10, 60)),
        ("5/hour", RateLimit(5, 3600)),
        ("1/second", RateLimit(1, 1)),
        ("100/day", RateLimit(100, 86400)),
        (" 3 / minute ", RateLimit(3, 60)),
    ],
)
def test_parse(spec: str, expected: RateLimit) -> None:
    assert RateLimit.parse(spec) == expected


@pytest.mark.parametrize(("spec", "error"), [("10/week", KeyError), ("ten/minute", ValueError)])
def test_parse_invalid(spec: str, error: type[Exception]) -> None:
    with pytest.raises(error):
        RateLimit.parse(spec)


# =============================================================================== sliding window
class TestSlidingWindow:
    @pytest.mark.usefixtures("clock")
    async def test_allows_up_to_limit_then_blocks(self, limiter: RateLimiter) -> None:
        rule = RateLimit(3, 60)
        results = [await limiter.hit("login:ip:1.2.3.4", rule) for _ in range(4)]

        assert [r.allowed for r in results] == [True, True, True, False]
        assert [r.remaining for r in results] == [2, 1, 0, 0]
        assert results[-1].retry_after == 59
        assert all(1 <= r.retry_after <= 60 for r in results)

    @pytest.mark.usefixtures("clock")
    async def test_keys_are_independent(self, limiter: RateLimiter) -> None:
        rule = RateLimit(1, 60)
        assert (await limiter.hit("a", rule)).allowed
        assert not (await limiter.hit("a", rule)).allowed
        assert (await limiter.hit("b", rule)).allowed

    async def test_previous_window_is_weighted_by_elapsed_time(
        self, limiter: RateLimiter, clock: FakeClock
    ) -> None:
        rule = RateLimit(4, 60)
        for _ in range(4):
            assert (await limiter.hit("k", rule)).allowed
        assert not (await limiter.hit("k", rule)).allowed

        # Halfway through the next window the previous four hits count as two.
        clock.now = WINDOW_START + 60 + 30
        results = [await limiter.hit("k", rule) for _ in range(3)]
        assert [r.allowed for r in results] == [True, True, False]
        assert [r.remaining for r in results] == [1, 0, 0]
        assert results[-1].retry_after == 30

        # Two windows later the old counts no longer matter.
        clock.now = WINDOW_START + 180
        assert (await limiter.hit("k", rule)).remaining == 3

    async def test_retry_after_is_at_least_one_second(
        self, limiter: RateLimiter, clock: FakeClock
    ) -> None:
        clock.now = WINDOW_START + 59.9995
        rule = RateLimit(1, 60)
        await limiter.hit("edge", rule)
        assert (await limiter.hit("edge", rule)).retry_after == 1

    async def test_counter_expires_after_two_windows(
        self, limiter: RateLimiter, redis: fakeredis.FakeAsyncRedis, clock: FakeClock
    ) -> None:
        await limiter.hit("ttl", RateLimit(5, 60))
        bucket = int(clock.now * 1000) // 60_000
        key = f"rl:ttl:{bucket}"
        assert await redis.get(key) == "1"
        assert 0 < await redis.pttl(key) <= 120_000

    async def test_blocked_hits_are_not_counted(
        self, limiter: RateLimiter, redis: fakeredis.FakeAsyncRedis, clock: FakeClock
    ) -> None:
        rule = RateLimit(2, 60)
        for _ in range(5):
            await limiter.hit("count", rule)
        bucket = int(clock.now * 1000) // 60_000
        assert await redis.get(f"rl:count:{bucket}") == "2"

    @pytest.mark.usefixtures("clock")
    async def test_custom_prefix(self, redis: fakeredis.FakeAsyncRedis) -> None:
        limiter = RateLimiter(redis, make_settings(), prefix="custom")
        await limiter.hit("x", RateLimit(1, 60))
        assert [key async for key in redis.scan_iter("custom:x:*")]


class TestDegradedModes:
    async def test_disabled_settings_bypass_redis(self, redis: fakeredis.FakeAsyncRedis) -> None:
        limiter = RateLimiter(redis, make_settings(enabled=False))
        rule = RateLimit(1, 60)
        for _ in range(5):
            assert await limiter.hit("k", rule) == RateLimitResult(
                allowed=True, remaining=1, retry_after=0
            )
        assert await redis.dbsize() == 0

    async def test_redis_error_fails_open(
        self, limiter: RateLimiter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def failing_script(**_: Any) -> list[int]:
            raise RedisError("connection refused")

        monkeypatch.setattr(limiter, "_script", failing_script)
        assert await limiter.hit("k", RateLimit(1, 60)) == RateLimitResult(
            allowed=True, remaining=1, retry_after=0
        )

    async def test_unreachable_redis_fails_open(
        self, broken_redis: fakeredis.FakeAsyncRedis
    ) -> None:
        limiter = RateLimiter(broken_redis, make_settings())
        await limiter.enforce("k", RateLimit(1, 60))
        await limiter.enforce("k", RateLimit(1, 60))


# =============================================================================== enforce
async def test_enforce_raises_with_retry_after(limiter: RateLimiter, clock: FakeClock) -> None:
    clock.now = WINDOW_START + 15
    rule = RateLimit(1, 60)
    await limiter.enforce("k", rule)
    with pytest.raises(RateLimitedError) as caught:
        await limiter.enforce("k", rule)

    error = caught.value
    assert error.status_code == 429
    assert error.code == "rate_limited"
    assert error.retry_after == 45
    assert error.headers == {"Retry-After": "45"}
    assert error.detail == "Rate limit exceeded. Try again in 45 seconds."


# =============================================================================== dependency
def _request(client: tuple[str, int] | None, app: FastAPI | None = None) -> Request:
    scope: dict[str, Any] = {"type": "http", "headers": [], "client": client}
    if app is not None:
        scope["app"] = app
    return Request(scope)


def test_client_ip() -> None:
    assert client_ip(_request(("203.0.113.9", 5000))) == "203.0.113.9"
    assert client_ip(_request(None)) == "unknown"


def test_get_rate_limiter_reads_app_state(limiter: RateLimiter) -> None:
    app = FastAPI()
    app.state.rate_limiter = limiter
    assert get_rate_limiter(_request(None, app)) is limiter


async def test_rate_limit_dependency(limiter: RateLimiter, clock: FakeClock) -> None:
    app = FastAPI()
    app.state.rate_limiter = limiter
    register_exception_handlers(app)

    @app.post("/login", dependencies=[rate_limit("login", "2/minute")])
    async def login() -> dict[str, bool]:
        return {"ok": True}

    transport = httpx.ASGITransport(app=app, client=("198.51.100.7", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        statuses = [(await client.post("/login")).status_code for _ in range(2)]
        blocked = await client.post("/login")

    assert statuses == [200, 200]
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "59"
    assert blocked.headers["content-type"] == "application/problem+json"
    assert blocked.json()["code"] == "rate_limited"
