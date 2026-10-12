from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import middleware
from app.core.context import get_request_id
from app.core.errors import register_exception_handlers
from app.core.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)

MAX_BYTES = 1024


class RecordingLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, event: str, **fields: Any) -> None:
        self.records.append(("info", event, fields))

    def exception(self, event: str, **fields: Any) -> None:
        self.records.append(("exception", event, fields))


@pytest.fixture
def access_log(monkeypatch: pytest.MonkeyPatch) -> RecordingLogger:
    recorder = RecordingLogger()
    monkeypatch.setattr(middleware, "access_logger", recorder)
    return recorder


@pytest.fixture
def error_log(monkeypatch: pytest.MonkeyPatch) -> RecordingLogger:
    recorder = RecordingLogger()
    monkeypatch.setattr(middleware, "error_logger", recorder)
    return recorder


def client_for(app: ASGIApp, *, raise_errors: bool = True) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_errors)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def call_asgi(app: ASGIApp, scope: Scope, chunks: list[bytes]) -> list[Message]:
    """Drive ``app`` directly, feeding the request body in ``chunks``."""
    pending = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]
    sent: list[Message] = []

    async def receive() -> Message:
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


def http_scope(path: str = "/", headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers or [],
        "query_string": b"",
        "client": ("127.0.0.1", 5000),
    }


async def read_body(receive: Receive) -> bytes:
    body = b""
    while True:
        message = await receive()
        chunk: bytes = message.get("body", b"")
        body += chunk
        if not message.get("more_body"):
            return body


async def echo_length_app(scope: Scope, receive: Receive, send: Send) -> None:
    body = await read_body(receive)
    await PlainTextResponse(str(len(body)))(scope, receive, send)


# =============================================================================== request context
def context_app(quiet_paths: tuple[str, ...] = ()) -> Starlette:
    """Registered like the real application: inside Starlette's error middleware."""

    async def whoami(request: Request) -> Response:
        return JSONResponse({"state": request.state.request_id, "context": get_request_id()})

    async def crash(request: Request) -> Response:
        raise RuntimeError("secret connection string")

    return Starlette(
        routes=[Route("/whoami", whoami), Route("/crash", crash)],
        middleware=[Middleware(RequestContextMiddleware, quiet_paths=quiet_paths)],
    )


