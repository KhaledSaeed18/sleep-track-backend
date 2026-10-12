from __future__ import annotations

import sys
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import observability
from app.core.config import Settings


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


def _paths(app: FastAPI) -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def test_metrics_route_is_optional() -> None:
    enabled = FastAPI()
    observability.setup_metrics(enabled, _settings(metrics_enabled=True))
    assert "/metrics" in _paths(enabled)

    disabled = FastAPI()
    observability.setup_metrics(disabled, _settings(metrics_enabled=False))
    assert "/metrics" not in _paths(disabled)


def test_sentry_disabled_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    import sentry_sdk

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
    observability.setup_sentry(_settings())
    assert calls == []


def test_sentry_initialised_with_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    import sentry_sdk

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
    observability.setup_sentry(
        _settings(
            sentry_dsn=SecretStr("https://key@sentry.example/1"), sentry_traces_sample_rate=0.2
        )
    )
    assert len(calls) == 1
    assert calls[0]["dsn"] == "https://key@sentry.example/1"
    assert calls[0]["traces_sample_rate"] == 0.2
    assert calls[0]["send_default_pii"] is False
    assert calls[0]["release"].startswith("sleeptrack-api@")


def test_sentry_missing_package_is_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    observability.setup_sentry(_settings(sentry_dsn=SecretStr("https://key@sentry.example/1")))


def test_tracing_disabled_by_default() -> None:
    app = FastAPI()
    observability.setup_tracing(app, _settings())
    assert not getattr(app, "_is_instrumented_by_opentelemetry", False)


def test_tracing_instruments_app(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry import trace

    providers: list[object] = []
    monkeypatch.setattr(trace, "set_tracer_provider", providers.append)
    app = FastAPI()
    observability.setup_tracing(
        app,
        _settings(otel_enabled=True, otel_exporter_otlp_endpoint="http://collector:4318"),
    )
    assert len(providers) == 1
    assert getattr(app, "_is_instrumented_by_opentelemetry", False)
    providers[0].shutdown()  # type: ignore[attr-defined]


def test_tracing_missing_packages_is_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "opentelemetry.instrumentation.fastapi", None)
    observability.setup_tracing(FastAPI(), _settings(otel_enabled=True))


async def test_engine_instrumentation_follows_flag() -> None:
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    engine = create_async_engine("postgresql+asyncpg://user:pass@localhost:1/none")
    observability.instrument_engine(engine, _settings(otel_enabled=False))
    assert not SQLAlchemyInstrumentor().is_instrumented_by_opentelemetry
    observability.instrument_engine(engine, _settings(otel_enabled=True))
    assert SQLAlchemyInstrumentor().is_instrumented_by_opentelemetry
    SQLAlchemyInstrumentor().uninstrument()
    await engine.dispose()


def test_engine_instrumentation_missing_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "opentelemetry.instrumentation.sqlalchemy", None)
    engine = create_async_engine("postgresql+asyncpg://user:pass@localhost:1/none")
    observability.instrument_engine(engine, _settings(otel_enabled=True))
