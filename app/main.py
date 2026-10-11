"""Application factory and lifespan management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.api import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.observability import instrument_engine, setup_metrics, setup_sentry, setup_tracing
from app.core.openapi import API_DESCRIPTION, OPENAPI_TAGS, generate_operation_id, install_openapi
from app.core.rate_limit import RateLimiter
from app.core.redis import create_redis
from app.core.security import TokenCodec, get_password_hasher
from app.db.session import create_engine, create_session_factory
from app.integrations.email.providers import build_email_provider
from app.integrations.email.renderer import EmailRenderer, EmailSender
from app.integrations.oauth.registry import build_oauth_registry
from app.modules.insights.providers import build_insight_provider
from app.modules.meta.router import health_router
from app.worker.context import JobResources
from app.worker.queue import ArqJobQueue, EagerJobQueue, JobQueue
from app.worker.tasks import JOB_FUNCTIONS

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = create_engine(settings)
    instrument_engine(engine, settings)
    redis = create_redis(settings.redis_url)
    session_factory = create_session_factory(engine)
    email_sender = EmailSender(build_email_provider(settings), EmailRenderer(settings))

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.redis = redis
    app.state.rate_limiter = RateLimiter(redis, settings)
    app.state.email_sender = email_sender

    arq_pool = None
    queue: JobQueue
    if settings.jobs_eager:
        queue = EagerJobQueue(
            JOB_FUNCTIONS,
            JobResources(
                settings=settings,
                engine=engine,
                session_factory=session_factory,
                redis=redis,
                email=email_sender,
            ),
        )
    else:
        arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        queue = ArqJobQueue(arq_pool)
    app.state.job_queue = queue

    logger.info("startup", environment=settings.environment.value, version=settings.version)
    try:
        yield
    finally:
        if arq_pool is not None:
            await arq_pool.aclose()
        await redis.aclose()
        await engine.dispose()
        logger.info("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    setup_sentry(settings)

    prefix = settings.api_prefix
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        summary="Sleep logging, analytics and insights.",
        description=API_DESCRIPTION,
        openapi_url=f"{prefix}/openapi.json",
        docs_url=f"{prefix}/docs",
        redoc_url=f"{prefix}/redoc",
        openapi_tags=OPENAPI_TAGS,
        license_info={"name": "MIT", "identifier": "MIT"},
        contact={"name": "Khaled Saeed", "url": "https://github.com/KhaledSaeed18"},
        generate_unique_id_function=generate_operation_id,
        lifespan=lifespan,
        debug=settings.debug,
    )
    app.state.settings = settings
    app.state.token_codec = TokenCodec(settings)
    app.state.password_hasher = get_password_hasher(settings)
    app.state.oauth = build_oauth_registry(settings)
    app.state.insight_provider = build_insight_provider()

    register_exception_handlers(app)
    install_openapi(app)

    app.include_router(health_router)
    app.include_router(api_router, prefix=prefix)
    setup_metrics(app, settings)
    setup_tracing(app, settings)

    # Middleware executes in reverse order of registration (last added = outermost).
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After", "Content-Disposition"],
        max_age=600,
    )
    if settings.is_production and "*" not in settings.allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(RequestContextMiddleware, quiet_paths=("/health/live", "/metrics"))
    # Outermost, so even responses produced by the middleware above get the headers.
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts=settings.secure_cookies,
        docs_prefixes=(f"{prefix}/docs", f"{prefix}/redoc"),
    )
    return app
