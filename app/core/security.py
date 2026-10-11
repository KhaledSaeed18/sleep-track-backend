"""Password hashing, access tokens (JWT) and opaque single-use tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.config import Settings
from app.core.errors import TokenExpiredError, TokenInvalidError
from app.core.timeutils import utcnow

ACCESS_TOKEN_TYPE = "access"  # noqa: S105 - token type label, not a secret


# --------------------------------------------------------------------------- passwords
class PasswordHasher:
    def __init__(self, *, time_cost: int, memory_cost: int, parallelism: int) -> None:
        self._hash = PasswordHash(
            (Argon2Hasher(time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism),)
        )
        # Verified against when an account does not exist so that login latency
        # does not reveal whether an email is registered.
        self._dummy_hash = self._hash.hash(secrets.token_urlsafe(16))

    def hash(self, password: str) -> str:
        return self._hash.hash(password)

    def verify(self, password: str, hashed: str | None) -> tuple[bool, str | None]:
        """Return ``(valid, updated_hash)``; ``updated_hash`` is set when the stored
        hash uses outdated parameters and should be persisted."""
        if hashed is None:
            self._hash.verify(password, self._dummy_hash)
            return False, None
        try:
            return self._hash.verify_and_update(password, hashed)
        except Exception:
            return False, None

    def burn(self, password: str) -> None:
        self._hash.verify(password, self._dummy_hash)


@lru_cache
def _hasher_for(time_cost: int, memory_cost: int, parallelism: int) -> PasswordHasher:
    return PasswordHasher(time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism)


def get_password_hasher(settings: Settings) -> PasswordHasher:
    return _hasher_for(
        settings.argon2_time_cost, settings.argon2_memory_cost, settings.argon2_parallelism
    )


# --------------------------------------------------------------------------- opaque tokens
def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Opaque tokens have ~256 bits of entropy, so a fast hash is sufficient and
    lets us look them up by digest."""
    return hashlib.sha256(token.encode()).hexdigest()


def constant_time_equals(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode(), b.encode())


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


# --------------------------------------------------------------------------- JWT
@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID
    role: str
    jti: str
    issued_at: int
    expires_at: int


@dataclass(frozen=True, slots=True)
class IssuedAccessToken:
    token: str
    expires_in: int
    claims: AccessTokenClaims


class TokenCodec:
    def __init__(self, settings: Settings) -> None:
        self._key = settings.secret_key.get_secret_value()
        self._algorithm = settings.jwt_algorithm
        self._issuer = settings.jwt_issuer
        self._audience = settings.jwt_audience
        self._ttl = settings.access_token_ttl_seconds

    def issue_access_token(
        self, *, user_id: uuid.UUID, session_id: uuid.UUID, role: str, now: datetime | None = None
    ) -> IssuedAccessToken:
        issued = now or utcnow()
        expires = issued + timedelta(seconds=self._ttl)
        claims = AccessTokenClaims(
            user_id=user_id,
            session_id=session_id,
            role=role,
            jti=uuid.uuid4().hex,
            issued_at=int(issued.timestamp()),
            expires_at=int(expires.timestamp()),
        )
        payload: dict[str, Any] = {
            "sub": str(user_id),
            "sid": str(session_id),
            "role": role,
            "typ": ACCESS_TOKEN_TYPE,
            "jti": claims.jti,
            "iat": claims.issued_at,
            "nbf": claims.issued_at,
            "exp": claims.expires_at,
            "iss": self._issuer,
            "aud": self._audience,
        }
        token = jwt.encode(payload, self._key, algorithm=self._algorithm)
        return IssuedAccessToken(token=token, expires_in=self._ttl, claims=claims)

    def decode_access_token(self, token: str) -> AccessTokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._key,
                algorithms=[self._algorithm],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub", "sid", "jti", "typ"]},
                leeway=5,
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError("Access token has expired.") from exc
        except jwt.PyJWTError as exc:
            raise TokenInvalidError("Access token is invalid.") from exc
        if payload.get("typ") != ACCESS_TOKEN_TYPE:
            raise TokenInvalidError("Unexpected token type.")
        try:
            return AccessTokenClaims(
                user_id=uuid.UUID(payload["sub"]),
                session_id=uuid.UUID(payload["sid"]),
                role=str(payload.get("role", "user")),
                jti=str(payload["jti"]),
                issued_at=int(payload["iat"]),
                expires_at=int(payload["exp"]),
            )
        except (ValueError, TypeError) as exc:
            raise TokenInvalidError("Access token is invalid.") from exc
