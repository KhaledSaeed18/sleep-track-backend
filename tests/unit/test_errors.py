from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core import errors
from app.core.context import reset_request_id, set_request_id
from app.core.errors import (
    PROBLEM_CONTENT_TYPE,
    AppError,
    FieldError,
    NotAuthenticatedError,
    NotFoundError,
    RateLimitedError,
    ValidationFailedError,
    problem_response,
    problem_responses,
    register_exception_handlers,
    validation_errors,
)


class Window(BaseModel):
    started_at: int
    ended_at: int = Field(ge=0)
    tags: list[str] = []

    @field_validator("ended_at")
    @classmethod
    def _after_start(cls, value: int) -> int:
        if value == 13:
            raise ValueError("must be after started_at")
        return value


def build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/sessions/{session_id}")
    async def get_session(session_id: int) -> dict[str, int]:
        if session_id == 404:
            raise NotFoundError("Sleep session not found.")
        return {"id": session_id}

    @app.post("/windows")
    async def create_window(window: Window) -> Window:
        return window

    @app.get("/limited")
    async def limited() -> None:
        raise RateLimitedError(30)

    @app.get("/private")
    async def private() -> None:
        raise NotAuthenticatedError

    @app.get("/invalid")
    async def invalid() -> None:
        raise ValidationFailedError.for_field("sort", "must be one of started_at")

    @app.get("/teapot")
    async def teapot() -> None:
        raise StarletteHTTPException(418)

    @app.get("/gone")
    async def gone() -> None:
        raise StarletteHTTPException(410, "This export has expired.")

    @app.get("/unavailable")
    async def unavailable() -> None:
        raise StarletteHTTPException(503, headers={"Retry-After": "120"})

    @app.get("/crash")
    async def crash() -> None:
        raise RuntimeError("database password is hunter2")

    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


# =============================================================================== helpers
class TestValidationErrors:
    @pytest.mark.parametrize(
        ("loc", "field"),
        [
            (("body", "email"), "email"),
            (("query", "page"), "page"),
            (("path", "session_id"), "session_id"),
            (("header", "x-csrf-token"), "x-csrf-token"),
            (("cookie", "refresh"), "refresh"),
            (("form", "file"), "file"),
            (("body", "items", 0, "name"), "items.0.name"),
            (("ended_at",), "ended_at"),
            (("body",), "__root__"),
            ((), "__root__"),
        ],
    )
    def test_loc_formatting(self, loc: tuple[int | str, ...], field: str) -> None:
        (error,) = validation_errors([{"loc": loc, "msg": "bad", "type": "value_error"}])
        assert error.field == field

    def test_value_error_prefix_is_stripped(self) -> None:
        (error,) = validation_errors(
            [{"loc": ("body", "ended_at"), "msg": "Value error, must be after started_at"}]
        )
        assert error == FieldError(
            field="ended_at", message="must be after started_at", code="value_error"
        )

    def test_prefix_only_stripped_at_start(self) -> None:
        (error,) = validation_errors([{"loc": ("x",), "msg": "Bad. Value error, nested"}])
        assert error.message == "Bad. Value error, nested"

    def test_defaults_for_missing_keys(self) -> None:
        assert validation_errors([{}]) == [
            FieldError(field="__root__", message="invalid value", code="value_error")
        ]

    def test_keeps_order(self) -> None:
        raw: list[dict[str, Any]] = [
            {"loc": ("body", "a"), "msg": "first", "type": "missing"},
            {"loc": ("body", "b"), "msg": "second", "type": "int_parsing"},
        ]
        assert [(e.field, e.code) for e in validation_errors(raw)] == [
            ("a", "missing"),
            ("b", "int_parsing"),
        ]


class TestProblemResponse:
    def test_shape(self) -> None:
        response = problem_response(
            None,
            status=409,
            code="session_overlap",
            title="Sleep session overlaps another session",
            detail="Overlaps 23:00-07:00.",
            errors=[FieldError(field="started_at", message="overlaps", code="overlap")],
            headers={"X-Extra": "1"},
            instance="/api/v1/sleep/sessions",
        )
        assert response.status_code == 409
        assert response.media_type == PROBLEM_CONTENT_TYPE
        assert response.headers["x-extra"] == "1"
        assert json.loads(bytes(response.body)) == {
            "type": "https://sleeptrack.app/problems/session-overlap",
            "title": "Sleep session overlaps another session",
            "status": 409,
            "detail": "Overlaps 23:00-07:00.",
            "instance": "/api/v1/sleep/sessions",
            "code": "session_overlap",
            "errors": [{"field": "started_at", "message": "overlaps", "code": "overlap"}],
        }

    def test_optional_members_are_omitted(self) -> None:
        response = problem_response(None, status=403, code="forbidden", title="Forbidden")
        assert json.loads(bytes(response.body)) == {
            "type": "https://sleeptrack.app/problems/forbidden",
            "title": "Forbidden",
            "status": 403,
            "code": "forbidden",
        }

    def test_includes_current_request_id(self) -> None:
        token = set_request_id("req-123")
        try:
            response = problem_response(None, status=404, code="not_found", title="Not found")
        finally:
            reset_request_id(token)
        assert json.loads(bytes(response.body))["request_id"] == "req-123"


