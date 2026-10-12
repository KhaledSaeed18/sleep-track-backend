from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from app.core.deps import SettingsDep
from app.modules.meta.schemas import (
    FeatureFlags,
    HealthStatus,
    MetaResponse,
    ReadinessCheck,
    ReadinessStatus,
)

router = APIRouter(tags=["meta"])
health_router = APIRouter(prefix="/health", tags=["health"])

READINESS_TIMEOUT_SECONDS = 2.0


@router.get("/meta", summary="API metadata and feature flags")
async def get_meta(settings: SettingsDep) -> MetaResponse:
    return MetaResponse(
        name=settings.app_name,
        version=settings.version,
        environment=settings.environment.value,
        features=FeatureFlags(
            google_oauth=settings.google_oauth_enabled,
            demo_mode=settings.demo_mode_enabled,
            email_verification_required=settings.email_verification_required,
            registration_open=settings.registration_open,
        ),
    )


@health_router.get("/live", summary="Liveness probe")
async def live() -> HealthStatus:
    return HealthStatus(status="ok")


async def _timed(check: Callable[[], Awaitable[Any]]) -> ReadinessCheck:
    start = time.perf_counter()
    try:
        await asyncio.wait_for(check(), timeout=READINESS_TIMEOUT_SECONDS)
    except Exception as exc:
        return ReadinessCheck(status="error", error=type(exc).__name__)
    return ReadinessCheck(status="ok", latency_ms=round((time.perf_counter() - start) * 1000, 2))


@health_router.get(
    "/ready",
    summary="Readiness probe (database and Redis)",
    responses={503: {"model": ReadinessStatus, "description": "A dependency is unavailable"}},
)
async def ready(request: Request, response: Response) -> ReadinessStatus:
    async def database() -> None:
        async with request.app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def redis() -> None:
        await request.app.state.redis.ping()

    db_check, redis_check = await asyncio.gather(_timed(database), _timed(redis))
    checks = {"database": db_check, "redis": redis_check}
    healthy = all(check.status == "ok" for check in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessStatus(status="ok" if healthy else "degraded", checks=checks)
