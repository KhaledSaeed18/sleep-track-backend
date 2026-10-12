"""Access-token revocation backed by Redis.

Access tokens are stateless JWTs bound to a device session (``sid`` claim).
Revoking a session writes ``auth:revoked:sid:<session>`` with a TTL equal to
the access-token lifetime: after that, every token minted for the session has
expired anyway and refreshing is blocked by the database. Signing out
everywhere simply revokes each active session, which is exact and avoids
clock-granularity races of "issued-before" timestamps.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.security import AccessTokenClaims

logger = structlog.get_logger(__name__)


class TokenRevocationStore:
    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        self._redis = redis
        # Headroom over the token TTL covers JWT leeway and clock skew.
        self._ttl = ttl_seconds + 60

    @staticmethod
    def _key(session_id: uuid.UUID) -> str:
        return f"auth:revoked:sid:{session_id}"

    async def revoke_sessions(self, session_ids: Iterable[uuid.UUID]) -> None:
        ids = list(session_ids)
        if not ids:
            return
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                for session_id in ids:
                    pipe.set(self._key(session_id), "1", ex=self._ttl)
                await pipe.execute()
        except RedisError:
            logger.warning("revocation_store_unavailable", op="revoke")

    async def is_revoked(self, claims: AccessTokenClaims) -> bool:
        try:
            return bool(await self._redis.exists(self._key(claims.session_id)))
        except RedisError:
            # Fail open: access tokens are short-lived and refreshes are still
            # enforced by the database.
            logger.warning("revocation_store_unavailable", op="check")
            return False
