from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from tests.integration.conftest import API

pytestmark = pytest.mark.integration


async def test_liveness(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_reports_dependencies(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["redis"]["status"] == "ok"


async def test_readiness_degraded_when_redis_fails(
    client: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken_ping() -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(app.state.redis, "ping", broken_ping)
    response = await client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"] == {
        "status": "error",
        "latency_ms": None,
        "error": "ConnectionError",
    }


async def test_meta_exposes_feature_flags(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{API}/meta")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "SleepTrack API"
    assert body["environment"] == "test"
    assert body["features"] == {
        "google_oauth": False,
        "demo_mode": True,
        "email_verification_required": False,
        "registration_open": True,
    }


async def test_metrics_endpoint(client: httpx.AsyncClient) -> None:
    await client.get(f"{API}/meta")
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text


async def test_openapi_document(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{API}/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    operation_ids = {op["operationId"] for path in schema["paths"].values() for op in path.values()}
    assert {"auth_login", "sleep_list_sessions", "analytics_get_overview"} <= operation_ids
    login_errors = schema["paths"][f"{API}/auth/login"]["post"]["responses"]["401"]
    assert "application/problem+json" in login_errors["content"]
    assert "BearerAuth" in schema["components"]["securitySchemes"]


async def test_request_id_is_generated_and_echoed(client: httpx.AsyncClient) -> None:
    generated = await client.get("/health/live")
    assert len(generated.headers["X-Request-ID"]) == 32
    echoed = await client.get("/health/live", headers={"X-Request-ID": "trace-123"})
    assert echoed.headers["X-Request-ID"] == "trace-123"
    rejected = await client.get("/health/live", headers={"X-Request-ID": "bad id with spaces"})
    assert rejected.headers["X-Request-ID"] != "bad id with spaces"


async def test_security_headers(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{API}/meta")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in response.headers
    docs = await client.get(f"{API}/docs")
    assert "cdn.jsdelivr.net" in docs.headers["Content-Security-Policy"]


async def test_cors_preflight_allows_configured_origin(client: httpx.AsyncClient) -> None:
    response = await client.options(
        f"{API}/auth/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"

    denied = await client.options(
        f"{API}/auth/login",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in denied.headers


async def test_unknown_route_returns_problem(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{API}/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "not_found"
    assert body["instance"] == f"{API}/does-not-exist"
    assert body["request_id"] == response.headers["X-Request-ID"]


async def test_method_not_allowed_problem(client: httpx.AsyncClient) -> None:
    response = await client.put(f"{API}/meta")
    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"


async def test_validation_error_shape(client: httpx.AsyncClient) -> None:
    response = await client.post(f"{API}/auth/login", json={"email": "not-an-email"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["type"] == "https://sleeptrack.app/problems/validation-error"
    fields = {error["field"] for error in body["errors"]}
    assert fields == {"email", "password"}


async def test_body_size_limit(client: httpx.AsyncClient, app: FastAPI) -> None:
    limit = app.state.settings.max_request_body_bytes
    response = await client.post(
        f"{API}/auth/login",
        content=b"x" * (limit + 1),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


async def test_streamed_body_over_limit_is_rejected(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    limit = app.state.settings.max_request_body_bytes

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(3):
            yield b"x" * (limit // 2 + 1)

    response = await client.post(
        f"{API}/auth/login", content=chunks(), headers={"content-type": "application/json"}
    )
    assert response.status_code == 413


async def test_unhandled_errors_do_not_leak(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.meta import router as meta_router

    def explode(*_: object, **__: object) -> None:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(meta_router, "FeatureFlags", explode)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        response = await http.get(f"{API}/meta")
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "internal_error"
    assert "secret" not in response.text
    assert response.headers["X-Request-ID"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
