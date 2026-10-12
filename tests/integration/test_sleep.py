from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutils import utcnow
from tests.integration.conftest import API, Account, AccountFactory

pytestmark = pytest.mark.integration


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


async def create(account: Account, start: str, end: str, **extra: Any) -> dict[str, Any]:
    response = await account.post(
        f"{API}/sleep/sessions", json={"started_at": start, "ended_at": end, **extra}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def tag_ids(account: Account, *names: str) -> list[str]:
    tags = {t["name"]: t["id"] for t in (await account.get(f"{API}/tags")).json()}
    return [tags[name] for name in names]


# --------------------------------------------------------------------------- create
async def test_create_session_computes_fields(account: Account) -> None:
    caffeine, reading = await tag_ids(account, "Caffeine", "Reading")
    body = await create(
        account,
        "2026-03-10T20:30:00Z",
        "2026-03-11T04:30:00Z",
        quality=4,
        mood_on_wake=5,
        sleep_latency_minutes=10,
        awakenings=1,
        notes="  Good night  ",
        tag_ids=[reading, caffeine, caffeine],
        timezone="Asia/Beirut",
    )
    assert body["status"] == "completed"
    assert body["duration_minutes"] == 480
    assert body["night_of"] == "2026-03-10"
    assert body["timezone"] == "Asia/Beirut"
    assert body["notes"] == "Good night"
    assert body["source"] == "manual"
    assert [t["name"] for t in body["tags"]] == ["Caffeine", "Reading"]
    assert body["tags"][0]["usage_count"] == 1
    # Bedtime 22:30 local sits inside the 23:00 +/- 30 min window and every input is
    # ideal except quality 4, which earns 75 % of its 25-point weight (93.75 overall).
    assert body["score"] == 94


async def test_night_of_uses_previous_date_after_midnight(account: Account) -> None:
    body = await create(account, "2026-03-11T00:30:00Z", "2026-03-11T07:00:00Z", timezone="UTC")
    assert body["night_of"] == "2026-03-10"
    afternoon = await create(account, "2026-03-11T13:00:00Z", "2026-03-11T14:00:00Z")
    assert afternoon["night_of"] == "2026-03-11"


async def test_session_defaults_to_user_timezone(make_account: AccountFactory) -> None:
    account = await make_account(timezone="America/New_York")
    body = await create(account, "2026-03-11T03:00:00Z", "2026-03-11T11:00:00Z")
    assert body["timezone"] == "America/New_York"
    assert body["night_of"] == "2026-03-10"  # 23:00 local


@pytest.mark.parametrize(
    ("payload", "field", "fragment"),
    [
        (
            {"started_at": "2026-03-11T06:00:00Z", "ended_at": "2026-03-11T05:00:00Z"},
            "ended_at",
            "after",
        ),
        (
            {"started_at": "2026-03-11T06:00:00Z", "ended_at": "2026-03-11T06:05:00Z"},
            "ended_at",
            "10 minutes",
        ),
        (
            {"started_at": "2026-03-10T06:00:00Z", "ended_at": "2026-03-11T06:00:00Z"},
            "ended_at",
            "20 hours",
        ),
        (
            {"started_at": "2026-03-10T22:00:00", "ended_at": "2026-03-11T06:00:00Z"},
            "started_at",
            "timezone",
        ),
        (
            {
                "started_at": "2026-03-10T22:00:00Z",
                "ended_at": "2026-03-11T06:00:00Z",
                "quality": 6,
            },
            "quality",
            "less than or equal",
        ),
        (
            {
                "started_at": "2026-03-10T22:00:00Z",
                "ended_at": "2026-03-11T06:00:00Z",
                "sleep_latency_minutes": 241,
            },
            "sleep_latency_minutes",
            "",
        ),
        (
            {
                "started_at": "2026-03-10T22:00:00Z",
                "ended_at": "2026-03-11T06:00:00Z",
                "awakenings": -1,
            },
            "awakenings",
            "",
        ),
        (
            {
                "started_at": "2026-03-10T22:00:00Z",
                "ended_at": "2026-03-11T06:00:00Z",
                "notes": "x" * 2001,
            },
            "notes",
            "",
        ),
        (
            {
                "started_at": "2026-03-10T22:00:00Z",
                "ended_at": "2026-03-11T06:00:00Z",
                "timezone": "Bad/Zone",
            },
            "timezone",
            "unknown timezone",
        ),
        (
            {
                "started_at": "2026-03-10T22:00:00Z",
                "ended_at": "2026-03-11T06:00:00Z",
                "tag_ids": [str(uuid.uuid4())],
            },
            "tag_ids",
            "unknown tag",
        ),
        (
            {"started_at": "2026-03-10T22:00:00Z", "ended_at": "2026-03-11T06:00:00Z", "extra": 1},
            "extra",
            "",
        ),
    ],
)
async def test_create_session_validation(
    account: Account, payload: dict[str, Any], field: str, fragment: str
) -> None:
    response = await account.post(f"{API}/sleep/sessions", json=payload)
    assert response.status_code == 422, response.text
    error = response.json()["errors"][0]
    assert error["field"] == field
    assert fragment.lower() in error["message"].lower()


async def test_cannot_end_in_the_future(account: Account) -> None:
    now = utcnow()
    response = await account.post(
        f"{API}/sleep/sessions",
        json={
            "started_at": iso(now - timedelta(hours=7)),
            "ended_at": iso(now + timedelta(minutes=5)),
        },
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["message"] == "cannot be in the future"
    # 60 seconds of clock skew is tolerated.
    ok = await account.post(
        f"{API}/sleep/sessions",
        json={
            "started_at": iso(now - timedelta(hours=7)),
            "ended_at": iso(now + timedelta(seconds=30)),
        },
    )
    assert ok.status_code == 201


async def test_overlapping_sessions_conflict(account: Account) -> None:
    await create(account, "2026-03-10T22:00:00Z", "2026-03-11T06:00:00Z")
    for start, end in [
        ("2026-03-10T21:00:00Z", "2026-03-10T23:00:00Z"),
        ("2026-03-11T05:00:00Z", "2026-03-11T08:00:00Z"),
        ("2026-03-10T23:00:00Z", "2026-03-11T05:00:00Z"),
        ("2026-03-10T21:00:00Z", "2026-03-11T07:00:00Z"),
    ]:
        response = await account.post(
            f"{API}/sleep/sessions", json={"started_at": start, "ended_at": end}
        )
        assert response.status_code == 409
        assert response.json()["code"] == "session_overlap"
    # Touching boundaries are fine (half-open intervals).
    await create(account, "2026-03-11T06:00:00Z", "2026-03-11T07:00:00Z")


async def test_overlap_is_per_user(make_account: AccountFactory) -> None:
    alice, bob = await make_account(), await make_account()
    await create(alice, "2026-03-10T22:00:00Z", "2026-03-11T06:00:00Z")
    await create(bob, "2026-03-10T22:00:00Z", "2026-03-11T06:00:00Z")


async def test_exclusion_constraint_guards_races(account: Account, db: AsyncSession) -> None:
    await create(account, "2026-03-10T22:00:00Z", "2026-03-11T06:00:00Z")
    with pytest.raises(IntegrityError, match="ex_sleep_sessions_no_overlap"):
        await db.execute(
            text(
                "INSERT INTO sleep_sessions (id, user_id, status, started_at, ended_at, timezone,"
                " night_of, source) VALUES (gen_random_uuid(), :uid, 'completed',"
                " '2026-03-10T23:00:00Z', '2026-03-11T05:00:00Z', 'UTC', '2026-03-10', 'manual')"
            ),
            {"uid": account.id},
        )


async def test_cannot_use_another_users_tag(make_account: AccountFactory) -> None:
    alice, bob = await make_account(), await make_account()
    (bob_tag,) = await tag_ids(bob, "Caffeine")
    response = await alice.post(
        f"{API}/sleep/sessions",
        json={
            "started_at": "2026-03-10T22:00:00Z",
            "ended_at": "2026-03-11T06:00:00Z",
            "tag_ids": [bob_tag],
        },
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- read / update / delete
async def test_get_update_delete(account: Account) -> None:
    session = await create(account, "2026-03-10T22:00:00Z", "2026-03-11T06:00:00Z", quality=2)
    url = f"{API}/sleep/sessions/{session['id']}"
    assert (await account.get(url)).json()["quality"] == 2

    (exercise,) = await tag_ids(account, "Exercise")
    updated = await account.patch(
        url,
        json={
            "ended_at": "2026-03-11T05:00:00Z",
            "quality": 5,
            "tag_ids": [exercise],
            "notes": None,
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["duration_minutes"] == 420
    assert body["quality"] == 5
    assert [t["name"] for t in body["tags"]] == ["Exercise"]
    assert body["updated_at"] > session["updated_at"]

    cleared = await account.patch(url, json={"tag_ids": [], "quality": None})
    assert cleared.json()["tags"] == []
    assert cleared.json()["quality"] is None

    assert (await account.delete(url)).status_code == 204
    assert (await account.get(url)).status_code == 404


async def test_update_rejects_overlap_and_invalid_window(account: Account) -> None:
    first = await create(account, "2026-03-10T22:00:00Z", "2026-03-11T06:00:00Z")
    second = await create(account, "2026-03-11T08:00:00Z", "2026-03-11T12:00:00Z")
    overlap = await account.patch(
        f"{API}/sleep/sessions/{second['id']}", json={"started_at": "2026-03-11T05:00:00Z"}
    )
    assert overlap.status_code == 409
    invalid = await account.patch(
        f"{API}/sleep/sessions/{first['id']}", json={"ended_at": "2026-03-10T21:00:00Z"}
    )
    assert invalid.status_code == 422
    # Moving a session onto its own previous slot is not an overlap with itself.
    same = await account.patch(
        f"{API}/sleep/sessions/{first['id']}", json={"started_at": "2026-03-10T22:30:00Z"}
    )
    assert same.status_code == 200


async def test_cross_user_isolation(make_account: AccountFactory) -> None:
    alice, bob = await make_account(), await make_account()
    session = await create(alice, "2026-03-10T22:00:00Z", "2026-03-11T06:00:00Z")
    url = f"{API}/sleep/sessions/{session['id']}"
    assert (await bob.get(url)).status_code == 404
    assert (await bob.patch(url, json={"quality": 1})).status_code == 404
    assert (await bob.delete(url)).status_code == 404
    bulk = await bob.post(f"{API}/sleep/sessions/bulk-delete", json={"ids": [session["id"]]})
    assert bulk.json() == {"deleted": 0}
    assert (await bob.get(f"{API}/sleep/sessions")).json()["total"] == 0
    assert (await alice.get(url)).status_code == 200


async def test_bulk_delete(account: Account) -> None:
    ids = [
        (await create(account, f"2026-03-{day:02d}T22:00:00Z", f"2026-03-{day + 1:02d}T06:00:00Z"))[
            "id"
        ]
        for day in range(1, 4)
    ]
    response = await account.post(
        f"{API}/sleep/sessions/bulk-delete", json={"ids": [*ids[:2], ids[0], str(uuid.uuid4())]}
    )
    assert response.json() == {"deleted": 2}
    assert (await account.get(f"{API}/sleep/sessions")).json()["total"] == 1
    too_many = await account.post(
        f"{API}/sleep/sessions/bulk-delete", json={"ids": [str(uuid.uuid4()) for _ in range(101)]}
    )
    assert too_many.status_code == 422
    empty = await account.post(f"{API}/sleep/sessions/bulk-delete", json={"ids": []})
    assert empty.status_code == 422


# --------------------------------------------------------------------------- list
@pytest.fixture
async def history(account: Account) -> Account:
    caffeine, reading = await tag_ids(account, "Caffeine", "Reading")
    specs = [
        (1, 7 * 60, 3, [caffeine], "coffee late"),
        (2, 8 * 60, 5, [reading], "great book"),
        (3, 6 * 60, 2, [caffeine, reading], None),
        (4, 9 * 60, 4, [], "weekend lie-in"),
        (5, 7 * 60 + 30, None, [], None),
    ]
    for day, minutes, quality, tags, notes in specs:
        start = f"2026-04-{day:02d}T22:00:00Z"
        end_hour = 22 + minutes // 60
        end_day = day + end_hour // 24
        end = f"2026-04-{end_day:02d}T{end_hour % 24:02d}:{minutes % 60:02d}:00Z"
        await create(account, start, end, quality=quality, tag_ids=tags, notes=notes)
    return account


async def test_list_defaults_and_pagination(history: Account) -> None:
    page = (await history.get(f"{API}/sleep/sessions?page_size=2")).json()
    assert page["total"] == 5
    assert page["pages"] == 3
    assert page["page"] == 1
    assert [s["night_of"] for s in page["items"]] == ["2026-04-05", "2026-04-04"]
    last = (await history.get(f"{API}/sleep/sessions?page_size=2&page=3")).json()
    assert len(last["items"]) == 1
    beyond = (await history.get(f"{API}/sleep/sessions?page=9")).json()
    assert beyond["items"] == []
    assert (await history.get(f"{API}/sleep/sessions?page_size=101")).status_code == 422
    assert (await history.get(f"{API}/sleep/sessions?page=0")).status_code == 422


@pytest.mark.parametrize(
    ("query", "expected_nights"),
    [
        ("from=2026-04-02&to=2026-04-03", ["2026-04-03", "2026-04-02"]),
        ("min_quality=4", ["2026-04-04", "2026-04-02"]),
        ("max_quality=3", ["2026-04-03", "2026-04-01"]),
        ("min_quality=3&max_quality=4", ["2026-04-04", "2026-04-01"]),
        ("q=BOOK", ["2026-04-02"]),
        ("q=%25", []),
        (
            "sort=duration_minutes",
            ["2026-04-03", "2026-04-01", "2026-04-05", "2026-04-02", "2026-04-04"],
        ),
        ("sort=-quality", ["2026-04-02", "2026-04-04", "2026-04-01", "2026-04-03", "2026-04-05"]),
        ("sort=quality", ["2026-04-03", "2026-04-01", "2026-04-04", "2026-04-02", "2026-04-05"]),
        ("sort=night_of", ["2026-04-01", "2026-04-02", "2026-04-03", "2026-04-04", "2026-04-05"]),
        ("status=in_progress", []),
    ],
)
async def test_list_filters_and_sorting(
    history: Account, query: str, expected_nights: list[str]
) -> None:
    response = await history.get(f"{API}/sleep/sessions?{query}")
    assert response.status_code == 200, response.text
    assert [s["night_of"] for s in response.json()["items"]] == expected_nights


async def test_list_filter_by_tags(history: Account) -> None:
    caffeine, reading = await tag_ids(history, "Caffeine", "Reading")
    only = (await history.get(f"{API}/sleep/sessions?tag_ids={caffeine}")).json()
    assert {s["night_of"] for s in only["items"]} == {"2026-04-01", "2026-04-03"}
    either = (await history.get(f"{API}/sleep/sessions?tag_ids={caffeine},{reading}")).json()
    assert either["total"] == 3


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ("sort=notes", "sort"),
        ("tag_ids=not-a-uuid", "tag_ids"),
        ("from=2026-04-05&to=2026-04-01", "from"),
        ("min_quality=5&max_quality=1", "min_quality"),
        ("min_quality=9", "min_quality"),
    ],
)
async def test_list_query_validation(history: Account, query: str, field: str) -> None:
    response = await history.get(f"{API}/sleep/sessions?{query}")
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == field


# --------------------------------------------------------------------------- timer
async def test_timer_lifecycle(account: Account) -> None:
    idle = (await account.get(f"{API}/sleep/timer")).json()
    assert idle == {"active": False, "session": None}

    start_at = utcnow() - timedelta(hours=8)
    started = await account.post(
        f"{API}/sleep/timer/start", json={"started_at": iso(start_at), "timezone": "Europe/Paris"}
    )
    assert started.status_code == 201
    body = started.json()
    assert body["status"] == "in_progress"
    assert body["ended_at"] is None
    assert body["duration_minutes"] is None
    assert body["source"] == "timer"

    running = (await account.get(f"{API}/sleep/timer")).json()
    assert running["active"] is True
    assert running["session"]["id"] == body["id"]

    again = await account.post(f"{API}/sleep/timer/start")
    assert again.status_code == 409
    assert again.json()["code"] == "timer_already_running"

    (meditation,) = await tag_ids(account, "Meditation")
    stopped = await account.post(
        f"{API}/sleep/timer/stop", json={"quality": 4, "tag_ids": [meditation], "awakenings": 0}
    )
    assert stopped.status_code == 200
    result = stopped.json()
    assert result["status"] == "completed"
    assert 479 <= result["duration_minutes"] <= 481
    assert result["score"] is not None
    assert [t["name"] for t in result["tags"]] == ["Meditation"]

    assert (await account.post(f"{API}/sleep/timer/stop")).json()["code"] == "no_active_timer"
    assert (await account.delete(f"{API}/sleep/timer")).status_code == 409


async def test_timer_cancel(account: Account) -> None:
    assert (await account.post(f"{API}/sleep/timer/start")).status_code == 201
    assert (await account.delete(f"{API}/sleep/timer")).status_code == 204
    assert (await account.get(f"{API}/sleep/timer")).json()["active"] is False
    assert (await account.get(f"{API}/sleep/sessions")).json()["total"] == 0


async def test_timer_stop_too_short(account: Account) -> None:
    await account.post(f"{API}/sleep/timer/start")
    response = await account.post(f"{API}/sleep/timer/stop")
    assert response.status_code == 422
    assert "10 minutes" in response.json()["errors"][0]["message"]


async def test_timer_start_validation(account: Account) -> None:
    future = await account.post(
        f"{API}/sleep/timer/start", json={"started_at": iso(utcnow() + timedelta(hours=1))}
    )
    assert future.status_code == 422
    ancient = await account.post(
        f"{API}/sleep/timer/start", json={"started_at": iso(utcnow() - timedelta(hours=21))}
    )
    assert ancient.status_code == 422


async def test_timer_cannot_start_inside_logged_session(account: Account) -> None:
    now = utcnow()
    await create(account, iso(now - timedelta(hours=9)), iso(now - timedelta(hours=1)))
    response = await account.post(
        f"{API}/sleep/timer/start", json={"started_at": iso(now - timedelta(hours=2))}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "session_overlap"


async def test_running_timer_blocks_overlapping_manual_session(account: Account) -> None:
    now = utcnow()
    await account.post(
        f"{API}/sleep/timer/start", json={"started_at": iso(now - timedelta(hours=2))}
    )
    overlapping = await account.post(
        f"{API}/sleep/sessions",
        json={"started_at": iso(now - timedelta(hours=1)), "ended_at": iso(now)},
    )
    assert overlapping.status_code == 409
    earlier = await create(account, iso(now - timedelta(hours=12)), iso(now - timedelta(hours=4)))
    assert earlier["status"] == "completed"


async def test_patch_running_session(account: Account) -> None:
    started = (await account.post(f"{API}/sleep/timer/start")).json()
    url = f"{API}/sleep/sessions/{started['id']}"
    rejected = await account.patch(url, json={"ended_at": iso(utcnow())})
    assert rejected.status_code == 422
    moved = await account.patch(
        url, json={"started_at": iso(utcnow() - timedelta(hours=1)), "notes": "reading first"}
    )
    assert moved.status_code == 200
    assert moved.json()["status"] == "in_progress"
    assert moved.json()["notes"] == "reading first"


async def test_patch_running_session_cannot_start_over_20_hours_ago(account: Account) -> None:
    """Otherwise the timer can no longer be stopped at 'now'."""
    started = (await account.post(f"{API}/sleep/timer/start")).json()
    url = f"{API}/sleep/sessions/{started['id']}"
    rejected = await account.patch(url, json={"started_at": iso(utcnow() - timedelta(hours=21))})
    assert rejected.status_code == 422
    assert rejected.json()["errors"][0]["field"] == "started_at"
    state = (await account.get(f"{API}/sleep/timer")).json()
    assert state["session"]["started_at"] == started["started_at"]


# --------------------------------------------------------------------------- NUL bytes
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "POST",
            "/sleep/sessions",
            {
                "started_at": "2026-06-01T22:00:00Z",
                "ended_at": "2026-06-02T06:00:00Z",
                "notes": "bad\u0000note",
            },
        ),
        ("POST", "/tags", {"name": "x\u0000y", "color": "#aabbcc"}),
        ("PATCH", "/users/me", {"first_name": "x\u0000y"}),
        ("GET", "/sleep/sessions?q=a%00b", None),
    ],
)
async def test_nul_characters_are_rejected_as_validation_errors(
    account: Account, method: str, path: str, payload: dict[str, Any] | None
) -> None:
    """PostgreSQL cannot store U+0000; that is bad input, not a server error."""
    response = await account.client.request(
        method, f"{API}{path}", json=payload, headers=account.headers
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
    # The request-scoped session was rolled back and the account still works.
    assert (await account.get(f"{API}/users/me")).status_code == 200


async def test_far_past_timestamps_are_rejected(account: Account) -> None:
    """Year-1 instants overflow local-date conversion; they must be a 422, not a 500."""
    for start, end in [
        ("0001-01-01T01:00:00Z", "0001-01-01T09:00:00Z"),
        ("0001-01-01T01:00:00+05:00", "0001-01-01T09:00:00+05:00"),
    ]:
        response = await account.post(
            f"{API}/sleep/sessions", json={"started_at": start, "ended_at": end}
        )
        assert response.status_code == 422, response.text
        assert response.json()["errors"][0]["field"] == "started_at"

    created = await create(account, "2026-05-01T22:00:00Z", "2026-05-02T06:00:00Z")
    patched = await account.patch(
        f"{API}/sleep/sessions/{created['id']}", json={"started_at": "0001-01-01T01:00:00+05:00"}
    )
    assert patched.status_code == 422

    content = "started_at,ended_at\n0001-01-01T01:00:00+05:00,0001-01-01T09:00:00+05:00\n"
    imported = await account.post(
        f"{API}/users/me/import", files={"file": ("x.csv", content.encode(), "text/csv")}
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["errors"][0]["message"].startswith("started_at")
