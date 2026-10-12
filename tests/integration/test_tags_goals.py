from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutils import local_today
from tests.integration.conftest import API, Account, AccountFactory
from tests.integration.test_sleep import create

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- tags
async def test_default_tags_shape(account: Account) -> None:
    tags = (await account.get(f"{API}/tags")).json()
    caffeine = next(t for t in tags if t["name"] == "Caffeine")
    assert caffeine["color"] == "#f59e0b"
    assert caffeine["icon"] == "coffee"
    assert caffeine["is_system"] is True
    assert caffeine["usage_count"] == 0
    assert [t["name"].lower() for t in tags] == sorted(t["name"].lower() for t in tags)


async def test_create_update_delete_tag(account: Account) -> None:
    created = await account.post(
        f"{API}/tags", json={"name": "  Hot room ", "color": "#AABBCC", "icon": "thermometer"}
    )
    assert created.status_code == 201
    tag = created.json()
    assert tag["name"] == "Hot room"
    assert tag["color"] == "#aabbcc"
    assert tag["is_system"] is False

    session = await create(
        account, "2026-05-01T22:00:00Z", "2026-05-02T06:00:00Z", tag_ids=[tag["id"]]
    )
    patched = await account.patch(
        f"{API}/tags/{tag['id']}", json={"name": "Warm room", "icon": None}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Warm room"
    assert patched.json()["icon"] is None
    assert patched.json()["usage_count"] == 1

    assert (await account.delete(f"{API}/tags/{tag['id']}")).status_code == 204
    reloaded = await account.get(f"{API}/sleep/sessions/{session['id']}")
    assert reloaded.json()["tags"] == []
    assert (await account.delete(f"{API}/tags/{tag['id']}")).status_code == 404


async def test_tag_names_are_unique_case_insensitively(account: Account) -> None:
    duplicate = await account.post(f"{API}/tags", json={"name": "caffeine", "color": "#000000"})
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "conflict"
    tags = {t["name"]: t["id"] for t in (await account.get(f"{API}/tags")).json()}
    rename = await account.patch(f"{API}/tags/{tags['Reading']}", json={"name": "ALCOHOL"})
    assert rename.status_code == 409
    # Renaming to its own name with different casing is allowed.
    same = await account.patch(f"{API}/tags/{tags['Reading']}", json={"name": "READING"})
    assert same.status_code == 200


async def test_unique_index_race_maps_to_conflict(
    account: Account, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.tags.repository import TagRepository

    async def never_exists(*_: object, **__: object) -> bool:
        return False

    monkeypatch.setattr(TagRepository, "name_exists", never_exists)
    response = await account.post(f"{API}/tags", json={"name": "Caffeine", "color": "#000000"})
    assert response.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "color": "#000000"},
        {"name": "x" * 51, "color": "#000000"},
        {"name": "Ok", "color": "red"},
        {"name": "Ok", "color": "#000000", "icon": "Not An Icon"},
        {"name": "Ok", "color": "#000000", "unexpected": True},
    ],
)
async def test_tag_validation(account: Account, payload: dict[str, object]) -> None:
    assert (await account.post(f"{API}/tags", json=payload)).status_code == 422


async def test_tags_are_isolated(make_account: AccountFactory) -> None:
    alice, bob = await make_account(), await make_account()
    alice_tag = (await alice.get(f"{API}/tags")).json()[0]["id"]
    assert (await bob.patch(f"{API}/tags/{alice_tag}", json={"name": "Mine"})).status_code == 404
    assert (await bob.delete(f"{API}/tags/{alice_tag}")).status_code == 404


# --------------------------------------------------------------------------- goals
async def test_goal_defaults(account: Account) -> None:
    response = await account.get(f"{API}/goals")
    assert response.status_code == 200
    assert response.json() == {
        "target_duration_minutes": 480,
        "target_bedtime": "23:00",
        "target_wake_time": "07:00",
        "bedtime_window_minutes": 30,
        "recommended": {"min_hours": 7, "max_hours": 9, "age_group": None},
    }


async def test_update_goals_rescores_sessions(account: Account) -> None:
    session = await create(account, "2026-05-01T23:00:00Z", "2026-05-02T06:00:00Z", quality=5)
    before = session["score"]
    response = await account.put(
        f"{API}/goals",
        json={
            "target_duration_minutes": 420,
            "target_bedtime": "23:00",
            "target_wake_time": "06:00",
            "bedtime_window_minutes": 15,
        },
    )
    assert response.status_code == 200
    assert response.json()["target_duration_minutes"] == 420
    after = (await account.get(f"{API}/sleep/sessions/{session['id']}")).json()["score"]
    assert after > before
    assert after == 100
    # Saved goals are returned on the next read.
    assert (await account.get(f"{API}/goals")).json()["target_wake_time"] == "06:00"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "target_duration_minutes": 100,
            "target_bedtime": "23:00",
            "target_wake_time": "07:00",
            "bedtime_window_minutes": 30,
        },
        {
            "target_duration_minutes": 480,
            "target_bedtime": "25:00",
            "target_wake_time": "07:00",
            "bedtime_window_minutes": 30,
        },
        {
            "target_duration_minutes": 480,
            "target_bedtime": "23:00",
            "target_wake_time": "7am",
            "bedtime_window_minutes": 30,
        },
        {
            "target_duration_minutes": 480,
            "target_bedtime": "23:00",
            "target_wake_time": "07:00",
            "bedtime_window_minutes": 999,
        },
        {"target_duration_minutes": 480, "target_bedtime": "23:00"},
    ],
)
async def test_goal_validation(account: Account, payload: dict[str, object]) -> None:
    assert (await account.put(f"{API}/goals", json=payload)).status_code == 422


@pytest.mark.parametrize(
    ("years_old", "expected"),
    [
        (15, ("teen", 8, 10)),
        (22, ("young_adult", 7, 9)),
        (40, ("adult", 7, 9)),
        (70, ("older_adult", 7, 8)),
    ],
)
async def test_goal_recommendation_follows_age(
    account: Account, db: AsyncSession, years_old: int, expected: tuple[str, int, int]
) -> None:
    today = local_today("UTC")
    dob = today.replace(year=today.year - years_old, day=1)
    await db.execute(
        text("UPDATE users SET date_of_birth = :dob WHERE id = :id"), {"dob": dob, "id": account.id}
    )
    await db.commit()
    recommended = (await account.get(f"{API}/goals")).json()["recommended"]
    assert (
        recommended["age_group"],
        recommended["min_hours"],
        recommended["max_hours"],
    ) == expected
