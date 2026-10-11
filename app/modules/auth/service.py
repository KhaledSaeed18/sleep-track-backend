"""Authentication use cases.

Refresh tokens are opaque, stored as SHA-256 digests and rotated on every use.
Each device session (``AuthSession``) owns a family of tokens; presenting an
already-rotated token outside a short grace window is treated as theft and
revokes the whole family.
"""

from __future__ import annotations

import random
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.deps import ClientInfo
from app.core.errors import (
    AccountDisabledError,
    AppError,
    EmailNotVerifiedError,
    EmailTakenError,
    ForbiddenError,
    InvalidCredentialsError,
    NotAuthenticatedError,
    NotFoundError,
    ProviderDisabledError,
    TokenExpiredError,
    TokenInvalidError,
)
from app.core.rate_limit import RateLimit, RateLimiter
from app.core.security import (
    IssuedAccessToken,
    PasswordHasher,
    TokenCodec,
    generate_token,
    hash_token,
    pkce_challenge,
)
from app.core.timeutils import utcnow
from app.db.errors import constraint_name
from app.integrations.oauth.base import OAuthError, OAuthUserInfo
from app.integrations.oauth.registry import OAuthRegistry
from app.modules.auth.audit import record_audit
from app.modules.auth.devices import describe_user_agent
from app.modules.auth.models import (
    ActionToken,
    ActionTokenPurpose,
    AuditEvent,
    AuthSession,
    RefreshToken,
)
from app.modules.auth.repository import AuthRepository
from app.modules.auth.revocation import TokenRevocationStore
from app.modules.auth.schemas import LoginRequest, RegisterRequest
from app.modules.sleep.demo_data import populate_demo_history
from app.modules.users.models import OAuthAccount, User
from app.modules.users.provisioning import provision_user
from app.modules.users.repository import UserRepository, normalize_email
from app.worker.queue import JobQueue

logger = structlog.get_logger(__name__)

LOGIN_EMAIL_LIMIT = RateLimit(5, 60)
FORGOT_PASSWORD_EMAIL_LIMIT = RateLimit(3, 3600)
RESEND_VERIFICATION_LIMIT = RateLimit(3, 3600)
OAUTH_STATE_TTL_SECONDS = 600
EMAIL_UNIQUE_INDEX = "ux_users_email_lower"


class ActionTokenInvalidError(AppError):
    # 400 rather than 401: these tokens arrive in a body from an email link and
    # must not trigger a client's "refresh and retry" handling.
    status_code = 400
    code = "token_invalid"
    title = "Invalid or already used link"


class ActionTokenExpiredError(AppError):
    status_code = 400
    code = "token_expired"
    title = "Link expired"


@dataclass(frozen=True, slots=True)
class AuthResult:
    user: User
    access: IssuedAccessToken
    refresh_token: str
    csrf_token: str
    persistent: bool
    refresh_expires_at: datetime


@dataclass(frozen=True, slots=True)
class OAuthCompletion:
    result: AuthResult
    redirect_to: str


def create_action_token(
    repo: AuthRepository, user_id: uuid.UUID, purpose: ActionTokenPurpose, ttl: timedelta
) -> str:
    raw = generate_token()
    now = utcnow()
    repo.add(
        ActionToken(
            id=uuid.uuid4(),
            user_id=user_id,
            purpose=purpose,
            token_hash=hash_token(raw),
            expires_at=now + ttl,
            created_at=now,
        )
    )
    return raw


