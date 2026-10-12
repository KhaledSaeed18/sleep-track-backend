from __future__ import annotations

from collections.abc import AsyncIterator

import fakeredis
import pytest


@pytest.fixture
async def redis() -> AsyncIterator[fakeredis.FakeAsyncRedis]:
    client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def broken_redis() -> AsyncIterator[fakeredis.FakeAsyncRedis]:
    """A client whose every command fails with ``redis.exceptions.ConnectionError``."""
    server = fakeredis.FakeServer()
    server.connected = False
    client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    yield client
    await client.aclose()
