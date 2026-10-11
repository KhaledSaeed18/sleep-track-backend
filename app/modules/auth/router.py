from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Body, Cookie, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.core.deps import ClientInfoDep, SettingsDep
from app.core.errors import AppError, problem_response, problem_responses
from app.core.rate_limit import rate_limit
from app.integrations.oauth.base import OAuthError
from app.modules.auth.cookies import (
    OAUTH_STATE_COOKIE,
    REFRESH_COOKIE,
    clear_auth_cookies,
    clear_oauth_state_cookie,
    set_auth_cookies,
    set_oauth_state_cookie,
)
from app.modules.auth.dependencies import (
    AuthServiceDep,
    CurrentUser,
    OAuthRegistryDep,
    verify_csrf,
)
from app.modules.auth.schemas import (
    AuthResponse,
    DemoRequest,
    EmailRequest,
    LoginRequest,
    MessageResponse,
    OAuthProviderInfo,
    RegisterRequest,
    ResetPasswordRequest,
    TokenRequest,
)
from app.modules.auth.service import AuthResult
from app.modules.users.schemas import UserRead

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE, include_in_schema=False)]
DEFAULT_REDIRECT = "/dashboard"


def _auth_response(result: AuthResult, response: Response, settings: SettingsDep) -> AuthResponse:
    set_auth_cookies(
        response,
        settings,
        refresh_token=result.refresh_token,
        csrf_token=result.csrf_token,
        persistent=result.persistent,
        expires_at=result.refresh_expires_at,
    )
    response.headers["Cache-Control"] = "no-store"
    return AuthResponse(
        access_token=result.access.token,
        expires_in=result.access.expires_in,
        user=UserRead.model_validate(result.user),
    )


