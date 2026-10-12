"""OAuth provider abstraction (authorization-code flow with PKCE)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class OAuthUserInfo:
    provider: str
    account_id: str
    email: str
    email_verified: bool
    first_name: str
    last_name: str
    avatar_url: str | None = None


class OAuthError(Exception):
    """Raised when the provider rejects an exchange or returns unusable data."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class OAuthProvider(Protocol):
    id: str
    name: str

    @property
    def enabled(self) -> bool: ...

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str: ...

    async def fetch_user(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthUserInfo: ...
