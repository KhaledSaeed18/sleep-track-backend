"""Fixtures for tests that exercise the HTTP API against Postgres and Redis."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import Base
from app.integrations.email.base import EmailMessage
from app.integrations.email.providers import MemoryEmailProvider
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PASSWORD = "SleepWell42"
API = "/api/v1"


def _asyncpg_dsn(url: str, database: str | None = None) -> str:
    parsed = make_url(url).set(drivername="postgresql")
    if database is not None:
        parsed = parsed.set(database=database)
    return parsed.render_as_string(hide_password=False)


async def _recreate_database(url: str) -> None:
    name = make_url(url).database
    assert name, "database name missing"
    assert name.endswith("_test"), "refusing to touch a non-test database"
    conn = await asyncpg.connect(_asyncpg_dsn(url, "postgres"))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


def _migrate(url: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture(scope="session")
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    await _recreate_database(settings.database_url)
    # Alembic's env runs its own event loop, so migrate from a worker thread.
    await asyncio.to_thread(_migrate, settings.database_url)
    application = create_app(settings)
    async with LifespanManager(application):
        yield application


@pytest.fixture(autouse=True)
async def _clean_state(app: FastAPI) -> AsyncIterator[None]:
    yield
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    async with app.state.engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await app.state.redis.flushdb()
    outbox(app).clear()
    app.state.settings.rate_limit_enabled = False


def outbox(app: FastAPI) -> list[EmailMessage]:
    provider = app.state.email_sender.provider
    assert isinstance(provider, MemoryEmailProvider)
    return provider.outbox


@pytest.fixture
def mails(app: FastAPI) -> list[EmailMessage]:
    return outbox(app)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 51000))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest.fixture
async def db(app: FastAPI) -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


def extract_token(message: EmailMessage) -> str:
    match = re.search(r"token=([A-Za-z0-9_\-]+)", message.text)
    assert match, message.text
    return match.group(1)


@dataclass
class Account:
    """An authenticated test user with its own cookie jar."""

    client: httpx.AsyncClient
    token: str
    user: dict[str, Any]
    password: str = DEFAULT_PASSWORD
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @property
    def id(self) -> str:
        return str(self.user["id"])

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.client.get(url, headers=self.headers, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.client.post(url, headers=self.headers, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.client.patch(url, headers=self.headers, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.client.put(url, headers=self.headers, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.client.request("DELETE", url, headers=self.headers, **kwargs)

    def csrf_headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.client.cookies.get("st_csrf") or ""}


AccountFactory = Callable[..., Awaitable[Account]]


@pytest.fixture
async def make_account(app: FastAPI) -> AsyncIterator[AccountFactory]:
    clients: list[httpx.AsyncClient] = []
    counter = 0

    async def factory(
        email: str | None = None,
        password: str = DEFAULT_PASSWORD,
        *,
        first_name: str = "Test",
        timezone: str = "UTC",
        admin: bool = False,
    ) -> Account:
        nonlocal counter
        counter += 1
        transport = httpx.ASGITransport(app=app, client=(f"198.51.100.{counter}", 50000 + counter))
        http = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        clients.append(http)
        response = await http.post(
            f"{API}/auth/register",
            json={
                "email": email or f"user{counter}@example.com",
                "password": password,
                "first_name": first_name,
                "last_name": "User",
                "timezone": timezone,
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        account = Account(http, body["access_token"], body["user"], password)
        if admin:
            async with app.state.session_factory() as session:
                await session.execute(
                    text("UPDATE users SET role = 'admin' WHERE id = :id"), {"id": account.id}
                )
                await session.commit()
            account.user["role"] = "admin"
        return account

    yield factory
    for http in clients:
        await http.aclose()


@pytest.fixture
async def account(make_account: AccountFactory) -> Account:
    return await make_account()