def _safe_redirect_path(value: str | None) -> str:
    """Only same-site relative paths are allowed (prevents open redirects)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return DEFAULT_REDIRECT
    return value[:512]


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    dependencies=[rate_limit("register", "5/hour")],
    responses=problem_responses(403, 409, 422, 429),
)
async def register(
    data: RegisterRequest,
    response: Response,
    service: AuthServiceDep,
    client: ClientInfoDep,
    settings: SettingsDep,
) -> AuthResponse:
    """Creates the account, signs it in and sends a verification email."""
    result = await service.register(data, client)
    return _auth_response(result, response, settings)


@router.post(
    "/login",
    summary="Sign in with email and password",
    dependencies=[rate_limit("login", "10/minute")],
    responses=problem_responses(401, 403, 422, 429),
)
async def login(
    data: LoginRequest,
    response: Response,
    service: AuthServiceDep,
    client: ClientInfoDep,
    settings: SettingsDep,
) -> AuthResponse:
    result = await service.login(data, client)
    return _auth_response(result, response, settings)


@router.post(
    "/refresh",
    response_model=AuthResponse,
    summary="Rotate the refresh token and get a new access token",
    dependencies=[Depends(verify_csrf), rate_limit("refresh", "60/minute")],
    responses=problem_responses(401, 403, 429),
)
async def refresh(
    response: Response,
    service: AuthServiceDep,
    client: ClientInfoDep,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> AuthResponse | Response:
    """Requires the `st_refresh` cookie and an `X-CSRF-Token` header equal to `st_csrf`."""
    try:
        result = await service.refresh(refresh_token, client)
    except AppError as exc:
        failure = problem_response(
            None,
            status=exc.status_code,
            code=exc.code,
            title=exc.title,
            detail=exc.detail,
            instance=f"{settings.api_prefix}/auth/refresh",
            headers=exc.headers or None,
        )
        clear_auth_cookies(failure, settings)
        return failure
    return _auth_response(result, response, settings)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out this device",
    dependencies=[Depends(verify_csrf)],
    responses=problem_responses(403),
)
async def logout(
    service: AuthServiceDep,
    client: ClientInfoDep,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> Response:
    await service.logout(refresh_token, client)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookies(response, settings)
    return response


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out every device",
    responses=problem_responses(401),
)
async def logout_all(
    user: CurrentUser, service: AuthServiceDep, client: ClientInfoDep, settings: SettingsDep
) -> Response:
    await service.logout_all(user, client)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookies(response, settings)
    return response


@router.post(
    "/demo",
    summary="Start an ephemeral demo account",
    dependencies=[rate_limit("demo", "5/hour")],
    responses=problem_responses(403, 429),
)
async def demo(
    response: Response,
    service: AuthServiceDep,
    client: ClientInfoDep,
    settings: SettingsDep,
    data: Annotated[DemoRequest | None, Body()] = None,
) -> AuthResponse:
    """Creates a throw-away account pre-filled with ~90 nights of realistic data.
    Demo accounts are deleted automatically after 24 hours."""
    result = await service.create_demo(client, data.timezone if data else None)
    return _auth_response(result, response, settings)


@router.post(
    "/verify-email",
    summary="Confirm an email address",
    dependencies=[rate_limit("verify-email", "20/hour")],
    responses=problem_responses(400, 422, 429),
)
async def verify_email(
    data: TokenRequest, service: AuthServiceDep, client: ClientInfoDep
) -> MessageResponse:
    await service.verify_email(data.token, client)
    return MessageResponse(message="Your email address has been verified.")


@router.post(
    "/verify-email/resend",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a new verification email",
    responses=problem_responses(401, 429),
)
async def resend_verification(user: CurrentUser, service: AuthServiceDep) -> MessageResponse:
    await service.resend_verification(user)
    return MessageResponse(message="If your email is unverified, a new link is on its way.")


@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request a password reset email",
    dependencies=[rate_limit("forgot-password", "10/hour")],
    responses=problem_responses(422, 429),
)
async def forgot_password(data: EmailRequest, service: AuthServiceDep) -> MessageResponse:
    """Always returns 202 so the response never reveals whether an account exists."""
    await service.forgot_password(data.email)
    return MessageResponse(
        message="If an account exists for that email, a reset link has been sent."
    )


@router.post(
    "/reset-password",
    summary="Set a new password using a reset token",
    dependencies=[rate_limit("reset-password", "10/hour")],
    responses=problem_responses(400, 422, 429),
)
async def reset_password(
    data: ResetPasswordRequest, service: AuthServiceDep, client: ClientInfoDep
) -> MessageResponse:
    """Signs out every device once the password has been changed."""
    await service.reset_password(data.token, data.password, client)
    return MessageResponse(message="Your password has been reset. Please sign in.")


# ---------------------------------------------------------------------------- OAuth
@router.get("/oauth/providers", summary="List OAuth providers")
async def list_oauth_providers(registry: OAuthRegistryDep) -> list[OAuthProviderInfo]:
    return [
        OAuthProviderInfo(id=provider.id, name=provider.name, enabled=provider.enabled)
        for provider in registry.all()
    ]


@router.get(
    "/oauth/{provider}/authorize",
    status_code=status.HTTP_302_FOUND,
    summary="Start an OAuth sign-in",
    response_class=RedirectResponse,
    responses=problem_responses(400, 404),
)
async def oauth_authorize(
    provider: str,
    service: AuthServiceDep,
    registry: OAuthRegistryDep,
    settings: SettingsDep,
    redirect_to: Annotated[str | None, Query(max_length=512)] = DEFAULT_REDIRECT,
) -> RedirectResponse:
    """Redirects to the provider with PKCE (S256) and a state bound to a cookie."""
    url, state = await service.begin_oauth(registry, provider, _safe_redirect_path(redirect_to))
    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    set_oauth_state_cookie(response, settings, state)
    return response


@router.get(
    "/oauth/{provider}/callback",
    status_code=status.HTTP_302_FOUND,
    summary="OAuth redirect target",
    response_class=RedirectResponse,
)
async def oauth_callback(
    *,
    request: Request,
    provider: str,
    service: AuthServiceDep,
    registry: OAuthRegistryDep,
    client: ClientInfoDep,
    settings: SettingsDep,
    code: Annotated[str | None, Query(max_length=2048)] = None,
    state: Annotated[str | None, Query(max_length=256)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> RedirectResponse:
    """Signs the user in and redirects to `${FRONTEND_URL}/auth/callback?next=...`,
    or to `...?error=<code>` on failure."""
    callback_url = f"{settings.frontend_url}/auth/callback"

    def fail(reason: str) -> RedirectResponse:
        failure = RedirectResponse(
            f"{callback_url}?{urlencode({'error': reason})}", status_code=status.HTTP_302_FOUND
        )
        clear_oauth_state_cookie(failure, settings)
        return failure

    if error:
        return fail("access_denied")
    if not code or not state:
        return fail("invalid_request")
    try:
        completion = await service.complete_oauth(
            registry,
            provider,
            code=code,
            state=state,
            cookie_state=request.cookies.get(OAUTH_STATE_COOKIE),
            client=client,
        )
    except OAuthError as exc:
        logger.info("oauth_failed", provider=provider, reason=exc.code)
        return fail(exc.code)
    except AppError as exc:
        return fail(exc.code)

    result = completion.result
    response = RedirectResponse(
        f"{callback_url}?{urlencode({'next': completion.redirect_to})}",
        status_code=status.HTTP_302_FOUND,
    )
    set_auth_cookies(
        response,
        settings,
        refresh_token=result.refresh_token,
        csrf_token=result.csrf_token,
        persistent=result.persistent,
        expires_at=result.refresh_expires_at,
    )
    clear_oauth_state_cookie(response, settings)
    return response
