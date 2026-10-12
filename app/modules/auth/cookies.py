"""Auth cookie handling (refresh token, CSRF double-submit, session hint)."""

from __future__ import annotations

from datetime import datetime

from fastapi import Response

from app.core.config import Settings
from app.core.timeutils import utcnow

REFRESH_COOKIE = "st_refresh"
CSRF_COOKIE = "st_csrf"
SESSION_HINT_COOKIE = "st_session"
OAUTH_STATE_COOKIE = "st_oauth_state"
CSRF_HEADER = "X-CSRF-Token"


def set_auth_cookies(
    response: Response,
    settings: Settings,
    *,
    refresh_token: str,
    csrf_token: str,
    persistent: bool,
    expires_at: datetime,
) -> None:
    # Remember-me sessions get a persistent cookie; otherwise a browser-session
    # cookie while the server still enforces its own (shorter) expiry.
    max_age = max(0, int((expires_at - utcnow()).total_seconds())) if persistent else None

    def _set(name: str, value: str, *, path: str, httponly: bool) -> None:
        response.set_cookie(
            name,
            value,
            max_age=max_age,
            path=path,
            domain=settings.cookie_domain,
            secure=settings.secure_cookies,
            httponly=httponly,
            samesite="lax",
        )

    _set(REFRESH_COOKIE, refresh_token, path=settings.refresh_cookie_path, httponly=True)
    _set(CSRF_COOKIE, csrf_token, path="/", httponly=False)
    _set(SESSION_HINT_COOKIE, "1", path="/", httponly=False)


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    for name, path, httponly in (
        (REFRESH_COOKIE, settings.refresh_cookie_path, True),
        (CSRF_COOKIE, "/", False),
        (SESSION_HINT_COOKIE, "/", False),
    ):
        response.delete_cookie(
            name,
            path=path,
            domain=settings.cookie_domain,
            secure=settings.secure_cookies,
            httponly=httponly,
            samesite="lax",
        )


def set_oauth_state_cookie(response: Response, settings: Settings, state: str) -> None:
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path=f"{settings.api_prefix}/auth/oauth",
        domain=settings.cookie_domain,
    )


def clear_oauth_state_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        OAUTH_STATE_COOKIE,
        path=f"{settings.api_prefix}/auth/oauth",
        domain=settings.cookie_domain,
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )
