"""Redis-backed rate limiting using a sliding-window counter.

The sliding window blends the current and previous fixed windows weighted by
elapsed time, which smooths the burst-at-boundary problem of plain fixed
windows while needing just two counters per key.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Annotated, Any

import structlog
from fastapi import Depends, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.errors import RateLimitedError

logger = structlog.get_logger(__name__)

# KEYS[1] current window, KEYS[2] previous window; ARGV: limit, window_ms, elapsed_ms
_SLIDING_WINDOW_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local previous = tonumber(redis.call('GET', KEYS[2]) or '0')
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local elapsed = tonumber(ARGV[3])
local weighted = previous * ((window - elapsed) / window) + current
if weighted >= limit then
  return {0, math.floor(weighted)}
end
current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('PEXPIRE', KEYS[1], window * 2)
end
return {1, math.floor(weighted) + 1}
"""


@dataclass(frozen=True, slots=True)
class RateLimit:
    limit: int
    window_seconds: int

    @classmethod
    def parse(cls, spec: str) -> RateLimit:
        """Parse specs like ``"10/minute"`` or ``"5/hour"``."""
        amount, _, unit = spec.partition("/")
        seconds = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}[unit.strip()]
        return cls(int(amount), seconds)


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after: int


class RateLimiter:
    def __init__(self, redis: Redis, settings: Settings, *, prefix: str = "rl") -> None:
        self._redis = redis
        self._settings = settings
        self._prefix = prefix
        self._script = redis.register_script(_SLIDING_WINDOW_LUA)

    async def hit(self, key: str, rule: RateLimit) -> RateLimitResult:
        if not self._settings.rate_limit_enabled:
            return RateLimitResult(allowed=True, remaining=rule.limit, retry_after=0)
        window_ms = rule.window_seconds * 1000
        now_ms = int(time.time() * 1000)
        bucket = now_ms // window_ms
        elapsed = now_ms - bucket * window_ms
        keys = [f"{self._prefix}:{key}:{bucket}", f"{self._prefix}:{key}:{bucket - 1}"]
        try:
            allowed, count = await self._script(keys=keys, args=[rule.limit, window_ms, elapsed])
        except RedisError:
            # Fail open: an unavailable Redis should degrade protection, not
            # take authentication down with it.
            logger.warning("rate_limiter_unavailable", key=key)
            return RateLimitResult(allowed=True, remaining=rule.limit, retry_after=0)
        retry_after = max(1, math.ceil((window_ms - elapsed) / 1000))
        return RateLimitResult(
            allowed=bool(allowed),
            remaining=max(0, rule.limit - int(count)),
            retry_after=retry_after,
        )

    async def enforce(self, key: str, rule: RateLimit) -> None:
        result = await self.hit(key, rule)
        if not result.allowed:
            logger.info("rate_limited", key=key)
            raise RateLimitedError(result.retry_after)


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def client_ip(request: Request) -> str:
    # Uvicorn's proxy-headers support rewrites request.client from
    # X-Forwarded-For for trusted proxies (FORWARDED_ALLOW_IPS).
    return request.client.host if request.client else "unknown"


def rate_limit(name: str, spec: str) -> Any:
    """Dependency factory enforcing a per-IP limit for a route."""
    rule = RateLimit.parse(spec)

    async def dependency(request: Request, limiter: RateLimiterDep) -> None:
        await limiter.enforce(f"{name}:ip:{client_ip(request)}", rule)

    return Depends(dependency)
