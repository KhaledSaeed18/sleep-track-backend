"""ARQ worker configuration: ``arq app.worker.settings.WorkerSettings``."""

from __future__ import annotations

from typing import Any, ClassVar

import structlog
from arq import cron
from arq.connections import RedisSettings
from arq.cron import CronJob

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.observability import setup_sentry
from app.core.redis import create_redis
from app.db.session import create_engine, create_session_factory
from app.integrations.email.providers import build_email_provider
from app.integrations.email.renderer import EmailRenderer, EmailSender
from app.worker import tasks
from app.worker.context import RESOURCES_KEY, JobResources, resources

logger = structlog.get_logger(__name__)
_settings = get_settings()


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging(_settings)
    setup_sentry(_settings)
    engine = create_engine(_settings)
    ctx[RESOURCES_KEY] = JobResources(
        settings=_settings,
        engine=engine,
        session_factory=create_session_factory(engine),
        redis=create_redis(_settings.redis_url),
        email=EmailSender(build_email_provider(_settings), EmailRenderer(_settings)),
    )
    logger.info("worker_started", environment=_settings.environment.value)


async def shutdown(ctx: dict[str, Any]) -> None:
    res = resources(ctx)
    await res.redis.aclose()
    await res.engine.dispose()
    logger.info("worker_stopped")


class WorkerSettings:
    functions: ClassVar[list[Any]] = list(tasks.JOB_FUNCTIONS.values())
    cron_jobs: ClassVar[list[CronJob]] = [
        cron(tasks.dispatch_weekly_reports, minute={0}, run_at_startup=False),
        cron(tasks.cleanup_demo_accounts, minute={15, 45}),
        cron(tasks.cleanup_expired_tokens, hour={3}, minute={30}),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_tries = 3
    job_timeout = 300
    keep_result = 3600
    health_check_interval = 30
