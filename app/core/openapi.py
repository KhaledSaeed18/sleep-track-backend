"""OpenAPI document customisation."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

from app.core.errors import PROBLEM_CONTENT_TYPE

OPENAPI_TAGS: list[dict[str, str]] = [
    {
        "name": "auth",
        "description": "Registration, sign-in, token refresh, OAuth and demo accounts.",
    },
    {
        "name": "users",
        "description": "The signed-in user's profile, preferences, devices and data.",
    },
    {"name": "sleep", "description": "Sleep sessions and the live sleep timer."},
    {"name": "tags", "description": "Tags that annotate sleep sessions (caffeine, exercise, ...)."},
    {"name": "goals", "description": "Sleep goals and age-based recommendations."},
    {"name": "analytics", "description": "Aggregated sleep metrics over a date range."},
    {
        "name": "insights",
        "description": "Personalised, explainable observations about sleep habits.",
    },
    {"name": "reports", "description": "Weekly sleep reports."},
    {"name": "admin", "description": "Administrative endpoints (role `admin`)."},
    {"name": "meta", "description": "API metadata and feature flags."},
    {"name": "health", "description": "Liveness and readiness probes."},
]

API_DESCRIPTION = """
SleepTrack records sleep sessions and turns them into analytics, insights and weekly reports.

**Authentication.** Obtain an access token from `POST /api/v1/auth/login` (or `register`/`demo`)
and send it as `Authorization: Bearer <token>`. Access tokens live for 15 minutes; the rotating
refresh token is delivered as the `st_refresh` HttpOnly cookie and exchanged at
`POST /api/v1/auth/refresh` together with the `X-CSRF-Token` header (double-submit of `st_csrf`).

**Errors** use [RFC 9457 problem details](https://www.rfc-editor.org/rfc/rfc9457)
(`application/problem+json`) with a stable `code` field.
""".strip()


def generate_operation_id(route: APIRoute) -> str:
    """``<tag>_<function name>``, e.g. ``sleep_list_sessions``, for clean generated clients."""
    tag = route.tags[0] if route.tags else "default"
    return f"{tag}_{route.name}"


def _use_problem_media_type(schema: dict[str, Any]) -> None:
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            for status_code, response in operation.get("responses", {}).items():
                if not str(status_code).startswith(("4", "5")):
                    continue
                content = response.get("content", {})
                if "application/json" in content:
                    content[PROBLEM_CONTENT_TYPE] = content.pop("application/json")


def install_openapi(app: FastAPI) -> None:
    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
            contact=app.contact,
            license_info=app.license_info,
            servers=app.servers,
        )
        _use_problem_media_type(schema)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
