"""Domain exceptions and their RFC 9457 problem-details representation.

Services raise these typed exceptions; a single set of handlers turns them
into ``application/problem+json`` responses with a stable ``code`` the
frontend can switch on.
"""

from __future__ import annotations

from collections.abc import Sequence
from http import HTTPStatus
from typing import Any, ClassVar

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import get_request_id
from app.db.errors import UNSTORABLE_TEXT_SQLSTATES, sqlstate

PROBLEM_BASE_URL = "https://sleeptrack.app/problems/"
PROBLEM_CONTENT_TYPE = "application/problem+json"

logger = structlog.get_logger(__name__)


class FieldError(BaseModel):
    field: str
    message: str
    code: str


class ProblemDetail(BaseModel):
    """RFC 9457 problem details with SleepTrack extensions."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "https://sleeptrack.app/problems/validation-error",
                "title": "Validation failed",
                "status": 422,
                "detail": "One or more fields are invalid.",
                "instance": "/api/v1/sleep/sessions",
                "code": "validation_error",
                "request_id": "6f1c2d0e9a7b4c3f",
                "errors": [
                    {
                        "field": "ended_at",
                        "message": "must be after started_at",
                        "code": "value_error",
                    }
                ],
            }
        }
    )

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    code: str
    request_id: str | None = None
    errors: list[FieldError] | None = Field(default=None)


class AppError(Exception):
    """Base class for errors that map to a problem-details response."""

    status_code: ClassVar[int] = HTTPStatus.BAD_REQUEST
    code: ClassVar[str] = "bad_request"
    title: ClassVar[str] = "Bad request"

    def __init__(
        self,
        detail: str | None = None,
        *,
        errors: Sequence[FieldError] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.errors = list(errors) if errors else None
        self.headers = headers or {}


class ValidationFailedError(AppError):
    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    code = "validation_error"
    title = "Validation failed"

    @classmethod
    def for_field(
        cls, field: str, message: str, code: str = "value_error"
    ) -> ValidationFailedError:
        return cls(
            "One or more fields are invalid.",
            errors=[FieldError(field=field, message=message, code=code)],
        )


class InvalidCredentialsError(AppError):
    status_code = HTTPStatus.UNAUTHORIZED
    code = "invalid_credentials"
    title = "Invalid credentials"


class EmailTakenError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "email_taken"
    title = "Email already registered"


class EmailNotVerifiedError(AppError):
    status_code = HTTPStatus.FORBIDDEN
    code = "email_not_verified"
    title = "Email not verified"


class AccountDisabledError(AppError):
    status_code = HTTPStatus.FORBIDDEN
    code = "account_disabled"
    title = "Account disabled"


class TokenInvalidError(AppError):
    status_code = HTTPStatus.UNAUTHORIZED
    code = "token_invalid"
    title = "Invalid token"


class TokenExpiredError(AppError):
    status_code = HTTPStatus.UNAUTHORIZED
    code = "token_expired"
    title = "Token expired"


class NotAuthenticatedError(AppError):
    status_code = HTTPStatus.UNAUTHORIZED
    code = "not_authenticated"
    title = "Not authenticated"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail, headers={"WWW-Authenticate": "Bearer"})


class ForbiddenError(AppError):
    status_code = HTTPStatus.FORBIDDEN
    code = "forbidden"
    title = "Forbidden"


class NotFoundError(AppError):
    status_code = HTTPStatus.NOT_FOUND
    code = "not_found"
    title = "Not found"


class ConflictError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "conflict"
    title = "Conflict"


class SessionOverlapError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "session_overlap"
    title = "Sleep session overlaps another session"


class TimerAlreadyRunningError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "timer_already_running"
    title = "A sleep timer is already running"


class NoActiveTimerError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "no_active_timer"
    title = "No active sleep timer"


class RateLimitedError(AppError):
    status_code = HTTPStatus.TOO_MANY_REQUESTS
    code = "rate_limited"
    title = "Too many requests"

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            f"Rate limit exceeded. Try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )
        self.retry_after = retry_after


class CsrfFailedError(AppError):
    status_code = HTTPStatus.FORBIDDEN
    code = "csrf_failed"
    title = "CSRF validation failed"


class ProviderDisabledError(AppError):
    status_code = HTTPStatus.BAD_REQUEST
    code = "provider_disabled"
    title = "Provider disabled"


class PayloadTooLargeError(AppError):
    status_code = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    code = "payload_too_large"
    title = "Payload too large"


class ServiceUnavailableError(AppError):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "service_unavailable"
    title = "Service unavailable"


_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    429: "rate_limited",
}


def _slug(code: str) -> str:
    return code.replace("_", "-")


def problem_response(
    request: Request | None,
    *,
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    errors: Sequence[FieldError] | None = None,
    headers: dict[str, str] | None = None,
    instance: str | None = None,
) -> JSONResponse:
    problem = ProblemDetail(
        type=f"{PROBLEM_BASE_URL}{_slug(code)}",
        title=title,
        status=status,
        detail=detail,
        instance=instance or (request.url.path if request else None),
        code=code,
        request_id=get_request_id(),
        errors=list(errors) if errors else None,
    )
    return JSONResponse(
        problem.model_dump(exclude_none=True),
        status_code=status,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=headers,
    )


def _format_loc(loc: Sequence[int | str]) -> str:
    parts = [str(p) for p in loc]
    if parts and parts[0] in {"body", "query", "path", "header", "cookie", "form"}:
        parts = parts[1:]
    return ".".join(parts) or "__root__"


def _clean_message(message: str) -> str:
    # Pydantic prefixes custom ValueError messages; strip that for clients.
    return message.removeprefix("Value error, ")


def validation_errors(raw: Sequence[Any]) -> list[FieldError]:
    return [
        FieldError(
            field=_format_loc(err.get("loc", ())),
            message=_clean_message(str(err.get("msg", "invalid value"))),
            code=str(err.get("type", "value_error")),
        )
        for err in raw
    ]


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101 - narrowed by registration
    return problem_response(
        request,
        status=exc.status_code,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        errors=exc.errors,
        headers=exc.headers or None,
    )


async def _validation_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    return problem_response(
        request,
        status=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="validation_error",
        title="Validation failed",
        detail="One or more fields are invalid.",
        errors=validation_errors(exc.errors()),
    )


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    status = exc.status_code
    code = _STATUS_CODES.get(status, "error" if status < 500 else "internal_error")
    title = HTTPStatus(status).phrase if status in HTTPStatus._value2member_map_ else "Error"
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail != title else None
    return problem_response(
        request,
        status=status,
        code=code,
        title=title,
        detail=detail,
        headers=dict(exc.headers) if exc.headers else None,
    )


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled_exception", path=request.url.path, exc_info=exc)
    return problem_response(
        request,
        status=HTTPStatus.INTERNAL_SERVER_ERROR,
        code="internal_error",
        title="Internal server error",
        detail="An unexpected error occurred. Please try again later.",
    )


async def _database_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DBAPIError)  # noqa: S101
    # Input PostgreSQL refuses to store (a NUL character in any text value) is a
    # client error, not an outage.
    if sqlstate(exc) in UNSTORABLE_TEXT_SQLSTATES:
        return problem_response(
            request,
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            code="validation_error",
            title="Validation failed",
            detail="Text values must not contain NUL (U+0000) characters.",
            errors=[
                FieldError(
                    field="__root__",
                    message="must not contain NUL (U+0000) characters",
                    code="invalid_character",
                )
            ],
        )
    return await _unhandled_handler(request, exc)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(DBAPIError, _database_error_handler)
    app.add_exception_handler(Exception, _unhandled_handler)


# Shared OpenAPI response declarations so every operation documents problem+json.
def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    descriptions = {
        400: "Bad request",
        401: "Not authenticated or token invalid",
        403: "Forbidden",
        404: "Resource not found",
        409: "Conflict",
        413: "Payload too large",
        422: "Validation error",
        429: "Rate limited",
        503: "Service unavailable",
    }
    # The media type is rewritten to application/problem+json when the OpenAPI
    # document is generated (see app.core.openapi).
    return {
        status: {
            "model": ProblemDetail,
            "description": descriptions.get(status, HTTPStatus(status).phrase),
        }
        for status in statuses
    }
