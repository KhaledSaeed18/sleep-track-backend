from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.conftest import API, Account, AccountFactory
from tests.integration.test_sleep import create

pytestmark = pytest.mark.integration


@pytest.fixture
async def admin(make_account: AccountFactory) -> Account:
    return await make_account("admin@example.com", admin=True)


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/admin/stats"), ("GET", "/admin/users"), ("PATCH", f"/admin/users/{uuid.uuid4()}")],
)
async def test_admin_requires_role(account: Account, method: str, path: str) -> None:
    response = await account.client.request(
        method, f"{API}{path}", headers=account.headers, json={}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


async def test_admin_requires_authentication(account: Account) -> None:
    response = await account.client.get(f"{API}/admin/stats")
    assert response.status_code == 401


async def test_stats(admin: Account, make_account: AccountFactory, db: AsyncSession) -> None:
    user = await make_account("someone@example.com")
    await create(user, "2026-06-01T23:00:00Z", "2026-06-02T07:00:00Z")
    await db.execute(
        text(
            "UPDATE users SET created_at = now() - interval '20 days' "
            "WHERE email = 'someone@example.com'"
        )
    )
    await db.commit()

    stats = (await admin.get(f"{API}/admin/stats")).json()
    assert stats["users_total"] == 2
    assert stats["users_new_7d"] == 1
    assert stats["users_active_7d"] == 2
    assert stats["sessions_total"] == 1
    assert stats["sessions_7d"] == 1
    assert len(stats["signups_daily"]) == 30
    assert sum(day["count"] for day in stats["signups_daily"]) == 2
    assert stats["signups_daily"][-1]["count"] == 1


async def test_list_users_with_filters(admin: Account, make_account: AccountFactory) -> None:
    alice = await make_account("alice@example.com", first_name="Alice")
    await make_account("bob@example.com", first_name="Bob")
    await create(alice, "2026-06-01T23:00:00Z", "2026-06-02T07:00:00Z")

    page = (await admin.get(f"{API}/admin/users")).json()
    assert page["total"] == 3
    first = page["items"][0]
    assert "preferences" not in first
    assert {"is_active", "sessions_count", "email", "role"} <= first.keys()

    found = (await admin.get(f"{API}/admin/users?q=ALI")).json()
    assert [u["email"] for u in found["items"]] == ["alice@example.com"]
    assert found["items"][0]["sessions_count"] == 1

    admins = (await admin.get(f"{API}/admin/users?role=admin")).json()
    assert [u["email"] for u in admins["items"]] == ["admin@example.com"]
    paged = (await admin.get(f"{API}/admin/users?page_size=1&page=2")).json()
    assert len(paged["items"]) == 1
    assert paged["pages"] == 3
    wildcard = (await admin.get(f"{API}/admin/users?q=%25")).json()
    assert wildcard["total"] == 0


async def test_disable_and_promote_user(admin: Account, account: Account) -> None:
    disabled = await admin.patch(f"{API}/admin/users/{account.id}", json={"is_active": False})
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False
    assert (await account.get(f"{API}/users/me")).status_code == 401

    inactive = (await admin.get(f"{API}/admin/users?is_active=false")).json()
    assert [u["id"] for u in inactive["items"]] == [account.id]

    enabled = await admin.patch(
        f"{API}/admin/users/{account.id}", json={"is_active": True, "role": "admin"}
    )
    assert enabled.json()["role"] == "admin"
    assert enabled.json()["is_active"] is True

    log = (await admin.get(f"{API}/users/me/audit-log")).json()
    assert log["total"] == 1  # admin's own log: only registration
    login = await account.client.post(
        f"{API}/auth/login", json={"email": account.user["email"], "password": account.password}
    )
    token = login.json()["access_token"]
    target_log = await account.client.get(
        f"{API}/users/me/audit-log", headers={"Authorization": f"Bearer {token}"}
    )
    events = [e["event"] for e in target_log.json()["items"]]
    assert events.count("account_updated_by_admin") == 2


async def test_admin_cannot_demote_or_disable_self(admin: Account) -> None:
    for payload in ({"role": "user"}, {"is_active": False}):
        response = await admin.patch(f"{API}/admin/users/{admin.id}", json=payload)
        assert response.status_code == 403
    noop = await admin.patch(f"{API}/admin/users/{admin.id}", json={"role": "admin"})
    assert noop.status_code == 200


async def test_update_unknown_user(admin: Account) -> None:
    response = await admin.patch(f"{API}/admin/users/{uuid.uuid4()}", json={"is_active": False})
    assert response.status_code == 404
    invalid = await admin.patch(f"{API}/admin/users/{admin.id}", json={"role": "root"})
    assert invalid.status_code == 422
