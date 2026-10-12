from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.email.base import EmailMessage
from app.modules.auth.models import ActionToken, AuthSession
from app.modules.users.models import User
from app.worker import tasks
from app.worker.context import RESOURCES_KEY, JobResources
from app.worker.queue import EagerJobQueue
from app.worker.settings import WorkerSettings
from tests.integration.conftest import API, Account, AccountFactory
from tests.integration.test_sleep import create

pytestmark = pytest.mark.integration


@pytest.fixture
def ctx(app: FastAPI) -> dict[str, Any]:
    queue = app.state.job_queue
    assert isinstance(queue, EagerJobQueue)
    resources: JobResources = queue._ctx[RESOURCES_KEY]
    return {RESOURCES_KEY: resources}


async def test_worker_settings_registers_jobs() -> None:
    names = {getattr(f, "__name__", "") for f in WorkerSettings.functions}
    assert names == set(tasks.JOB_FUNCTIONS)
    assert len(WorkerSettings.cron_jobs) == 3


async def test_cleanup_demo_accounts(
    ctx: dict[str, Any], app: FastAPI, db: AsyncSession, account: Account
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        for _ in range(2):
            assert (await http.post(f"{API}/auth/demo")).status_code == 200
    await db.execute(
        text(
            "UPDATE users SET created_at = now() - interval '25 hours' "
            "WHERE id = (SELECT id FROM users WHERE is_demo ORDER BY created_at LIMIT 1)"
        )
    )
    await db.commit()

    deleted = await tasks.cleanup_demo_accounts(ctx)
    assert deleted == 1
    remaining = await db.scalar(select(func.count()).select_from(User).where(User.is_demo))
    assert remaining == 1
    # Regular accounts are never touched, however old.
    await db.execute(
        text("UPDATE users SET created_at = now() - interval '400 days' WHERE NOT is_demo")
    )
    await db.commit()
    assert await tasks.cleanup_demo_accounts(ctx) == 0


async def test_cleanup_expired_tokens(
    ctx: dict[str, Any], account: Account, db: AsyncSession
) -> None:
    await account.client.post(f"{API}/auth/logout", headers=account.csrf_headers())
    await db.execute(text("UPDATE auth_sessions SET revoked_at = now() - interval '8 days'"))
    await db.execute(text("UPDATE action_tokens SET expires_at = now() - interval '8 days'"))
    await db.commit()

    result = await tasks.cleanup_expired_tokens(ctx)
    assert result == {"sessions": 1, "action_tokens": 1}
    assert await db.scalar(select(func.count()).select_from(AuthSession)) == 0
    assert await db.scalar(select(func.count()).select_from(ActionToken)) == 0


async def test_send_password_reset_skips_unknown_and_demo(
    ctx: dict[str, Any], mails: list[EmailMessage]
) -> None:
    assert await tasks.send_password_reset(ctx, email="nobody@example.com") is False
    assert mails == []


async def test_send_weekly_report_skips_missing_user(ctx: dict[str, Any]) -> None:
    result = await tasks.send_weekly_report(
        ctx, user_id="00000000-0000-0000-0000-000000000000", week_start="2026-06-01"
    )
    assert result is False


async def _verify(db: AsyncSession, account: Account) -> None:
    await db.execute(
        text("UPDATE users SET is_email_verified = true WHERE id = :id"), {"id": account.id}
    )
    await db.commit()


async def test_dispatch_weekly_reports_in_user_timezone(
    ctx: dict[str, Any],
    make_account: AccountFactory,
    db: AsyncSession,
    mails: list[EmailMessage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    beirut = await make_account("beirut@example.com", timezone="Asia/Beirut")
    tokyo = await make_account("tokyo@example.com", timezone="Asia/Tokyo")
    opted_out = await make_account("quiet@example.com", timezone="Asia/Beirut")
    unverified = await make_account("new@example.com", timezone="Asia/Beirut")
    for acc in (beirut, tokyo, opted_out):
        await _verify(db, acc)
    await opted_out.patch(f"{API}/users/me/preferences", json={"email_weekly_report": False})
    await create(beirut, "2026-10-05T20:00:00Z", "2026-10-06T04:00:00Z", quality=4)
    mails.clear()

    # Monday 2026-10-12 05:30 UTC is 08:30 in Beirut and 14:30 in Tokyo.
    monkeypatch.setattr(tasks, "utcnow", lambda: datetime(2026, 10, 12, 5, 30, tzinfo=UTC))
    sent = await tasks.dispatch_weekly_reports(ctx)
    assert sent == 1
    assert [m.to for m in mails] == ["beirut@example.com"]
    assert "Oct 05 - Oct 11, 2026" in mails[0].subject

    # Running again within the hour does not send duplicates.
    assert await tasks.dispatch_weekly_reports(ctx) == 0
    assert len(mails) == 1
    assert unverified.user["email"] not in [m.to for m in mails]


async def test_dispatch_respects_sunday_week_start(
    ctx: dict[str, Any],
    account: Account,
    db: AsyncSession,
    mails: list[EmailMessage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _verify(db, account)
    await account.patch(f"{API}/users/me/preferences", json={"week_starts_on": "sunday"})
    mails.clear()
    monkeypatch.setattr(tasks, "utcnow", lambda: datetime(2026, 10, 12, 8, 0, tzinfo=UTC))
    assert await tasks.dispatch_weekly_reports(ctx) == 0
    monkeypatch.setattr(tasks, "utcnow", lambda: datetime(2026, 10, 11, 8, 15, tzinfo=UTC))
    assert await tasks.dispatch_weekly_reports(ctx) == 1
    assert "Oct 04 - Oct 10, 2026" in mails[0].subject


async def test_dispatch_continues_after_a_failure(
    ctx: dict[str, Any],
    make_account: AccountFactory,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for email in ("a@example.com", "b@example.com"):
        await _verify(db, await make_account(email))
    calls: list[str] = []

    async def flaky(_: dict[str, Any], *, user_id: str, week_start: str) -> bool:
        calls.append(user_id)
        if len(calls) == 1:
            raise RuntimeError("smtp down")
        return True

    monkeypatch.setattr(tasks, "send_weekly_report", flaky)
    monkeypatch.setattr(tasks, "utcnow", lambda: datetime(2026, 10, 12, 8, 0, tzinfo=UTC))
    assert await tasks.dispatch_weekly_reports(ctx) == 1
    assert len(calls) == 2
