"""Redis client factory and FastAPI dependency."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis

RedisClient = Redis


def create_redis(url: str) -> Redis:
    client: Redis = Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30,
    )
    return client


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


RedisDep = Annotated[Redis, Depends(get_redis)]
