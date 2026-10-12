"""Google OpenID Connect provider."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from app.core.config import Settings
from app.integrations.oauth.base import OAuthError, OAuthUserInfo

logger = structlog.get_logger(__name__)

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - endpoint URL
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _json(response: httpx.Response, code: str) -> dict[str, Any]:
    """Decode a JSON object body, treating anything else as unusable provider data."""
    try:
        data = response.json()
    except ValueError as exc:
        raise OAuthError(code, "Google returned an unexpected response.") from exc
    if not isinstance(data, dict):
        raise OAuthError(code, "Google returned an unexpected response.")
    return data


class GoogleOAuthProvider:
    id = "google"
    name = "Google"

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._client_id = settings.google_client_id
        self._client_secret = (
            settings.google_client_secret.get_secret_value()
            if settings.google_client_secret
            else None
        )
        self._transport = transport

    @property
    def enabled(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "online",
            "prompt": "select_account",
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    async def fetch_user(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthUserInfo:
        async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
            try:
                token_response = await client.post(
                    TOKEN_URL,
                    data={
                        "code": code,
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "redirect_uri": redirect_uri,
                        "grant_type": "authorization_code",
                        "code_verifier": code_verifier,
                    },
                    headers={"Accept": "application/json"},
                )
                if token_response.status_code != 200:
                    logger.warning("oauth_token_exchange_failed", status=token_response.status_code)
                    raise OAuthError(
                        "exchange_failed", "Could not exchange the authorization code."
                    )
                access_token = _json(token_response, "exchange_failed").get("access_token")
                if not access_token:
                    raise OAuthError("exchange_failed", "Provider returned no access token.")
                userinfo_response = await client.get(
                    USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
                )
            except httpx.HTTPError as exc:
                raise OAuthError("provider_unavailable", "Google is unreachable.") from exc
        if userinfo_response.status_code != 200:
            raise OAuthError("userinfo_failed", "Could not load the Google profile.")
        return self._parse_user(_json(userinfo_response, "userinfo_failed"))

    def _parse_user(self, data: dict[str, Any]) -> OAuthUserInfo:
        subject = data.get("sub")
        email = data.get("email")
        if not subject or not email:
            raise OAuthError("userinfo_failed", "Google profile is missing an email address.")
        return OAuthUserInfo(
            provider=self.id,
            account_id=str(subject),
            email=str(email).lower(),
            email_verified=bool(data.get("email_verified")),
            first_name=str(data.get("given_name") or data.get("name") or "SleepTrack")[:100],
            last_name=str(data.get("family_name") or "")[:100],
            avatar_url=data.get("picture"),
        )
