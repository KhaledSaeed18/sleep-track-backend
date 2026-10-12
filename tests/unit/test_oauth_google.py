from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.security import pkce_challenge
from app.integrations.oauth.base import OAuthError, OAuthUserInfo
from app.integrations.oauth.google import (
    AUTHORIZE_URL,
    TOKEN_URL,
    USERINFO_URL,
    GoogleOAuthProvider,
)
from app.integrations.oauth.registry import OAuthRegistry, build_oauth_registry

REDIRECT_URI = "https://app.example.com/auth/callback/google"
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"

Handler = Callable[[httpx.Request], httpx.Response]


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "google_client_id": "client-123.apps.googleusercontent.com",
        "google_client_secret": SecretStr("google-secret"),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def provider_with(handler: Handler, **overrides: Any) -> GoogleOAuthProvider:
    return GoogleOAuthProvider(make_settings(**overrides), transport=httpx.MockTransport(handler))


class FakeGoogle:
    """Records requests and answers the token and userinfo endpoints."""

    def __init__(
        self,
        *,
        token: httpx.Response | None = None,
        userinfo: httpx.Response | None = None,
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.token = token or httpx.Response(
            200, json={"access_token": "ya29.token", "token_type": "Bearer"}
        )
        self.userinfo = userinfo or httpx.Response(
            200,
            json={
                "sub": "1234567890",
                "email": "Ada.Lovelace@Example.com",
                "email_verified": True,
                "given_name": "Ada",
                "family_name": "Lovelace",
                "name": "Ada Lovelace",
                "picture": "https://lh3.googleusercontent.com/a/photo",
            },
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == TOKEN_URL:
            return self.token
        if str(request.url) == USERINFO_URL:
            return self.userinfo
        return httpx.Response(404)


async def fetch(provider: GoogleOAuthProvider) -> OAuthUserInfo:
    return await provider.fetch_user(
        code="auth-code", code_verifier=VERIFIER, redirect_uri=REDIRECT_URI
    )


# =============================================================================== authorization
def test_authorization_url() -> None:
    provider = GoogleOAuthProvider(make_settings())
    challenge = pkce_challenge(VERIFIER)
    url = provider.authorization_url(
        state="state-xyz", code_challenge=challenge, redirect_uri=REDIRECT_URI
    )

    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == AUTHORIZE_URL
    params = {key: values[0] for key, values in parse_qs(parts.query).items()}
    assert params == {
        "client_id": "client-123.apps.googleusercontent.com",
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": "openid email profile",
        "state": "state-xyz",
        "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        "code_challenge_method": "S256",
        "access_type": "online",
        "prompt": "select_account",
    }


def test_authorization_url_escapes_values() -> None:
    url = GoogleOAuthProvider(make_settings()).authorization_url(
        state="a b&c=d", code_challenge="x", redirect_uri=REDIRECT_URI
    )
    assert "state=a+b%26c%3Dd" in url
    assert parse_qs(urlsplit(url).query)["state"] == ["a b&c=d"]


@pytest.mark.parametrize(
    ("client_id", "client_secret", "enabled"),
    [
        ("client", SecretStr("secret"), True),
        ("client", None, False),
        (None, SecretStr("secret"), False),
        ("", SecretStr("secret"), False),
        ("client", SecretStr(""), False),
    ],
)
def test_enabled(client_id: str | None, client_secret: SecretStr | None, enabled: bool) -> None:
    settings = make_settings(google_client_id=client_id, google_client_secret=client_secret)
    assert GoogleOAuthProvider(settings).enabled is enabled


# =============================================================================== fetch_user
async def test_fetch_user_success() -> None:
    google = FakeGoogle()
    user = await fetch(provider_with(google))

    assert user == OAuthUserInfo(
        provider="google",
        account_id="1234567890",
        email="ada.lovelace@example.com",
        email_verified=True,
        first_name="Ada",
        last_name="Lovelace",
        avatar_url="https://lh3.googleusercontent.com/a/photo",
    )

    token_request, userinfo_request = google.requests
    assert token_request.method == "POST"
    assert token_request.headers["accept"] == "application/json"
    form = {key: values[0] for key, values in parse_qs(token_request.content.decode()).items()}
    assert form == {
        "code": "auth-code",
        "client_id": "client-123.apps.googleusercontent.com",
        "client_secret": "google-secret",
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
        "code_verifier": VERIFIER,
    }
    assert userinfo_request.method == "GET"
    assert userinfo_request.headers["authorization"] == "Bearer ya29.token"


async def test_fetch_user_minimal_profile() -> None:
    google = FakeGoogle(userinfo=httpx.Response(200, json={"sub": 42, "email": "x@example.com"}))
    user = await fetch(provider_with(google))
    assert user.account_id == "42"
    assert user.email_verified is False
    assert user.first_name == "SleepTrack"
    assert user.last_name == ""
    assert user.avatar_url is None


async def test_fetch_user_falls_back_to_full_name_and_truncates() -> None:
    google = FakeGoogle(
        userinfo=httpx.Response(
            200,
            json={
                "sub": "1",
                "email": "x@example.com",
                "name": "N" * 150,
                "family_name": "F" * 150,
            },
        )
    )
    user = await fetch(provider_with(google))
    assert user.first_name == "N" * 100
    assert user.last_name == "F" * 100


@pytest.mark.parametrize("status", [400, 401, 500])
async def test_token_endpoint_failure(status: int) -> None:
    google = FakeGoogle(token=httpx.Response(status, json={"error": "invalid_grant"}))
    with pytest.raises(OAuthError) as caught:
        await fetch(provider_with(google))
    assert caught.value.code == "exchange_failed"
    assert str(caught.value) == "Could not exchange the authorization code."
    assert len(google.requests) == 1


@pytest.mark.parametrize("body", [{}, {"access_token": ""}, {"access_token": None}])
async def test_missing_access_token(body: dict[str, Any]) -> None:
    google = FakeGoogle(token=httpx.Response(200, json=body))
    with pytest.raises(OAuthError) as caught:
        await fetch(provider_with(google))
    assert caught.value.code == "exchange_failed"
    assert str(caught.value) == "Provider returned no access token."


@pytest.mark.parametrize("status", [401, 403, 503])
async def test_userinfo_failure(status: int) -> None:
    google = FakeGoogle(userinfo=httpx.Response(status))
    with pytest.raises(OAuthError) as caught:
        await fetch(provider_with(google))
    assert caught.value.code == "userinfo_failed"


@pytest.mark.parametrize(
    "profile",
    [{"sub": "1"}, {"sub": "1", "email": ""}, {"email": "x@example.com"}, {}],
)
async def test_incomplete_profile(profile: dict[str, Any]) -> None:
    google = FakeGoogle(userinfo=httpx.Response(200, json=profile))
    with pytest.raises(OAuthError) as caught:
        await fetch(provider_with(google))
    assert caught.value.code == "userinfo_failed"


@pytest.mark.parametrize("failing_url", [TOKEN_URL, USERINFO_URL])
async def test_network_error(failing_url: str) -> None:
    google = FakeGoogle()

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == failing_url:
            raise httpx.ConnectError("connection refused", request=request)
        return google(request)

    with pytest.raises(OAuthError) as caught:
        await fetch(provider_with(handler))
    assert caught.value.code == "provider_unavailable"
    assert isinstance(caught.value.__cause__, httpx.ConnectError)


async def test_timeout_is_provider_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(OAuthError) as caught:
        await fetch(provider_with(handler))
    assert caught.value.code == "provider_unavailable"


@pytest.mark.parametrize("endpoint", ["token", "userinfo"])
async def test_non_json_response_is_an_oauth_error(endpoint: str) -> None:
    html = httpx.Response(200, text="<html>Service temporarily unavailable</html>")
    google = FakeGoogle(**{endpoint: html})
    with pytest.raises(OAuthError):
        await fetch(provider_with(google))


# =============================================================================== registry
def test_build_oauth_registry() -> None:
    registry = build_oauth_registry(make_settings())
    google = registry.get("google")
    assert isinstance(google, GoogleOAuthProvider)
    assert google.enabled
    assert registry.get("github") is None
    assert registry.all() == [google]


def test_registry_register_replaces_by_id() -> None:
    first = GoogleOAuthProvider(make_settings())
    second = GoogleOAuthProvider(make_settings(google_client_id=None))
    registry = OAuthRegistry([first])
    registry.register(second)
    assert registry.get("google") is second
    assert registry.all() == [second]


def test_empty_registry() -> None:
    registry = OAuthRegistry([])
    assert registry.all() == []
    assert registry.get("google") is None
