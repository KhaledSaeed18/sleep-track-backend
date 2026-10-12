from __future__ import annotations

import csv
import io
import json

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User
from tests.integration.conftest import API, DEFAULT_PASSWORD, Account, AccountFactory

pytestmark = pytest.mark.integration


async def _second_login(account: Account) -> httpx.AsyncClient:
    other = httpx.AsyncClient(transport=account.client._transport, base_url="http://testserver")
    response = await other.post(
        f"{API}/auth/login",
        json={"email": account.user["email"], "password": account.password},
        headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Safari/604.1"},
    )
    assert response.status_code == 200
    other.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    return other


async def test_get_me(account: Account) -> None:
    response = await account.get(f"{API}/users/me")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == account.id
    assert body["role"] == "user"
    assert body["preferences"] == {
        "timezone": "UTC",
        "time_format": "24h",
        "week_starts_on": "monday",
        "theme": "system",
        "email_weekly_report": True,
        "email_bedtime_reminder": False,
    }


async def test_update_profile(account: Account) -> None:
    response = await account.patch(
        f"{API}/users/me",
        json={
            "first_name": "  Khaled ",
            "last_name": "Saeed",
            "date_of_birth": "1999-05-01",
            "avatar_url": "https://cdn.example.com/me.png",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["first_name"] == "Khaled"
    assert body["full_name"] == "Khaled Saeed"
    assert body["date_of_birth"] == "1999-05-01"
    assert body["avatar_url"] == "https://cdn.example.com/me.png"

    cleared = await account.patch(
        f"{API}/users/me", json={"avatar_url": None, "date_of_birth": None}
    )
    assert cleared.json()["avatar_url"] is None
    assert cleared.json()["date_of_birth"] is None
    assert cleared.json()["first_name"] == "Khaled"


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"date_of_birth": "2999-01-01"}, "date_of_birth"),
        ({"date_of_birth": "1800-01-01"}, "date_of_birth"),
        ({"avatar_url": "javascript:alert(1)"}, "avatar_url"),
        ({"first_name": ""}, "first_name"),
        ({"email": "new@example.com"}, "email"),
    ],
)
async def test_update_profile_validation(
    account: Account, payload: dict[str, str], field: str
) -> None:
    response = await account.patch(f"{API}/users/me", json=payload)
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == field