def _email_key(email: str) -> str:
    # Hash so rate-limit keys never store raw addresses.
    return hash_token(normalize_email(email))[:32]


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        hasher: PasswordHasher,
        codec: TokenCodec,
        revocations: TokenRevocationStore,
        limiter: RateLimiter,
        queue: JobQueue,
        redis: Redis,
    ) -> None:
        self.session = session
        self.settings = settings
        self.hasher = hasher
        self.codec = codec
        self.revocations = revocations
        self.limiter = limiter
        self.queue = queue
        self.redis = redis
        self.users = UserRepository(session)
        self.repo = AuthRepository(session)

    # ================================================================== sessions
    def _session_ttl(self, remember_me: bool) -> timedelta:
        if remember_me:
            return timedelta(days=self.settings.refresh_token_ttl_days)
        return timedelta(hours=self.settings.refresh_session_ttl_hours)

    def _mint_refresh_token(self, auth_session: AuthSession, now: datetime) -> str:
        raw = generate_token()
        self.repo.add(
            RefreshToken(
                id=uuid.uuid4(),
                session_id=auth_session.id,
                token_hash=hash_token(raw),
                expires_at=auth_session.expires_at,
                created_at=now,
            )
        )
        return raw

    def _result(self, user: User, auth_session: AuthSession, refresh_token: str) -> AuthResult:
        access = self.codec.issue_access_token(
            user_id=user.id, session_id=auth_session.id, role=user.role.value
        )
        return AuthResult(
            user=user,
            access=access,
            refresh_token=refresh_token,
            csrf_token=generate_token(24),
            persistent=auth_session.remember_me,
            refresh_expires_at=auth_session.expires_at,
        )

    def _start_session(self, user: User, client: ClientInfo, *, remember_me: bool) -> AuthResult:
        now = utcnow()
        auth_session = AuthSession(
            id=uuid.uuid4(),
            user_id=user.id,
            user_agent=client.user_agent,
            ip_address=client.ip_address,
            device_label=describe_user_agent(client.user_agent),
            remember_me=remember_me,
            created_at=now,
            last_used_at=now,
            expires_at=now + self._session_ttl(remember_me),
        )
        self.repo.add(auth_session)
        refresh_token = self._mint_refresh_token(auth_session, now)
        return self._result(user, auth_session, refresh_token)

    async def _revoke_all(
        self, user_id: uuid.UUID, *, reason: str, except_session_id: uuid.UUID | None = None
    ) -> list[uuid.UUID]:
        return await self.repo.revoke_sessions(
            user_id, reason=reason, now=utcnow(), except_session_id=except_session_id
        )

    # ================================================================== register / login
    async def register(self, data: RegisterRequest, client: ClientInfo) -> AuthResult:
        if not self.settings.registration_open:
            raise ForbiddenError("Registration is currently closed.")
        if await self.users.email_exists(data.email):
            raise EmailTakenError("An account with this email already exists.")

        try:
            user = await provision_user(
                self.session,
                email=data.email,
                first_name=data.first_name,
                last_name=data.last_name,
                password_hash=self.hasher.hash(data.password),
                timezone=data.timezone or "UTC",
            )
        except IntegrityError as exc:
            # Lost a race with a concurrent registration for the same address.
            await self.session.rollback()
            if constraint_name(exc) == EMAIL_UNIQUE_INDEX:
                raise EmailTakenError("An account with this email already exists.") from exc
            raise
        result = self._start_session(user, client, remember_me=False)
        verify_token = self._create_action_token(
            user.id,
            ActionTokenPurpose.VERIFY_EMAIL,
            timedelta(hours=self.settings.email_token_ttl_hours),
        )
        record_audit(self.session, AuditEvent.REGISTER, user_id=user.id, client=client)
        await self.session.commit()

        await self._send_verification_email(user, verify_token)
        return result

    async def login(self, data: LoginRequest, client: ClientInfo) -> AuthResult:
        await self.limiter.enforce(f"login:email:{_email_key(data.email)}", LOGIN_EMAIL_LIMIT)

        user = await self.users.get_by_email(data.email)
        # verify() burns a comparable amount of time when the user is unknown.
        valid, updated_hash = self.hasher.verify(
            data.password, user.password_hash if user else None
        )
        if user is None or not valid:
            record_audit(
                self.session,
                AuditEvent.LOGIN_FAILED,
                user_id=user.id if user else None,
                client=client,
                metadata={"reason": "invalid_credentials"},
            )
            await self.session.commit()
            raise InvalidCredentialsError("Incorrect email or password.")

        self._ensure_can_sign_in(user)
        if updated_hash:
            user.password_hash = updated_hash
        user.last_login_at = utcnow()
        result = self._start_session(user, client, remember_me=data.remember_me)
        record_audit(
            self.session,
            AuditEvent.LOGIN,
            user_id=user.id,
            client=client,
            metadata={"method": "password", "remember_me": data.remember_me},
        )
        await self.session.commit()
        return result

    def _ensure_can_sign_in(self, user: User) -> None:
        if not user.is_active:
            raise AccountDisabledError("This account has been disabled.")
        if (
            self.settings.email_verification_required
            and not user.is_email_verified
            and not user.is_demo
        ):
            raise EmailNotVerifiedError("Please verify your email address before signing in.")

    # ================================================================== refresh / logout
    async def refresh(self, raw_token: str | None, client: ClientInfo) -> AuthResult:
        if not raw_token:
            raise NotAuthenticatedError("No refresh token was provided.")
        token = await self.repo.get_refresh_token_for_update(hash_token(raw_token))
        if token is None:
            raise TokenInvalidError("Refresh token is invalid.")

        auth_session = token.session
        now = utcnow()
        if auth_session.revoked_at is not None:
            raise TokenInvalidError("This session has been signed out.")

        if token.used_at is not None:
            await self._handle_reuse(token, auth_session, client, now)

        if token.expires_at <= now or auth_session.expires_at <= now:
            raise TokenExpiredError("Your session has expired. Please sign in again.")

        user = await self.users.get(auth_session.user_id)
        if user is None:
            raise TokenInvalidError("Refresh token is invalid.")
        self._ensure_can_sign_in(user)

        token.used_at = now
        auth_session.last_used_at = now
        auth_session.expires_at = now + self._session_ttl(auth_session.remember_me)
        auth_session.ip_address = client.ip_address
        if client.user_agent:
            auth_session.user_agent = client.user_agent
            auth_session.device_label = describe_user_agent(client.user_agent)
        new_raw = self._mint_refresh_token(auth_session, now)
        await self.session.commit()
        return self._result(user, auth_session, new_raw)

    async def _handle_reuse(
        self, token: RefreshToken, auth_session: AuthSession, client: ClientInfo, now: datetime
    ) -> None:
        assert token.used_at is not None  # noqa: S101 - guarded by caller
        grace = timedelta(seconds=self.settings.refresh_reuse_grace_seconds)
        if now - token.used_at <= grace:
            # Most likely two tabs refreshing at once: the winner already set the
            # new cookie, so reject without punishing the user.
            raise TokenInvalidError("Refresh token was already rotated.")

        auth_session.revoked_at = now
        auth_session.revoked_reason = "token_reuse"
        record_audit(
            self.session,
            AuditEvent.TOKEN_REUSE_DETECTED,
            user_id=auth_session.user_id,
            client=client,
            metadata={"session_id": str(auth_session.id)},
        )
        await self.session.commit()
        await self.revocations.revoke_sessions([auth_session.id])
        logger.warning("refresh_token_reuse", session_id=str(auth_session.id))

        user = await self.users.get(auth_session.user_id)
        if user is not None and not user.is_demo:
            await self.queue.enqueue(
                "send_email",
                to=user.email,
                template="security_alert",
                context={
                    "first_name": user.first_name,
                    "message": (
                        "A previously used sign-in token for your account was presented again, "
                        "which can indicate that it was copied from one of your devices."
                    ),
                },
            )
        raise TokenInvalidError("Refresh token reuse detected; the session was revoked.")

    async def logout(self, raw_token: str | None, client: ClientInfo) -> None:
        if not raw_token:
            return
        token = await self.repo.get_refresh_token_for_update(hash_token(raw_token))
        if token is None or token.session.revoked_at is not None:
            return
        auth_session = token.session
        auth_session.revoked_at = utcnow()
        auth_session.revoked_reason = "logout"
        record_audit(self.session, AuditEvent.LOGOUT, user_id=auth_session.user_id, client=client)
        await self.session.commit()
        await self.revocations.revoke_sessions([auth_session.id])

    async def logout_all(self, user: User, client: ClientInfo) -> None:
        revoked = await self._revoke_all(user.id, reason="logout_all")
        record_audit(
            self.session,
            AuditEvent.LOGOUT_ALL,
            user_id=user.id,
            client=client,
            metadata={"sessions": len(revoked)},
        )
        await self.session.commit()
        await self.revocations.revoke_sessions(revoked)

    # ================================================================== demo
    async def create_demo(self, client: ClientInfo, timezone: str | None) -> AuthResult:
        if not self.settings.demo_mode_enabled:
            raise ForbiddenError("Demo mode is disabled.")
        user = await provision_user(
            self.session,
            email=f"demo-{secrets.token_hex(6)}@demo.sleeptrack.app",
            first_name="Demo",
            last_name="Sleeper",
            timezone=timezone or "UTC",
            is_email_verified=True,
            is_demo=True,
        )
        await self.session.flush()
        await populate_demo_history(
            self.session, user, nights=self.settings.demo_nights, rng=random.Random()
        )
        user.last_login_at = utcnow()
        result = self._start_session(user, client, remember_me=False)
        record_audit(
            self.session,
            AuditEvent.LOGIN,
            user_id=user.id,
            client=client,
            metadata={"method": "demo"},
        )
        await self.session.commit()
        return result

    # ================================================================== email verification
    def _create_action_token(
        self, user_id: uuid.UUID, purpose: ActionTokenPurpose, ttl: timedelta
    ) -> str:
        return create_action_token(self.repo, user_id, purpose, ttl)

    async def _send_verification_email(self, user: User, raw_token: str) -> None:
        await self.queue.enqueue(
            "send_email",
            to=user.email,
            template="verify_email",
            context={
                "first_name": user.first_name,
                "link": f"{self.settings.frontend_url}/auth/verify-email?token={raw_token}",
                "expires_hours": self.settings.email_token_ttl_hours,
            },
        )

    async def _consume_action_token(self, raw: str, purpose: ActionTokenPurpose) -> ActionToken:
        token = await self.repo.get_action_token_for_update(hash_token(raw), purpose)
        if token is None or token.used_at is not None:
            raise ActionTokenInvalidError("This link is invalid or has already been used.")
        now = utcnow()
        if token.expires_at <= now:
            raise ActionTokenExpiredError("This link has expired. Please request a new one.")
        token.used_at = now
        return token

    async def verify_email(self, raw_token: str, client: ClientInfo) -> None:
        token = await self._consume_action_token(raw_token, ActionTokenPurpose.VERIFY_EMAIL)
        user = await self.users.get(token.user_id)
        if user is None:
            raise ActionTokenInvalidError("This link is invalid or has already been used.")
        if not user.is_email_verified:
            user.is_email_verified = True
            user.email_verified_at = utcnow()
            record_audit(self.session, AuditEvent.EMAIL_VERIFIED, user_id=user.id, client=client)
        await self.session.commit()

    async def resend_verification(self, user: User) -> None:
        if user.is_email_verified:
            return
        await self.limiter.enforce(f"verify-resend:user:{user.id}", RESEND_VERIFICATION_LIMIT)
        await self.repo.invalidate_action_tokens(user.id, ActionTokenPurpose.VERIFY_EMAIL, utcnow())
        raw = self._create_action_token(
            user.id,
            ActionTokenPurpose.VERIFY_EMAIL,
            timedelta(hours=self.settings.email_token_ttl_hours),
        )
        await self.session.commit()
        await self._send_verification_email(user, raw)

    # ================================================================== password reset
    async def forgot_password(self, email: str) -> None:
        """Always succeeds. The lookup runs in a background job so the response
        time does not reveal whether the address is registered."""
        await self.limiter.enforce(
            f"forgot-password:email:{_email_key(email)}", FORGOT_PASSWORD_EMAIL_LIMIT
        )
        await self.queue.enqueue("send_password_reset", email=normalize_email(email))

    async def reset_password(self, raw_token: str, password: str, client: ClientInfo) -> None:
        token = await self._consume_action_token(raw_token, ActionTokenPurpose.RESET_PASSWORD)
        user = await self.users.get(token.user_id)
        if user is None or not user.is_active:
            raise ActionTokenInvalidError("This link is invalid or has already been used.")
        user.password_hash = self.hasher.hash(password)
        # Receiving the link proves control of the inbox.
        if not user.is_email_verified:
            user.is_email_verified = True
            user.email_verified_at = utcnow()
        revoked = await self._revoke_all(user.id, reason="password_reset")
        record_audit(self.session, AuditEvent.PASSWORD_RESET, user_id=user.id, client=client)
        await self.session.commit()
        await self.revocations.revoke_sessions(revoked)
        await self._send_password_changed(user)

    async def _send_password_changed(self, user: User) -> None:
        await self.queue.enqueue(
            "send_email",
            to=user.email,
            template="password_changed",
            context={
                "first_name": user.first_name,
                "changed_at": utcnow().strftime("%Y-%m-%d %H:%M"),
            },
        )

    # ================================================================== OAuth
    def _oauth_redirect_uri(self, provider_id: str) -> str:
        base = self.settings.oauth_callback_base
        return f"{base}{self.settings.api_prefix}/auth/oauth/{provider_id}/callback"

    async def begin_oauth(
        self, registry: OAuthRegistry, provider_id: str, redirect_to: str
    ) -> tuple[str, str]:
        provider = registry.get(provider_id)
        if provider is None:
            raise NotFoundError("Unknown OAuth provider.")
        if not provider.enabled:
            raise ProviderDisabledError(f"{provider.name} sign-in is not configured.")
        state = generate_token(24)
        verifier = generate_token(48)
        await self.redis.set(
            f"oauth:state:{state}",
            f"{provider_id}|{verifier}|{redirect_to}",
            ex=OAUTH_STATE_TTL_SECONDS,
        )
        url = provider.authorization_url(
            state=state,
            code_challenge=pkce_challenge(verifier),
            redirect_uri=self._oauth_redirect_uri(provider_id),
        )
        return url, state

    async def complete_oauth(
        self,
        registry: OAuthRegistry,
        provider_id: str,
        *,
        code: str,
        state: str,
        cookie_state: str | None,
        client: ClientInfo,
    ) -> OAuthCompletion:
        provider = registry.get(provider_id)
        if provider is None or not provider.enabled:
            raise OAuthError("provider_disabled", "Provider is not available.")
        # The state must match the browser-bound cookie (login CSRF) and exist
        # server-side exactly once (replay protection).
        if not cookie_state or not secrets.compare_digest(cookie_state, state):
            raise OAuthError("state_mismatch", "OAuth state mismatch.")
        stored = await self.redis.getdel(f"oauth:state:{state}")
        if not stored:
            raise OAuthError("state_expired", "OAuth state expired.")
        stored_provider, verifier, redirect_to = str(stored).split("|", 2)
        if stored_provider != provider_id:
            raise OAuthError("state_mismatch", "OAuth state mismatch.")

        info = await provider.fetch_user(
            code=code, code_verifier=verifier, redirect_uri=self._oauth_redirect_uri(provider_id)
        )
        user = await self._user_for_oauth(info, client)
        self._ensure_can_sign_in(user)
        user.last_login_at = utcnow()
        result = self._start_session(user, client, remember_me=True)
        record_audit(
            self.session,
            AuditEvent.LOGIN,
            user_id=user.id,
            client=client,
            metadata={"method": info.provider},
        )
        await self.session.commit()
        return OAuthCompletion(result=result, redirect_to=redirect_to)

    async def _user_for_oauth(self, info: OAuthUserInfo, client: ClientInfo) -> User:
        account = await self.users.get_oauth_account(info.provider, info.account_id)
        if account is not None:
            user = await self.users.get(account.user_id)
            if user is None:  # pragma: no cover - FK cascade makes this unreachable
                raise OAuthError("account_missing", "Linked account no longer exists.")
            return user

        if not info.email_verified:
            raise OAuthError("email_unverified", "Your Google email address is not verified.")

        user = await self.users.get_by_email(info.email)
        metadata: dict[str, Any] = {"provider": info.provider}
        if user is None:
            if not self.settings.registration_open:
                raise OAuthError("registration_closed", "Registration is closed.")
            user = await provision_user(
                self.session,
                email=info.email,
                first_name=info.first_name,
                last_name=info.last_name,
                is_email_verified=True,
                avatar_url=info.avatar_url,
            )
            record_audit(
                self.session, AuditEvent.REGISTER, user_id=user.id, client=client, metadata=metadata
            )
        elif not user.is_email_verified:
            # The provider vouches for the address, which also blocks pre-registration
            # takeover: an unverified password account cannot keep its password.
            user.is_email_verified = True
            user.email_verified_at = utcnow()
            user.password_hash = None
        user.oauth_accounts.append(
            OAuthAccount(
                user_id=user.id,
                provider=info.provider,
                provider_account_id=info.account_id,
                email=info.email,
            )
        )
        record_audit(
            self.session, AuditEvent.OAUTH_LINKED, user_id=user.id, client=client, metadata=metadata
        )
        return user
