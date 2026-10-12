from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.email.base import EmailMessage
from app.integrations.oauth.base import OAuthError, OAuthUserInfo
from app.modules.auth.models import ActionToken, AuditLog, AuthSession, RefreshToken
from app.modules.users.models import User
from tests.integration.conftest import (
    API,
    DEFAULT_PASSWORD,
    Account,
    AccountFactory,
    extract_token,
)

pytestmark = pytest.mark.integration


async def _events(db: AsyncSession, user_id: str) -> list[str]:
    rows = await db.scalars(
        select(AuditLog.event).where(AuditLog.user_id == user_id).order_by(AuditLog.created_at)
    )
    return list(rows)


async def _login(
    client: httpx.AsyncClient, email: str, password: str = DEFAULT_PASSWORD, **extra: object
) -> httpx.Response:
    return await client.post(
        f"{API}/auth/login", json={"email": email, "password": password, **extra}
    )


# --------------------------------------------------------------------------- register
async def test_register_returns_tokens_cookies_and_sends_verification(
    client: httpx.AsyncClient, mails: list[EmailMessage], db: AsyncSession
) -> None:
    response = await client.post(
        f"{API}/auth/register",
        json={
            "email": "Khaled@Example.com",
            "password": "SleepWell42",
            "first_name": "Khaled",
            "last_name": "Saeed",
            "timezone": "Asia/Beirut",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    user = body["user"]
    assert user["email"] == "khaled@example.com"
    assert user["full_name"] == "Khaled Saeed"
    assert user["is_email_verified"] is False
    assert user["has_password"] is True
    assert user["auth_providers"] == ["password"]
    assert user["preferences"]["timezone"] == "Asia/Beirut"
    assert response.headers["cache-control"] == "no-store"

    set_cookies = response.headers.get_list("set-cookie")
    refresh = next(c for c in set_cookies if c.startswith("st_refresh="))
    assert "HttpOnly" in refresh
    assert "Path=/api/v1/auth" in refresh
    assert "SameSite=lax" in refresh
    assert "Max-Age" not in refresh  # session cookie without remember-me
    csrf = next(c for c in set_cookies if c.startswith("st_csrf="))
    assert "HttpOnly" not in csrf
    assert any(c.startswith("st_session=1") for c in set_cookies)

    assert len(mails) == 1
    assert mails[0].to == "khaled@example.com"
    assert "/auth/verify-email?token=" in mails[0].text
    assert await _events(db, user["id"]) == ["register"]


async def test_register_seeds_default_tags(account: Account) -> None:
    response = await account.get(f"{API}/tags")
    assert response.status_code == 200
    names = {tag["name"] for tag in response.json()}
    assert len(names) == 11
    assert {"Caffeine", "Alcohol", "Meditation", "Screen time"} <= names


async def test_register_rejects_duplicate_email_case_insensitively(
    make_account: AccountFactory, client: httpx.AsyncClient
) -> None:
    await make_account("taken@example.com")
    response = await client.post(
        f"{API}/auth/register",
        json={"email": "TAKEN@example.com", "password": "SleepWell42", "first_name": "A"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "email_taken"


@pytest.mark.parametrize(
    ("password", "fragment"),
    [
        ("short1", "between 8 and 128"),
        ("onlyletters", "letter and one digit"),
        ("1234567890", "letter and one digit"),
        ("Password123", "too common"),
        ("x" * 129 + "1", "between 8 and 128"),
    ],
)
async def test_register_password_policy(
    client: httpx.AsyncClient, password: str, fragment: str
) -> None:
    response = await client.post(
        f"{API}/auth/register",
        json={"email": "p@example.com", "password": password, "first_name": "P"},
    )
    assert response.status_code == 422
    error = response.json()["errors"][0]
    assert error["field"] == "password"
    assert fragment in error["message"]


async def test_register_validates_timezone_and_names(client: httpx.AsyncClient) -> None:
    response = await client.post(
        f"{API}/auth/register",
        json={
            "email": "tz@example.com",
            "password": "SleepWell42",
            "first_name": "   ",
            "timezone": "Mars/Olympus",
        },
    )
    assert response.status_code == 422
    fields = {e["field"] for e in response.json()["errors"]}
    assert fields == {"first_name", "timezone"}


async def test_register_closed(
    client: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.state.settings, "registration_open", False)
    response = await client.post(
        f"{API}/auth/register",
        json={"email": "closed@example.com", "password": "SleepWell42", "first_name": "C"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


# --------------------------------------------------------------------------- login
async def test_login_success_with_remember_me(
    account: Account, client: httpx.AsyncClient, db: AsyncSession
) -> None:
    response = await _login(client, account.user["email"], remember_me=True)
    assert response.status_code == 200
    assert response.json()["user"]["last_login_at"] is not None
    refresh = next(
        c for c in response.headers.get_list("set-cookie") if c.startswith("st_refresh=")
    )
    assert "Max-Age=2592000" in refresh or "Max-Age=259199" in refresh
    events = await _events(db, account.id)
    assert events == ["register", "login"]
    log = await db.scalar(select(AuditLog).where(AuditLog.event == "login"))
    assert log is not None
    assert log.event_metadata == {"method": "password", "remember_me": True}
    assert log.ip_address == "203.0.113.10"


async def test_login_wrong_password_is_audited(
    account: Account, client: httpx.AsyncClient, db: AsyncSession
) -> None:
    response = await _login(client, account.user["email"], "WrongPass99")
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"
    assert "login_failed" in await _events(db, account.id)


async def test_login_unknown_email_matches_wrong_password(client: httpx.AsyncClient) -> None:
    response = await _login(client, "nobody@example.com")
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"
    assert response.json()["detail"] == "Incorrect email or password."


async def test_login_disabled_account(
    account: Account, client: httpx.AsyncClient, db: AsyncSession
) -> None:
    await db.execute(update(User).where(User.id == account.id).values(is_active=False))
    await db.commit()
    response = await _login(client, account.user["email"])
    assert response.status_code == 403
    assert response.json()["code"] == "account_disabled"
    # Existing access tokens stop working too.
    me = await account.get(f"{API}/users/me")
    assert me.status_code == 403


async def test_login_requires_verified_email_when_configured(
    account: Account,
    client: httpx.AsyncClient,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    mails: list[EmailMessage],
) -> None:
    monkeypatch.setattr(app.state.settings, "email_verification_required", True)
    response = await _login(client, account.user["email"])
    assert response.status_code == 403
    assert response.json()["code"] == "email_not_verified"

    verify = await client.post(f"{API}/auth/verify-email", json={"token": extract_token(mails[0])})
    assert verify.status_code == 200
    assert (await _login(client, account.user["email"])).status_code == 200


async def test_login_upgrades_outdated_password_hash(
    account: Account, client: httpx.AsyncClient, db: AsyncSession, app: FastAPI
) -> None:
    from app.core.security import PasswordHasher

    weak = PasswordHasher(time_cost=1, memory_cost=2048, parallelism=1)
    await db.execute(
        update(User).where(User.id == account.id).values(password_hash=weak.hash(DEFAULT_PASSWORD))
    )
    await db.commit()
    assert (await _login(client, account.user["email"])).status_code == 200
    db.expire_all()
    stored = await db.scalar(select(User.password_hash).where(User.id == account.id))
    assert stored is not None
    assert "m=1024" in stored


# --------------------------------------------------------------------------- authorization
async def test_protected_endpoint_requires_bearer(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{API}/users/me")
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_invalid_and_expired_access_tokens(
    account: Account, client: httpx.AsyncClient, app: FastAPI
) -> None:
    garbage = await client.get(f"{API}/users/me", headers={"Authorization": "Bearer nope"})
    assert garbage.status_code == 401
    assert garbage.json()["code"] == "token_invalid"

    from app.core.timeutils import utcnow

    codec = app.state.token_codec
    stale = codec.issue_access_token(
        user_id=account.user["id"],
        session_id=account.user["id"],
        role="user",
        now=utcnow() - timedelta(hours=1),
    )
    expired = await client.get(
        f"{API}/users/me", headers={"Authorization": f"Bearer {stale.token}"}
    )
    assert expired.status_code == 401
    assert expired.json()["code"] == "token_expired"


# --------------------------------------------------------------------------- refresh
async def test_refresh_rotates_tokens(account: Account, db: AsyncSession) -> None:
    old_refresh = account.client.cookies.get("st_refresh")
    response = await account.client.post(f"{API}/auth/refresh", headers=account.csrf_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] != account.token
    assert account.client.cookies.get("st_refresh") != old_refresh

    tokens = list(await db.scalars(select(RefreshToken)))
    assert len(tokens) == 2
    assert sum(t.used_at is not None for t in tokens) == 1
    me = await account.client.get(
        f"{API}/users/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200


async def test_refresh_requires_csrf(account: Account) -> None:
    missing = await account.client.post(f"{API}/auth/refresh")
    assert missing.status_code == 403
    assert missing.json()["code"] == "csrf_failed"
    wrong = await account.client.post(f"{API}/auth/refresh", headers={"X-CSRF-Token": "wrong"})
    assert wrong.status_code == 403


async def test_refresh_without_cookie(client: httpx.AsyncClient) -> None:
    client.cookies.set("st_csrf", "abc")
    response = await client.post(f"{API}/auth/refresh", headers={"X-CSRF-Token": "abc"})
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


async def test_refresh_with_unknown_token_clears_cookies(client: httpx.AsyncClient) -> None:
    client.cookies.set("st_csrf", "abc")
    client.cookies.set("st_refresh", "forged-token-value", path="/api/v1/auth")
    response = await client.post(f"{API}/auth/refresh", headers={"X-CSRF-Token": "abc"})
    assert response.status_code == 401
    assert response.json()["code"] == "token_invalid"
    cleared = response.headers.get_list("set-cookie")
    assert any(
        c.startswith('st_refresh=""') or "st_refresh=;" in c or "Max-Age=0" in c for c in cleared
    )


async def test_refresh_reuse_within_grace_does_not_revoke(
    account: Account, db: AsyncSession
) -> None:
    stolen = account.client.cookies.get("st_refresh")
    csrf = account.csrf_headers()
    assert (await account.client.post(f"{API}/auth/refresh", headers=csrf)).status_code == 200

    replay = httpx.AsyncClient(transport=account.client._transport, base_url="http://testserver")
    replay.cookies.set("st_refresh", stolen or "", path="/api/v1/auth")
    replay.cookies.set("st_csrf", "x")
    response = await replay.post(f"{API}/auth/refresh", headers={"X-CSRF-Token": "x"})
    await replay.aclose()
    assert response.status_code == 401
    assert response.json()["code"] == "token_invalid"
    session = await db.scalar(select(AuthSession))
    assert session is not None
    assert session.revoked_at is None


async def test_refresh_reuse_after_grace_revokes_family(
    account: Account, db: AsyncSession, mails: list[EmailMessage]
) -> None:
    stolen = account.client.cookies.get("st_refresh")
    assert (
        await account.client.post(f"{API}/auth/refresh", headers=account.csrf_headers())
    ).status_code == 200
    # Pretend the rotation happened a minute ago.
    await db.execute(
        text(
            "UPDATE refresh_tokens SET used_at = used_at - interval '1 minute' "
            "WHERE used_at IS NOT NULL"
        )
    )
    await db.commit()

    replay = httpx.AsyncClient(transport=account.client._transport, base_url="http://testserver")
    replay.cookies.set("st_refresh", stolen or "", path="/api/v1/auth")
    replay.cookies.set("st_csrf", "x")
    response = await replay.post(f"{API}/auth/refresh", headers={"X-CSRF-Token": "x"})
    await replay.aclose()
    assert response.status_code == 401
    assert "reuse" in response.json()["detail"]

    session = await db.scalar(select(AuthSession))
    assert session is not None
    assert session.revoked_reason == "token_reuse"
    # The legitimate holder is signed out too: the family is gone.
    legit = await account.client.post(f"{API}/auth/refresh", headers=account.csrf_headers())
    assert legit.status_code == 401
    assert "token_reuse_detected" in await _events(db, account.id)
    assert mails[-1].subject == "Security alert for your SleepTrack account"


async def test_refresh_expired_session(account: Account, db: AsyncSession) -> None:
    await db.execute(text("UPDATE auth_sessions SET expires_at = now() - interval '1 minute'"))
    await db.execute(text("UPDATE refresh_tokens SET expires_at = now() - interval '1 minute'"))
    await db.commit()
    response = await account.client.post(f"{API}/auth/refresh", headers=account.csrf_headers())
    assert response.status_code == 401
    assert response.json()["code"] == "token_expired"


# --------------------------------------------------------------------------- logout
async def test_logout_revokes_session_and_access_token(account: Account, db: AsyncSession) -> None:
    response = await account.client.post(f"{API}/auth/logout", headers=account.csrf_headers())
    assert response.status_code == 204
    assert account.client.cookies.get("st_refresh") is None
    me = await account.get(f"{API}/users/me")
    assert me.status_code == 401
    assert me.json()["code"] == "token_invalid"
    assert "logout" in await _events(db, account.id)


async def test_logout_is_idempotent_without_cookie(client: httpx.AsyncClient) -> None:
    client.cookies.set("st_csrf", "abc")
    response = await client.post(f"{API}/auth/logout", headers={"X-CSRF-Token": "abc"})
    assert response.status_code == 204


async def test_logout_requires_csrf(account: Account) -> None:
    response = await account.client.post(f"{API}/auth/logout")
    assert response.status_code == 403


async def test_logout_all_revokes_every_device(
    account: Account, app: FastAPI, db: AsyncSession
) -> None:
    second = httpx.AsyncClient(transport=account.client._transport, base_url="http://testserver")
    login = await _login(second, account.user["email"])
    other_token = login.json()["access_token"]

    response = await account.post(f"{API}/auth/logout-all")
    assert response.status_code == 204
    for token in (account.token, other_token):
        me = await account.client.get(
            f"{API}/users/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me.status_code == 401
    refresh = await second.post(
        f"{API}/auth/refresh", headers={"X-CSRF-Token": second.cookies.get("st_csrf") or ""}
    )
    assert refresh.status_code == 401
    await second.aclose()
    sessions = list(await db.scalars(select(AuthSession)))
    assert all(s.revoked_reason == "logout_all" for s in sessions)


# --------------------------------------------------------------------------- email verification
async def test_verify_email_flow(
    account: Account, client: httpx.AsyncClient, mails: list[EmailMessage], db: AsyncSession
) -> None:
    token = extract_token(mails[0])
    response = await client.post(f"{API}/auth/verify-email", json={"token": token})
    assert response.status_code == 200
    me = await account.get(f"{API}/users/me")
    assert me.json()["is_email_verified"] is True
    assert "email_verified" in await _events(db, account.id)

    again = await client.post(f"{API}/auth/verify-email", json={"token": token})
    assert again.status_code == 400
    assert again.json()["code"] == "token_invalid"


async def test_verify_email_expired_token(
    account: Account, client: httpx.AsyncClient, mails: list[EmailMessage], db: AsyncSession
) -> None:
    await db.execute(update(ActionToken).values(expires_at=ActionToken.created_at))
    await db.commit()
    response = await client.post(
        f"{API}/auth/verify-email", json={"token": extract_token(mails[0])}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "token_expired"


async def test_verify_email_unknown_token(client: httpx.AsyncClient) -> None:
    response = await client.post(f"{API}/auth/verify-email", json={"token": "x" * 40})
    assert response.status_code == 400
    assert response.json()["code"] == "token_invalid"


async def test_resend_verification_replaces_previous_link(
    account: Account, client: httpx.AsyncClient, mails: list[EmailMessage]
) -> None:
    first = extract_token(mails[0])
    response = await account.post(f"{API}/auth/verify-email/resend")
    assert response.status_code == 202
    assert len(mails) == 2
    second = extract_token(mails[1])
    assert first != second
    assert (await client.post(f"{API}/auth/verify-email", json={"token": first})).status_code == 400
    assert (
        await client.post(f"{API}/auth/verify-email", json={"token": second})
    ).status_code == 200
    # Already verified: no further email.
    assert (await account.post(f"{API}/auth/verify-email/resend")).status_code == 202
    assert len(mails) == 2


# --------------------------------------------------------------------------- password reset
async def test_forgot_password_unknown_email_is_silent(
    client: httpx.AsyncClient, mails: list[EmailMessage]
) -> None:
    response = await client.post(f"{API}/auth/forgot-password", json={"email": "ghost@example.com"})
    assert response.status_code == 202
    assert mails == []


async def test_password_reset_flow(
    account: Account, client: httpx.AsyncClient, mails: list[EmailMessage], db: AsyncSession
) -> None:
    response = await client.post(
        f"{API}/auth/forgot-password", json={"email": account.user["email"].upper()}
    )
    assert response.status_code == 202
    reset_mail = mails[-1]
    assert reset_mail.subject == "Reset your SleepTrack password"
    token = extract_token(reset_mail)

    reset = await client.post(
        f"{API}/auth/reset-password", json={"token": token, "password": "BrandNew2026x"}
    )
    assert reset.status_code == 200
    assert mails[-1].subject == "Your SleepTrack password was changed"

    # All sessions are revoked, the old password no longer works, the new one does.
    assert (await account.get(f"{API}/users/me")).status_code == 401
    assert (await _login(client, account.user["email"])).status_code == 401
    assert (await _login(client, account.user["email"], "BrandNew2026x")).status_code == 200
    # Single use.
    again = await client.post(
        f"{API}/auth/reset-password", json={"token": token, "password": "Another2026x"}
    )
    assert again.status_code == 400
    events = await _events(db, account.id)
    assert {"password_reset_requested", "password_reset"} <= set(events)
    verified = await db.scalar(select(User.is_email_verified).where(User.id == account.id))
    assert verified is True


async def test_new_reset_request_invalidates_previous_token(
    account: Account, client: httpx.AsyncClient, mails: list[EmailMessage]
) -> None:
    for _ in range(2):
        await client.post(f"{API}/auth/forgot-password", json={"email": account.user["email"]})
    first, second = extract_token(mails[-2]), extract_token(mails[-1])
    stale = await client.post(
        f"{API}/auth/reset-password", json={"token": first, "password": "BrandNew2026x"}
    )
    assert stale.status_code == 400
    fresh = await client.post(
        f"{API}/auth/reset-password", json={"token": second, "password": "BrandNew2026x"}
    )
    assert fresh.status_code == 200


async def test_reset_password_enforces_policy(client: httpx.AsyncClient) -> None:
    response = await client.post(
        f"{API}/auth/reset-password", json={"token": "x" * 40, "password": "weak"}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- demo
async def test_demo_account(client: httpx.AsyncClient, db: AsyncSession) -> None:
    response = await client.post(f"{API}/auth/demo", json={"timezone": "Europe/Paris"})
    assert response.status_code == 200
    body = response.json()
    user = body["user"]
    assert user["is_demo"] is True
    assert user["is_email_verified"] is True
    assert user["has_password"] is False
    assert user["preferences"]["timezone"] == "Europe/Paris"

    headers = {"Authorization": f"Bearer {body['access_token']}"}
    sessions = await client.get(f"{API}/sleep/sessions?page_size=1", headers=headers)
    assert 70 <= sessions.json()["total"] <= 90
    overview = await client.get(f"{API}/analytics/overview", headers=headers)
    assert overview.json()["nights_tracked"] > 20

    blocked = await client.post(
        f"{API}/users/me/change-password",
        headers=headers,
        json={"current_password": None, "new_password": "SleepWell42"},
    )
    assert blocked.status_code == 403


async def test_demo_without_body_defaults_to_utc(client: httpx.AsyncClient) -> None:
    response = await client.post(f"{API}/auth/demo")
    assert response.status_code == 200
    assert response.json()["user"]["preferences"]["timezone"] == "UTC"


async def test_demo_disabled(
    client: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.state.settings, "demo_mode_enabled", False)
    response = await client.post(f"{API}/auth/demo")
    assert response.status_code == 403


# --------------------------------------------------------------------------- rate limiting
async def test_login_rate_limited_per_email(
    account: Account, client: httpx.AsyncClient, app: FastAPI
) -> None:
    app.state.settings.rate_limit_enabled = True
    statuses = [
        (await _login(client, account.user["email"], "WrongPass99")).status_code for _ in range(6)
    ]
    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429
    limited = await _login(client, account.user["email"], "WrongPass99")
    assert limited.json()["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1


async def test_login_rate_limited_per_ip(client: httpx.AsyncClient, app: FastAPI) -> None:
    app.state.settings.rate_limit_enabled = True
    statuses = [
        (await _login(client, f"user{i}@example.com", "WrongPass99")).status_code for i in range(11)
    ]
    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429


async def test_register_rate_limited_per_ip(client: httpx.AsyncClient, app: FastAPI) -> None:
    app.state.settings.rate_limit_enabled = True
    statuses = []
    for i in range(6):
        response = await client.post(
            f"{API}/auth/register",
            json={"email": f"r{i}@example.com", "password": "SleepWell42", "first_name": "R"},
        )
        statuses.append(response.status_code)
    assert statuses == [201] * 5 + [429]


async def test_forgot_password_rate_limited_per_email(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    app.state.settings.rate_limit_enabled = True
    statuses = [
        (
            await client.post(f"{API}/auth/forgot-password", json={"email": "x@example.com"})
        ).status_code
        for _ in range(4)
    ]
    assert statuses == [202, 202, 202, 429]


# --------------------------------------------------------------------------- OAuth
class FakeGoogle:
    id = "google"
    name = "Google"
    enabled = True

    def __init__(self, info: OAuthUserInfo | None = None, error: OAuthError | None = None) -> None:
        self.info = info
        self.error = error
        self.calls: list[dict[str, str]] = []

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return f"https://accounts.example/auth?state={state}&challenge={code_challenge}&redirect_uri={redirect_uri}"

    async def fetch_user(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthUserInfo:
        self.calls.append({"code": code, "verifier": code_verifier, "redirect_uri": redirect_uri})
        if self.error:
            raise self.error
        assert self.info is not None
        return self.info


def _google_info(email: str = "oauth@example.com", *, verified: bool = True) -> OAuthUserInfo:
    return OAuthUserInfo(
        provider="google",
        account_id="google-sub-123",
        email=email,
        email_verified=verified,
        first_name="Olivia",
        last_name="Auth",
        avatar_url="https://example.com/a.png",
    )


async def _oauth_roundtrip(
    client: httpx.AsyncClient, *, tamper_state: bool = False
) -> httpx.Response:
    start = await client.get(f"{API}/auth/oauth/google/authorize?redirect_to=/sleep")
    assert start.status_code == 302
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    if tamper_state:
        state = state + "x"
    return await client.get(f"{API}/auth/oauth/google/callback?code=abc&state={state}")


async def test_oauth_providers_listing(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{API}/auth/oauth/providers")
    assert response.json() == [{"id": "google", "name": "Google", "enabled": False}]


async def test_oauth_disabled_and_unknown_provider(client: httpx.AsyncClient) -> None:
    disabled = await client.get(f"{API}/auth/oauth/google/authorize")
    assert disabled.status_code == 400
    assert disabled.json()["code"] == "provider_disabled"
    unknown = await client.get(f"{API}/auth/oauth/github/authorize")
    assert unknown.status_code == 404


async def test_oauth_signup_flow(
    client: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch, db: AsyncSession
) -> None:
    provider = FakeGoogle(_google_info())
    monkeypatch.setitem(app.state.oauth._providers, "google", provider)

    response = await _oauth_roundtrip(client)
    assert response.status_code == 302
    assert response.headers["location"] == "http://localhost:3000/auth/callback?next=%2Fsleep"
    assert provider.calls[0]["redirect_uri"] == (
        "http://localhost:3000/api/v1/auth/oauth/google/callback"
    )
    assert len(provider.calls[0]["verifier"]) >= 43

    refreshed = await client.post(
        f"{API}/auth/refresh", headers={"X-CSRF-Token": client.cookies.get("st_csrf") or ""}
    )
    assert refreshed.status_code == 200
    user = refreshed.json()["user"]
    assert user["email"] == "oauth@example.com"
    assert user["auth_providers"] == ["google"]
    assert user["has_password"] is False
    assert user["is_email_verified"] is True

    # Second sign-in reuses the linked account.
    again = await _oauth_roundtrip(client)
    assert again.status_code == 302
    count = await db.scalar(text("SELECT count(*) FROM users"))
    assert count == 1


async def test_oauth_links_existing_verified_account(
    account: Account,
    client: httpx.AsyncClient,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        app.state.oauth._providers, "google", FakeGoogle(_google_info(account.user["email"]))
    )
    response = await _oauth_roundtrip(client)
    assert "next=" in response.headers["location"]
    me = await account.get(f"{API}/users/me")
    assert me.json()["auth_providers"] == ["google"]  # unverified password was discarded


async def test_oauth_rejects_state_mismatch_and_replay(
    client: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(app.state.oauth._providers, "google", FakeGoogle(_google_info()))
    response = await _oauth_roundtrip(client, tamper_state=True)
    assert response.headers["location"].endswith("?error=state_mismatch")

    start = await client.get(f"{API}/auth/oauth/google/authorize")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    first = await client.get(f"{API}/auth/oauth/google/callback?code=abc&state={state}")
    assert "next=" in first.headers["location"]
    client.cookies.set("st_oauth_state", state, path="/api/v1/auth/oauth")
    replay = await client.get(f"{API}/auth/oauth/google/callback?code=abc&state={state}")
    assert replay.headers["location"].endswith("?error=state_expired")


@pytest.mark.parametrize(
    ("query", "expected"),
    [("error=access_denied", "access_denied"), ("code=abc", "invalid_request")],
)
async def test_oauth_callback_errors(
    client: httpx.AsyncClient,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    expected: str,
) -> None:
    monkeypatch.setitem(app.state.oauth._providers, "google", FakeGoogle(_google_info()))
    response = await client.get(f"{API}/auth/oauth/google/callback?{query}")
    assert response.status_code == 302
    assert response.headers["location"].endswith(f"?error={expected}")


async def test_oauth_provider_failure_and_unverified_email(
    client: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        app.state.oauth._providers,
        "google",
        FakeGoogle(error=OAuthError("exchange_failed", "nope")),
    )
    response = await _oauth_roundtrip(client)
    assert response.headers["location"].endswith("?error=exchange_failed")

    monkeypatch.setitem(
        app.state.oauth._providers, "google", FakeGoogle(_google_info(verified=False))
    )
    response = await _oauth_roundtrip(client)
    assert response.headers["location"].endswith("?error=email_unverified")


async def test_oauth_open_redirect_is_neutralised(
    client: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(app.state.oauth._providers, "google", FakeGoogle(_google_info()))
    start = await client.get(f"{API}/auth/oauth/google/authorize?redirect_to=//evil.example/steal")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    response = await client.get(f"{API}/auth/oauth/google/callback?code=abc&state={state}")
    assert response.headers["location"].endswith("next=%2Fdashboard")