async def test_update_preferences(account: Account) -> None:
    response = await account.patch(
        f"{API}/users/me/preferences",
        json={"timezone": "Asia/Beirut", "theme": "dark", "week_starts_on": "sunday"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["timezone"] == "Asia/Beirut"
    assert body["theme"] == "dark"
    assert body["week_starts_on"] == "sunday"
    assert body["time_format"] == "24h"

    invalid = await account.patch(f"{API}/users/me/preferences", json={"theme": "neon"})
    assert invalid.status_code == 422
    bad_tz = await account.patch(f"{API}/users/me/preferences", json={"timezone": "Nowhere/City"})
    assert bad_tz.status_code == 422


async def test_change_password_revokes_other_sessions(account: Account, db: AsyncSession) -> None:
    other = await _second_login(account)
    response = await account.post(
        f"{API}/users/me/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "Different2026"},
    )
    assert response.status_code == 204
    assert (await account.get(f"{API}/users/me")).status_code == 200
    assert (await other.get(f"{API}/users/me")).status_code == 401
    await other.aclose()

    login = await account.client.post(
        f"{API}/auth/login", json={"email": account.user["email"], "password": "Different2026"}
    )
    assert login.status_code == 200
    events = list(await db.scalars(text("SELECT event FROM audit_logs ORDER BY created_at")))
    assert "password_changed" in events


@pytest.mark.parametrize(
    ("current", "new", "field"),
    [
        (None, "Different2026", "current_password"),
        ("WrongPass99", "Different2026", "current_password"),
        (DEFAULT_PASSWORD, DEFAULT_PASSWORD, "new_password"),
        (DEFAULT_PASSWORD, "weak", "new_password"),
    ],
)
async def test_change_password_validation(
    account: Account, current: str | None, new: str, field: str
) -> None:
    response = await account.post(
        f"{API}/users/me/change-password",
        json={"current_password": current, "new_password": new},
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == field


async def test_oauth_only_user_can_set_first_password(account: Account, db: AsyncSession) -> None:
    await db.execute(
        text("UPDATE users SET password_hash = NULL WHERE id = :id"), {"id": account.id}
    )
    await db.commit()
    response = await account.post(
        f"{API}/users/me/change-password",
        json={"current_password": None, "new_password": "FirstPass2026"},
    )
    assert response.status_code == 204
    me = await account.get(f"{API}/users/me")
    assert me.json()["has_password"] is True


async def test_list_and_revoke_device_sessions(account: Account) -> None:
    other = await _second_login(account)
    sessions = (await account.get(f"{API}/users/me/sessions")).json()
    assert len(sessions) == 2
    current = [s for s in sessions if s["current"]]
    assert len(current) == 1
    iphone = next(s for s in sessions if not s["current"])
    assert iphone["device_label"] == "Safari on iPhone"

    response = await account.delete(f"{API}/users/me/sessions/{iphone['id']}")
    assert response.status_code == 204
    assert (await other.get(f"{API}/users/me")).status_code == 401
    await other.aclose()
    assert len((await account.get(f"{API}/users/me/sessions")).json()) == 1

    again = await account.delete(f"{API}/users/me/sessions/{iphone['id']}")
    assert again.status_code == 404


async def test_cannot_revoke_someone_elses_session(make_account: AccountFactory) -> None:
    alice, bob = await make_account(), await make_account()
    bob_session = (await bob.get(f"{API}/users/me/sessions")).json()[0]["id"]
    response = await alice.delete(f"{API}/users/me/sessions/{bob_session}")
    assert response.status_code == 404
    assert (await bob.get(f"{API}/users/me")).status_code == 200


async def test_audit_log_is_paged_and_scoped(make_account: AccountFactory) -> None:
    alice, bob = await make_account(), await make_account()
    for _ in range(3):
        await alice.client.post(
            f"{API}/auth/login", json={"email": alice.user["email"], "password": "WrongPass99"}
        )
    page = (await alice.get(f"{API}/users/me/audit-log?page_size=2")).json()
    assert page["total"] == 4
    assert page["pages"] == 2
    assert [entry["event"] for entry in page["items"]] == ["login_failed", "login_failed"]
    assert page["items"][0]["metadata"] == {"reason": "invalid_credentials"}
    bob_log = (await bob.get(f"{API}/users/me/audit-log")).json()
    assert bob_log["total"] == 1


async def _create_session(
    account: Account, start: str, end: str, **extra: object
) -> dict[str, object]:
    response = await account.post(
        f"{API}/sleep/sessions", json={"started_at": start, "ended_at": end, **extra}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def test_export_json_and_csv(account: Account) -> None:
    tags = (await account.get(f"{API}/tags")).json()
    await _create_session(
        account,
        "2026-01-10T22:00:00Z",
        "2026-01-11T06:00:00Z",
        quality=4,
        notes="=HYPERLINK('x')",
        tag_ids=[tags[0]["id"]],
    )

    exported = await account.get(f"{API}/users/me/export?format=json")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/json")
    assert 'attachment; filename="sleeptrack-export-' in exported.headers["content-disposition"]
    data = json.loads(exported.text)
    assert data["user"]["email"] == account.user["email"]
    assert len(data["tags"]) == 11
    assert data["sessions"][0]["tags"] == [tags[0]["name"]]
    assert data["goals"]["target_duration_minutes"] == 480

    as_csv = await account.get(f"{API}/users/me/export?format=csv")
    assert as_csv.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(as_csv.text)))
    assert len(rows) == 1
    assert rows[0]["duration_minutes"] == "480"
    assert rows[0]["notes"].startswith("'=")  # formula injection neutralised

    bad = await account.get(f"{API}/users/me/export?format=xml")
    assert bad.status_code == 422


async def test_import_csv(account: Account) -> None:
    await _create_session(account, "2026-02-01T22:00:00Z", "2026-02-02T06:00:00Z")
    content = "\n".join(
        [
            "started_at,ended_at,quality,notes,tags",
            "2026-02-03T22:00:00Z,2026-02-04T06:30:00Z,4,Imported night,Caffeine;Brand New",
            "2026-02-04 23:00,2026-02-05 07:00,5,,caffeine|Reading",
            "2026-02-01T23:00:00Z,2026-02-02T05:00:00Z,3,overlaps,",
            "not-a-date,2026-02-06T06:00:00Z,3,,",
            "2026-02-06T22:00:00Z,2026-02-06T22:05:00Z,3,too short,",
            "2026-02-07T22:00:00Z,2026-02-08T06:00:00Z,9,bad quality,",
        ]
    )
    response = await account.post(
        f"{API}/users/me/import",
        files={"file": ("sleep.csv", content.encode(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["imported"] == 2
    assert body["skipped"] == 4
    messages = {error["row"]: error["message"] for error in body["errors"]}
    assert "overlaps" in messages[4]
    assert "invalid timestamp" in messages[5]
    assert "at least 10 minutes" in messages[6]
    assert "quality" in messages[7]

    tags = {t["name"]: t for t in (await account.get(f"{API}/tags")).json()}
    assert "Brand New" in tags
    assert tags["Caffeine"]["usage_count"] == 2
    sessions = (await account.get(f"{API}/sleep/sessions?sort=started_at")).json()["items"]
    assert [s["source"] for s in sessions] == ["manual", "import", "import"]


@pytest.mark.parametrize(
    ("content", "content_type", "fragment"),
    [
        (b"start,end\n1,2", "text/csv", "header must include"),
        (b"\xff\xfe\x00bad", "text/csv", "UTF-8"),
        (b"started_at,ended_at\n", "image/png", "CSV"),
        (
            b"started_at,ended_at,notes\n2026-01-01T22:00:00Z,2026-01-02T06:00:00Z,"
            + b"a" * 200_000,
            "text/csv",
            "not valid CSV",
        ),
    ],
)
async def test_import_rejects_bad_files(
    account: Account, content: bytes, content_type: str, fragment: str
) -> None:
    response = await account.post(
        f"{API}/users/me/import", files={"file": ("x.csv", content, content_type)}
    )
    assert response.status_code == 422
    assert fragment in response.json()["errors"][0]["message"]


async def test_import_reports_nul_characters_per_row(account: Account) -> None:
    content = (
        "started_at,ended_at,notes,tags\n"
        "2026-03-01T22:00:00Z,2026-03-02T06:00:00Z,bad\x00note,\n"
        "2026-03-02T22:00:00Z,2026-03-03T06:00:00Z,,Bad\x00Tag\n"
        "2026-03-03T22:00:00Z,2026-03-04T06:00:00Z,fine,\n"
    )
    response = await account.post(
        f"{API}/users/me/import", files={"file": ("x.csv", content.encode(), "text/csv")}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["imported"] == 1
    assert [error["row"] for error in body["errors"]] == [2, 3]
    assert all("NUL" in error["message"] for error in body["errors"])


async def test_import_row_limit(account: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI

    transport = account.client._transport
    assert isinstance(transport, httpx.ASGITransport)
    app = transport.app
    assert isinstance(app, FastAPI)
    monkeypatch.setattr(app.state.settings, "max_import_rows", 1)
    content = "started_at,ended_at\n2026-01-01T22:00:00Z,2026-01-02T06:00:00Z\n" * 1 + (
        "2026-01-02T22:00:00Z,2026-01-03T06:00:00Z\n"
    )
    response = await account.post(
        f"{API}/users/me/import", files={"file": ("x.csv", content.encode(), "text/csv")}
    )
    assert response.status_code == 422


async def test_delete_account(account: Account, db: AsyncSession) -> None:
    await _create_session(account, "2026-03-01T22:00:00Z", "2026-03-02T06:00:00Z")
    wrong = await account.delete(
        f"{API}/users/me", json={"password": "WrongPass99", "confirm": "DELETE"}
    )
    assert wrong.status_code == 422
    unconfirmed = await account.delete(
        f"{API}/users/me", json={"password": DEFAULT_PASSWORD, "confirm": "yes"}
    )
    assert unconfirmed.status_code == 422

    response = await account.delete(
        f"{API}/users/me", json={"password": DEFAULT_PASSWORD, "confirm": "DELETE"}
    )
    assert response.status_code == 204
    assert await db.scalar(select(User).where(User.id == account.id)) is None
    remaining = await db.scalar(text("SELECT count(*) FROM sleep_sessions"))
    assert remaining == 0
    assert (await account.get(f"{API}/users/me")).status_code == 401


async def test_demo_account_can_delete_without_password(client: httpx.AsyncClient) -> None:
    demo = await client.post(f"{API}/auth/demo")
    headers = {"Authorization": f"Bearer {demo.json()['access_token']}"}
    response = await client.request(
        "DELETE", f"{API}/users/me", json={"confirm": "DELETE"}, headers=headers
    )
    assert response.status_code == 204
