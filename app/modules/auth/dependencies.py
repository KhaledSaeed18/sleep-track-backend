from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.deps import HasherDep, SettingsDep, TokenCodecDep
from app.core.errors import (
    AccountDisabledError,
    CsrfFailedError,
    ForbiddenError,
    NotAuthenticatedError,
    TokenInvalidError,
)
from app.core.rate_limit import RateLimiterDep
from app.core.redis import RedisDep
from app.core.security import AccessTokenClaims, constant_time_equals
from app.db.session import DbSession
from app.integrations.oauth.registry import OAuthRegistry
from app.modules.auth.cookies import CSRF_COOKIE, CSRF_HEADER
from app.modules.auth.revocation import TokenRevocationStore
from app.modules.auth.service import AuthService
from app.modules.users.models import User, UserRole
from app.modules.users.repository import UserRepository
from app.worker.queue import JobQueueDep

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="Access token from login/refresh: `Authorization: Bearer <token>`.",
)


def get_revocation_store(redis: RedisDep, settings: SettingsDep) -> TokenRevocationStore:
    return TokenRevocationStore(redis, ttl_seconds=settings.access_token_ttl_seconds)


RevocationStoreDep = Annotated[TokenRevocationStore, Depends(get_revocation_store)]


async def get_access_claims(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    codec: TokenCodecDep,
    revocations: RevocationStoreDep,
) -> AccessTokenClaims:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise NotAuthenticatedError("Missing bearer token.")
    claims = codec.decode_access_token(credentials.credentials)
    if await revocations.is_revoked(claims):
        raise TokenInvalidError("This session has been signed out.")
    return claims


AccessClaims = Annotated[AccessTokenClaims, Depends(get_access_claims)]


async def get_current_user(claims: AccessClaims, session: DbSession) -> User:
    user = await UserRepository(session).get(claims.user_id)
    if user is None:
        raise TokenInvalidError("The account for this token no longer exists.")
    if not user.is_active:
        raise AccountDisabledError("This account has been disabled.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role is not UserRole.ADMIN:
        raise ForbiddenError("Administrator role required.")
    return user


CurrentAdmin = Annotated[User, Depends(require_admin)]


def verify_csrf(request: Request) -> None:
    """Double-submit check for cookie-authenticated endpoints."""
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not constant_time_equals(cookie, header):
        raise CsrfFailedError("Missing or mismatched CSRF token.")


def get_auth_service(
    session: DbSession,
    settings: SettingsDep,
    hasher: HasherDep,
    codec: TokenCodecDep,
    revocations: RevocationStoreDep,
    limiter: RateLimiterDep,
    queue: JobQueueDep,
    redis: RedisDep,
) -> AuthService:
    return AuthService(
        session,
        settings=settings,
        hasher=hasher,
        codec=codec,
        revocations=revocations,
        limiter=limiter,
        queue=queue,
        redis=redis,
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_oauth_registry(request: Request) -> OAuthRegistry:
    registry: OAuthRegistry = request.app.state.oauth
    return registry


OAuthRegistryDep = Annotated[OAuthRegistry, Depends(get_oauth_registry)]
