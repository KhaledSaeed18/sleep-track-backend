"""Pure ASGI middleware.

Written against the raw ASGI interface rather than ``BaseHTTPMiddleware`` to
avoid its per-request task overhead and streaming caveats.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Iterable

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import reset_request_id, set_request_id
from app.core.errors import problem_response

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")

access_logger = structlog.get_logger("app.access")
error_logger = structlog.get_logger("app.errors")


class RequestContextMiddleware:
    """Assigns a request id, measures latency, writes the access log and turns
    unhandled exceptions into a problem-details 500 that never leaks internals."""

    def __init__(self, app: ASGIApp, *, quiet_paths: Iterable[str] = ()) -> None:
        self.app = app
        self.quiet_paths = frozenset(quiet_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER)
        request_id = incoming if incoming and _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        token = set_request_id(request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id)
        scope.setdefault("state", {})["request_id"] = request_id

        start = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                elapsed_ms = (time.perf_counter() - start) * 1000
                headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            error_logger.exception("unhandled_exception", path=scope.get("path"), exc_info=exc)
            if response_started:
                raise
            response = problem_response(
                None,
                status=500,
                code="internal_error",
                title="Internal server error",
                detail="An unexpected error occurred. Please try again later.",
                instance=scope.get("path"),
            )
            await response(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            if path not in self.quiet_paths:
                client = scope.get("client")
                access_logger.info(
                    "request",
                    method=scope.get("method"),
                    path=path,
                    status=status_code,
                    duration_ms=round((time.perf_counter() - start) * 1000, 2),
                    client_ip=client[0] if client else None,
                )
            structlog.contextvars.unbind_contextvars("request_id")
            reset_request_id(token)


class SecurityHeadersMiddleware:
    """Adds defensive response headers suitable for a JSON API."""

    _API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    # Swagger UI / ReDoc load assets from a CDN and run inline bootstrap code.
    _DOCS_CSP = (
        "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly; "
        "font-src 'self' https://fonts.gstatic.com; worker-src 'self' blob:; frame-ancestors 'none'"
    )

    def __init__(self, app: ASGIApp, *, hsts: bool, docs_prefixes: Iterable[str] = ()) -> None:
        self.app = app
        self.hsts = hsts
        self.docs_prefixes = tuple(docs_prefixes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_docs = bool(self.docs_prefixes) and scope.get("path", "").startswith(self.docs_prefixes)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
                headers.setdefault(
                    "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
                )
                headers.setdefault(
                    "Content-Security-Policy", self._DOCS_CSP if is_docs else self._API_CSP
                )
                if self.hsts:
                    headers.setdefault(
                        "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
                    )
            await send(message)

        await self.app(scope, receive, send_wrapper)


class _BodyTooLargeError(StarletteHTTPException):
    # Subclassing HTTPException keeps FastAPI from re-wrapping it as a generic
    # "error parsing the body" 400 when it surfaces during body parsing.
    def __init__(self, max_bytes: int) -> None:
        super().__init__(413, f"Request body exceeds the {max_bytes} byte limit.")


class BodySizeLimitMiddleware:
    """Rejects request bodies above ``max_bytes`` without buffering them.

    ``Content-Length`` is checked up front; chunked uploads are counted as they
    stream in so a missing or lying header cannot bypass the limit.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = Headers(scope=scope).get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > self.max_bytes:
            await self._reject(scope, receive, send)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLargeError(self.max_bytes)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLargeError:
            if response_started:
                raise
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = problem_response(
            None,
            status=413,
            code="payload_too_large",
            title="Payload too large",
            detail=f"Request body exceeds the {self.max_bytes} byte limit.",
            instance=scope.get("path"),
        )
        await response(scope, receive, send)