class TestRequestContextMiddleware:
    @pytest.mark.usefixtures("access_log")
    async def test_generates_request_id(self) -> None:
        async with client_for(context_app()) as client:
            response = await client.get("/whoami")
        request_id = response.headers["x-request-id"]
        assert re.fullmatch(r"[0-9a-f]{32}", request_id)
        assert response.json() == {"state": request_id, "context": request_id}
        assert get_request_id() is None

    @pytest.mark.usefixtures("access_log")
    async def test_echoes_valid_incoming_id(self) -> None:
        async with client_for(context_app()) as client:
            response = await client.get("/whoami", headers={"X-Request-ID": "edge.req-42_A"})
        assert response.headers["x-request-id"] == "edge.req-42_A"
        assert response.json()["context"] == "edge.req-42_A"

    @pytest.mark.usefixtures("access_log")
    @pytest.mark.parametrize("incoming", ["has spaces", "x" * 129, "<script>", "idé"])
    async def test_replaces_unsafe_incoming_id(self, incoming: str) -> None:
        async with client_for(context_app()) as client:
            response = await client.get("/whoami", headers=[(b"x-request-id", incoming.encode())])
        assert response.headers["x-request-id"] != incoming
        assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])

    @pytest.mark.usefixtures("access_log")
    async def test_server_timing_header(self) -> None:
        async with client_for(context_app()) as client:
            response = await client.get("/whoami")
        assert re.fullmatch(r"app;dur=\d+\.\d", response.headers["server-timing"])

    async def test_access_log(self, access_log: RecordingLogger) -> None:
        app = context_app(quiet_paths=("/health/live",))
        async with client_for(app) as client:
            await client.get("/whoami")
            await client.get("/health/live")
        ((level, event, fields),) = access_log.records
        assert (level, event) == ("info", "request")
        assert fields["method"] == "GET"
        assert fields["path"] == "/whoami"
        assert fields["status"] == 200
        assert fields["client_ip"] == "127.0.0.1"
        assert fields["duration_ms"] >= 0

    async def test_unhandled_exception_becomes_problem(
        self, access_log: RecordingLogger, error_log: RecordingLogger
    ) -> None:
        async with client_for(context_app()) as client:
            response = await client.get("/crash", headers={"X-Request-ID": "trace-1"})

        assert response.status_code == 500
        assert response.headers["content-type"] == "application/problem+json"
        assert response.headers["x-request-id"] == "trace-1"
        assert response.json() == {
            "type": "https://sleeptrack.app/problems/internal-error",
            "title": "Internal server error",
            "status": 500,
            "detail": "An unexpected error occurred. Please try again later.",
            "instance": "/crash",
            "code": "internal_error",
            "request_id": "trace-1",
        }
        assert "secret" not in response.text
        assert [event for _, event, _ in error_log.records] == ["unhandled_exception"]
        assert access_log.records[0][2]["status"] == 500

    @pytest.mark.usefixtures("access_log", "error_log")
    async def test_error_after_response_started_is_reraised(self) -> None:
        async def streaming_failure(scope: Scope, receive: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            raise RuntimeError("stream broke")

        with pytest.raises(RuntimeError, match="stream broke"):
            await call_asgi(RequestContextMiddleware(streaming_failure), http_scope(), [b""])
        assert get_request_id() is None

    async def test_non_http_scopes_pass_through(self) -> None:
        seen: list[str] = []

        async def inner(scope: Scope, receive: Receive, send: Send) -> None:
            seen.append(scope["type"])

        await RequestContextMiddleware(inner)({"type": "lifespan"}, _never_receive, _never_send)
        assert seen == ["lifespan"]


async def _never_receive() -> Message:
    raise AssertionError("receive should not be called")


async def _never_send(message: Message) -> None:
    raise AssertionError("send should not be called")


# =============================================================================== security headers
def headers_app() -> Starlette:
    async def plain(request: Request) -> Response:
        return PlainTextResponse("ok")

    async def framed(request: Request) -> Response:
        return PlainTextResponse("ok", headers={"X-Frame-Options": "SAMEORIGIN"})

    return Starlette(
        routes=[Route("/plain", plain), Route("/framed", framed), Route("/api/v1/docs", plain)]
    )


class TestSecurityHeadersMiddleware:
    async def test_default_headers(self) -> None:
        async with client_for(SecurityHeadersMiddleware(headers_app(), hsts=False)) as client:
            response = await client.get("/plain")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert response.headers["cross-origin-opener-policy"] == "same-origin"
        assert response.headers["cross-origin-resource-policy"] == "same-site"
        assert response.headers["permissions-policy"] == (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        assert response.headers["content-security-policy"] == (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        )
        assert "strict-transport-security" not in response.headers

    async def test_hsts_when_enabled(self) -> None:
        async with client_for(SecurityHeadersMiddleware(headers_app(), hsts=True)) as client:
            response = await client.get("/plain")
        assert response.headers["strict-transport-security"] == (
            "max-age=63072000; includeSubDomains"
        )

    async def test_handler_headers_are_not_overwritten(self) -> None:
        async with client_for(SecurityHeadersMiddleware(headers_app(), hsts=False)) as client:
            response = await client.get("/framed")
        assert response.headers.get_list("x-frame-options") == ["SAMEORIGIN"]

    async def test_docs_get_a_relaxed_policy(self) -> None:
        app = SecurityHeadersMiddleware(headers_app(), hsts=False, docs_prefixes=("/api/v1/docs",))
        async with client_for(app) as client:
            docs = await client.get("/api/v1/docs")
            api = await client.get("/plain")
        assert "https://cdn.jsdelivr.net" in docs.headers["content-security-policy"]
        assert api.headers["content-security-policy"].startswith("default-src 'none'")

    async def test_error_responses_get_headers(self) -> None:
        async with client_for(SecurityHeadersMiddleware(headers_app(), hsts=True)) as client:
            response = await client.get("/missing")
        assert response.status_code == 404
        assert response.headers["x-content-type-options"] == "nosniff"

    async def test_non_http_scopes_pass_through(self) -> None:
        seen: list[str] = []

        async def inner(scope: Scope, receive: Receive, send: Send) -> None:
            seen.append(scope["type"])

        app = SecurityHeadersMiddleware(inner, hsts=True)
        await app({"type": "websocket"}, _never_receive, _never_send)
        assert seen == ["websocket"]


# =============================================================================== body size
async def chunked(total: int, size: int = 256) -> AsyncIterator[bytes]:
    sent = 0
    while sent < total:
        chunk = min(size, total - sent)
        sent += chunk
        yield b"x" * chunk


class TestBodySizeLimitMiddleware:
    async def test_small_body_passes(self) -> None:
        async with client_for(BodySizeLimitMiddleware(echo_length_app, max_bytes=MAX_BYTES)) as c:
            response = await c.post("/upload", content=b"x" * MAX_BYTES)
        assert response.status_code == 200
        assert response.text == str(MAX_BYTES)

    async def test_content_length_checked_up_front(self) -> None:
        called = False

        async def inner(scope: Scope, receive: Receive, send: Send) -> None:
            nonlocal called
            called = True

        async with client_for(BodySizeLimitMiddleware(inner, max_bytes=MAX_BYTES)) as client:
            response = await client.post("/upload", content=b"x" * (MAX_BYTES + 1))

        assert not called
        assert response.status_code == 413
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json() == {
            "type": "https://sleeptrack.app/problems/payload-too-large",
            "title": "Payload too large",
            "status": 413,
            "detail": f"Request body exceeds the {MAX_BYTES} byte limit.",
            "instance": "/upload",
            "code": "payload_too_large",
        }

    async def test_streamed_body_is_counted(self) -> None:
        app = BodySizeLimitMiddleware(echo_length_app, max_bytes=MAX_BYTES)
        async with client_for(app) as client:
            ok = await client.post("/upload", content=chunked(MAX_BYTES))
            too_large = await client.post("/upload", content=chunked(MAX_BYTES * 3))
        assert "content-length" not in ok.request.headers
        assert ok.text == str(MAX_BYTES)
        assert too_large.status_code == 413
        assert too_large.json()["code"] == "payload_too_large"

    async def test_lying_content_length_is_caught_while_streaming(self) -> None:
        app = BodySizeLimitMiddleware(echo_length_app, max_bytes=MAX_BYTES)
        scope = http_scope("/upload", headers=[(b"content-length", b"10")])
        messages = await call_asgi(app, scope, [b"x" * 600, b"x" * 600])
        assert messages[0]["status"] == 413

    async def test_non_numeric_content_length_is_ignored(self) -> None:
        app = BodySizeLimitMiddleware(echo_length_app, max_bytes=MAX_BYTES)
        scope = http_scope("/upload", headers=[(b"content-length", b"lots")])
        messages = await call_asgi(app, scope, [b"x" * 10])
        assert messages[0]["status"] == 200
        assert messages[1]["body"] == b"10"

    async def test_other_messages_pass_through(self) -> None:
        received: list[str] = []

        async def read_past_end(scope: Scope, receive: Receive, send: Send) -> None:
            await read_body(receive)
            received.append((await receive())["type"])
            await PlainTextResponse("done")(scope, receive, send)

        app = BodySizeLimitMiddleware(read_past_end, max_bytes=MAX_BYTES)
        messages = await call_asgi(app, http_scope(), [b"x" * 10])
        assert received == ["http.disconnect"]
        assert messages[0]["status"] == 200

    async def test_overflow_after_response_started_is_reraised(self) -> None:
        async def respond_then_read(scope: Scope, receive: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await read_body(receive)

        app = BodySizeLimitMiddleware(respond_then_read, max_bytes=MAX_BYTES)
        with pytest.raises(StarletteHTTPException) as caught:
            await call_asgi(app, http_scope(), [b"x" * 800, b"x" * 800])
        assert caught.value.status_code == 413

    async def test_inside_fastapi_returns_problem_details(self) -> None:
        api = FastAPI()
        register_exception_handlers(api)

        @api.post("/import")
        async def upload(request: Request) -> dict[str, int]:
            return {"size": len(await request.body())}

        app = BodySizeLimitMiddleware(api, max_bytes=MAX_BYTES)
        async with client_for(app) as client:
            ok = await client.post("/import", content=chunked(100))
            too_large = await client.post("/import", content=chunked(MAX_BYTES + 1))

        assert ok.json() == {"size": 100}
        assert too_large.status_code == 413
        body = too_large.json()
        assert body["code"] == "payload_too_large"
        assert body["detail"] == f"Request body exceeds the {MAX_BYTES} byte limit."

    async def test_non_http_scopes_pass_through(self) -> None:
        seen: list[str] = []

        async def inner(scope: Scope, receive: Receive, send: Send) -> None:
            seen.append(scope["type"])

        await BodySizeLimitMiddleware(inner, max_bytes=1)(
            {"type": "lifespan"}, _never_receive, _never_send
        )
        assert seen == ["lifespan"]