class TestAppErrors:
    def test_detail_defaults_to_title(self) -> None:
        error = NotFoundError()
        assert str(error) == "Not found"
        assert error.detail is None
        assert error.errors is None
        assert error.headers == {}

    def test_for_field(self) -> None:
        error = ValidationFailedError.for_field("sort", "unknown", code="enum")
        assert error.status_code == 422
        assert error.detail == "One or more fields are invalid."
        assert error.errors == [FieldError(field="sort", message="unknown", code="enum")]

    @pytest.mark.parametrize(
        "error_type",
        [
            cls
            for cls in vars(errors).values()
            if isinstance(cls, type) and issubclass(cls, AppError)
        ],
    )
    def test_every_error_has_a_distinct_code(self, error_type: type[AppError]) -> None:
        assert 400 <= error_type.status_code < 600
        assert error_type.code
        same_code = [
            cls
            for cls in vars(errors).values()
            if isinstance(cls, type) and issubclass(cls, AppError) and cls.code == error_type.code
        ]
        assert same_code == [error_type]


def test_problem_responses_for_openapi() -> None:
    responses = problem_responses(401, 422, 500)
    assert set(responses) == {401, 422, 500}
    assert responses[401]["model"] is errors.ProblemDetail
    assert responses[401]["description"] == "Not authenticated or token invalid"
    assert responses[500]["description"] == "Internal Server Error"


# =============================================================================== handlers
class TestHandlers:
    async def test_app_error(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/sessions/404")
        assert response.status_code == 404
        assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
        assert response.json() == {
            "type": "https://sleeptrack.app/problems/not-found",
            "title": "Not found",
            "status": 404,
            "detail": "Sleep session not found.",
            "instance": "/sessions/404",
            "code": "not_found",
        }

    async def test_error_headers_are_forwarded(self, client: httpx.AsyncClient) -> None:
        limited = await client.get("/limited")
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "30"
        private = await client.get("/private")
        assert private.status_code == 401
        assert private.headers["www-authenticate"] == "Bearer"
        assert private.json()["code"] == "not_authenticated"

    async def test_domain_validation_error(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/invalid")
        assert response.status_code == 422
        assert response.json()["errors"] == [
            {"field": "sort", "message": "must be one of started_at", "code": "value_error"}
        ]

    async def test_request_validation(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/windows", json={"started_at": "soon", "ended_at": 13, "tags": [1]}
        )
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "validation_error"
        assert body["detail"] == "One or more fields are invalid."
        assert {(e["field"], e["code"]) for e in body["errors"]} == {
            ("started_at", "int_parsing"),
            ("ended_at", "value_error"),
            ("tags.0", "string_type"),
        }
        messages = {e["field"]: e["message"] for e in body["errors"]}
        assert messages["ended_at"] == "must be after started_at"

    async def test_missing_body(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/windows")
        assert response.status_code == 422
        assert response.json()["errors"][0]["field"] == "__root__"

    async def test_path_parameter_validation(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/sessions/abc")
        assert response.status_code == 422
        assert response.json()["errors"][0]["field"] == "session_id"

    async def test_unknown_route(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/nope")
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == "not_found"
        assert body["title"] == "Not Found"
        assert "detail" not in body

    async def test_method_not_allowed(self, client: httpx.AsyncClient) -> None:
        response = await client.delete("/limited")
        assert response.status_code == 405
        assert response.json()["code"] == "method_not_allowed"
        assert response.headers["allow"] == "GET"

    async def test_unmapped_client_error(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/teapot")
        assert response.status_code == 418
        assert response.json()["code"] == "error"

    async def test_custom_http_detail(self, client: httpx.AsyncClient) -> None:
        body = (await client.get("/gone")).json()
        assert body["title"] == "Gone"
        assert body["detail"] == "This export has expired."

    async def test_server_http_error(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/unavailable")
        assert response.status_code == 503
        assert response.json()["code"] == "internal_error"
        assert response.headers["retry-after"] == "120"

    async def test_unhandled_exception_does_not_leak(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/crash")
        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "internal_error"
        assert body["detail"] == "An unexpected error occurred. Please try again later."
        assert "hunter2" not in response.text
