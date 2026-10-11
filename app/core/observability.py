"""Optional observability integrations, each enabled by configuration.

Sentry and OpenTelemetry live in the ``observability`` extra; when the flags are
off (the default) nothing is imported, keeping the core image lean.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings

logger = structlog.get_logger(__name__)

UNMETERED_PATHS = ("/metrics", "/health/live", "/health/ready")


def setup_metrics(app: FastAPI, settings: Settings) -> None:
    if not settings.metrics_enabled:
        return
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=list(UNMETERED_PATHS),
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False, tags=["health"])


def setup_sentry(settings: Settings) -> None:
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk  # noqa: PLC0415 - optional dependency
    except ImportError:
        logger.warning("sentry_unavailable", reason="sentry-sdk not installed")
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.environment.value,
        release=f"sleeptrack-api@{settings.version}",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
    )
    logger.info("sentry_enabled")


def setup_tracing(app: FastAPI, settings: Settings) -> None:
    """Configure the tracer provider and instrument the ASGI app.

    Must run before the app starts: middleware cannot be added afterwards.
    """
    if not settings.otel_enabled:
        return
    try:
        from opentelemetry import trace  # noqa: PLC0415 - optional dependency
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import (  # noqa: PLC0415
            FastAPIInstrumentor,
        )
        from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
        from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415
    except ImportError:
        logger.warning("otel_unavailable", reason="opentelemetry packages not installed")
        return

    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": settings.otel_service_name, "service.version": settings.version}
        )
    )
    exporter = (
        OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces")
        if settings.otel_exporter_otlp_endpoint
        else OTLPSpanExporter()
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls=",".join(UNMETERED_PATHS))
    logger.info("otel_enabled")


def instrument_engine(engine: AsyncEngine, settings: Settings) -> None:
    if not settings.otel_enabled:
        return
    try:
        from opentelemetry.instrumentation.sqlalchemy import (  # noqa: PLC0415
            SQLAlchemyInstrumentor,
        )
    except ImportError:
        return
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
