from __future__ import annotations

import uuid

import fakeredis
import pytest

from app.core.security import AccessTokenClaims
from app.modules.auth.devices import describe_user_agent
from app.modules.auth.revocation import TokenRevocationStore


def claims_for(session_id: uuid.UUID) -> AccessTokenClaims:
    return AccessTokenClaims(
        user_id=uuid.uuid4(),
        session_id=session_id,
        role="user",
        jti=uuid.uuid4().hex,
        issued_at=1_800_000_000,
        expires_at=1_800_000_900,
    )


# =============================================================================== revocation
class TestTokenRevocationStore:
    async def test_revoked_sessions(self, redis: fakeredis.FakeAsyncRedis) -> None:
        store = TokenRevocationStore(redis, ttl_seconds=900)
        first, second, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        await store.revoke_sessions([first, second])

        assert await store.is_revoked(claims_for(first))
        assert await store.is_revoked(claims_for(second))
        assert not await store.is_revoked(claims_for(other))

    async def test_ttl_has_headroom_over_token_lifetime(
        self, redis: fakeredis.FakeAsyncRedis
    ) -> None:
        session_id = uuid.uuid4()
        await TokenRevocationStore(redis, ttl_seconds=900).revoke_sessions([session_id])
        key = f"auth:revoked:sid:{session_id}"
        assert await redis.get(key) == "1"
        assert 900 < await redis.ttl(key) <= 960

    async def test_accepts_any_iterable(self, redis: fakeredis.FakeAsyncRedis) -> None:
        ids = [uuid.uuid4(), uuid.uuid4()]
        store = TokenRevocationStore(redis, ttl_seconds=60)
        await store.revoke_sessions(iter(ids))
        assert all([await store.is_revoked(claims_for(i)) for i in ids])

    async def test_empty_is_a_no_op(self, redis: fakeredis.FakeAsyncRedis) -> None:
        await TokenRevocationStore(redis, ttl_seconds=900).revoke_sessions([])
        assert await redis.dbsize() == 0

    async def test_unavailable_redis_fails_open(
        self, broken_redis: fakeredis.FakeAsyncRedis
    ) -> None:
        store = TokenRevocationStore(broken_redis, ttl_seconds=900)
        session_id = uuid.uuid4()
        await store.revoke_sessions([session_id])
        assert await store.is_revoked(claims_for(session_id)) is False


# =============================================================================== devices
CHROME_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
EDGE_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36 Edg/129.0.2792.79"
)
OPERA_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36 OPR/114.0.0.0"
)
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)
CHROME_IPAD = (
    "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) CriOS/129.0.6668.69 Mobile/15E148 Safari/604.1"
)
CHROME_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Mobile Safari/537.36"
)
CHROME_OS = (
    "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36"
)


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [
        (CHROME_MAC, "Chrome on macOS"),
        (EDGE_WINDOWS, "Edge on Windows"),
        (OPERA_WINDOWS, "Opera on Windows"),
        (FIREFOX_LINUX, "Firefox on Linux"),
        (SAFARI_IPHONE, "Safari on iPhone"),
        (CHROME_IPAD, "Chrome on iPad"),
        (CHROME_ANDROID, "Chrome on Android"),
        (CHROME_OS, "Chrome on ChromeOS"),
        ("curl/8.7.1", "curl"),
        ("python-httpx/0.28.1", "HTTP client"),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Windows"),
        ("SomeBot/1.0", "Unknown device"),
        (None, None),
        ("", None),
    ],
)
def test_describe_user_agent(user_agent: str | None, expected: str | None) -> None:
    assert describe_user_agent(user_agent) == expected
