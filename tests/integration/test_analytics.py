"""Analytics, insights and reports over a small, hand-checked week of data.

Nights (UTC user, default goal 8 h, 23:00 +/- 30 min):

    night      bedtime  duration  quality  tags
    Mon 06-01  23:00    480       4        Caffeine
    Tue 06-02  23:30    420       3        Caffeine
    Wed 06-03  22:45    480       5        Exercise
    Thu 06-04  -        -         -        (not logged)
    Fri 06-05  00:30    510       4        Alcohol
    Sat 06-06  01:00    510       3
    Sun 06-07  23:00    420       2        Caffeine
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI

from app.core.timeutils import format_hhmm
from app.integrations.email.base import EmailMessage
from app.modules.analytics.calculations import circular_mean_minutes
from tests.integration.conftest import API, Account, AccountFactory
from tests.integration.test_sleep import create, tag_ids

pytestmark = pytest.mark.integration

WEEK = "from=2026-06-01&to=2026-06-07"
NIGHTS = [
    ("2026-06-01T23:00:00Z", "2026-06-02T07:00:00Z", 4, "Caffeine"),
    ("2026-06-02T23:30:00Z", "2026-06-03T06:30:00Z", 3, "Caffeine"),
    ("2026-06-03T22:45:00Z", "2026-06-04T06:45:00Z", 5, "Exercise"),
    ("2026-06-06T00:30:00Z", "2026-06-06T09:00:00Z", 4, "Alcohol"),
    ("2026-06-07T01:00:00Z", "2026-06-07T09:30:00Z", 3, None),
    ("2026-06-07T23:00:00Z", "2026-06-08T06:00:00Z", 2, "Caffeine"),
]


@pytest.fixture
async def week(account: Account) -> Account:
    for start, end, quality, tag in NIGHTS:
        tags = await tag_ids(account, tag) if tag else []
        await create(
            account,
            start,
            end,
            quality=quality,
            sleep_latency_minutes=10,
            awakenings=1,
            mood_on_wake=quality,
            tag_ids=tags,
        )
    return account


async def _get(account: Account, path: str) -> Any:
    response = await account.get(f"{API}{path}")
    assert response.status_code == 200, response.text
    return response.json()


async def test_overview(week: Account) -> None:
    body = await _get(week, f"/analytics/overview?{WEEK}")
    assert body["range"] == {"from": "2026-06-01", "to": "2026-06-07", "days": 7}
    assert body["nights_tracked"] == 6
    assert body["total_sleep_minutes"] == 2820
    averages = body["averages"]
    assert averages["duration_minutes"] == 470
    assert averages["quality"] == 3.5
    assert averages["sleep_latency_minutes"] == 10
    assert averages["awakenings"] == 1.0
    assert averages["mood_on_wake"] == 3.5
    expected_bedtime = format_hhmm(circular_mean_minutes([1380, 1410, 1365, 30, 60, 1380]))
    assert averages["bedtime"] == expected_bedtime
    assert averages["bedtime"].startswith("23:3")
    assert body["goal_attainment_rate"] == 0.67
    assert body["sleep_debt_minutes"] == 60
    assert body["current_streak"] == 0
    assert body["longest_streak"] == 2
    assert body["previous_period"] is None
    assert 0 < body["consistency_score"] < 100


async def test_overview_previous_period(week: Account) -> None:
    await create(week, "2026-05-28T23:00:00Z", "2026-05-29T06:00:00Z", quality=2)
    body = await _get(week, f"/analytics/overview?{WEEK}")
    previous = body["previous_period"]
    assert previous["duration_minutes"] == 420
    assert previous["quality"] == 2.0
    assert previous["consistency_score"] is None


async def test_overview_empty_range(account: Account) -> None:
    body = await _get(account, "/analytics/overview")
    assert body["nights_tracked"] == 0
    assert body["range"]["days"] == 30
    assert body["averages"]["duration_minutes"] is None
    assert body["averages"]["bedtime"] is None
    assert body["consistency_score"] is None
    assert body["goal_attainment_rate"] is None
    assert body["sleep_debt_minutes"] == 0


async def test_daily_series(week: Account) -> None:
    days = await _get(week, f"/analytics/daily?{WEEK}")
    assert [d["date"] for d in days] == [f"2026-06-0{i}" for i in range(1, 8)]
    thursday = days[3]
    assert thursday == {
        "date": "2026-06-04",
        "duration_minutes": None,
        "quality": None,
        "score": None,
        "bedtime": None,
        "wake_time": None,
        "sessions": 0,
    }
    friday = days[4]
    assert friday["duration_minutes"] == 510
    assert friday["bedtime"] == "00:30"
    assert friday["wake_time"] == "09:00"
    assert friday["sessions"] == 1


async def test_daily_aggregates_naps(week: Account) -> None:
    await create(week, "2026-06-02T13:00:00Z", "2026-06-02T13:40:00Z", quality=5)
    tuesday = (await _get(week, f"/analytics/daily?{WEEK}"))[1]
    assert tuesday["date"] == "2026-06-02"
    assert tuesday["sessions"] == 2
    assert tuesday["duration_minutes"] == 460
    assert tuesday["quality"] == 4.0
    # Bedtime/wake come from the main (longest) session.
    assert tuesday["bedtime"] == "23:30"


async def test_weekday_breakdown(week: Account) -> None:
    rows = await _get(week, f"/analytics/weekday?{WEEK}")
    assert [r["weekday"] for r in rows] == list(range(7))
    assert rows[0]["nights"] == 1
    assert rows[0]["avg_duration_minutes"] == 480
    assert rows[0]["avg_bedtime"] == "23:00"
    assert rows[3] == {
        "weekday": 3,
        "nights": 0,
        "avg_duration_minutes": None,
        "avg_quality": None,
        "avg_bedtime": None,
        "avg_wake_time": None,
    }
    assert rows[5]["avg_bedtime"] == "01:00"


async def test_tag_impact(week: Account) -> None:
    rows = await _get(week, f"/analytics/tags?{WEEK}")
    caffeine = next(r for r in rows if r["tag"]["name"] == "Caffeine")
    assert caffeine["nights_with"] == 3
    assert caffeine["avg_quality_with"] == 3.0
    assert caffeine["avg_quality_without"] == 4.0
    assert caffeine["quality_impact"] == -1.0
    assert caffeine["avg_duration_with"] == 440
    assert caffeine["avg_duration_without"] == 500
    assert caffeine["duration_impact_minutes"] == -60
    assert caffeine["tag"]["usage_count"] == 3
    assert {r["tag"]["name"] for r in rows} == {"Caffeine", "Exercise", "Alcohol"}


async def test_distribution(week: Account) -> None:
    body = await _get(week, f"/analytics/distribution?{WEEK}")
    durations = {b["bucket"]: b["count"] for b in body["duration"]}
    assert durations == {"<5h": 0, "5-6h": 0, "6-7h": 0, "7-8h": 2, "8-9h": 4, "9h+": 0}
    assert {q["rating"]: q["count"] for q in body["quality"]} == {1: 0, 2: 1, 3: 2, 4: 2, 5: 1}
    hours = {b["hour"]: b["count"] for b in body["bedtime"]}
    assert len(hours) == 24
    assert hours[23] == 3
    assert hours[22] == 1
    assert hours[0] == 1
    assert hours[1] == 1


async def test_chronotype_needs_enough_nights(week: Account) -> None:
    body = await _get(week, f"/analytics/chronotype?{WEEK}")
    assert body["chronotype"] == "unknown"
    assert body["nights_analyzed"] == 6
    assert body["social_jetlag_minutes"] is None


async def test_chronotype_with_more_data(week: Account) -> None:
    await create(week, "2026-05-31T23:00:00Z", "2026-06-01T07:00:00Z", quality=4)
    body = await _get(week, "/analytics/chronotype?from=2026-05-31&to=2026-06-07")
    assert body["nights_analyzed"] == 7
    assert body["chronotype"] in {"early_bird", "intermediate", "night_owl"}
    # Free nights (Fri/Sat) mid-sleep: 04:45 and 05:15 -> 05:00.
    assert body["mid_sleep"] == "05:00"
    assert body["social_jetlag_minutes"] is not None
    assert body["social_jetlag_minutes"] > 60


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ("from=2026-06-07&to=2026-06-01", "from"),
        ("from=2025-01-01&to=2026-06-01", "from"),
        ("from=junk", "from"),
    ],
)
async def test_range_validation(account: Account, query: str, field: str) -> None:
    response = await account.get(f"{API}/analytics/overview?{query}")
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == field


async def test_cache_is_invalidated_by_writes(week: Account, app: FastAPI) -> None:
    first = await _get(week, f"/analytics/overview?{WEEK}")
    keys = [key async for key in app.state.redis.scan_iter("cache:analytics:*")]
    assert any(":overview:" in key for key in keys)

    await create(week, "2026-06-04T23:00:00Z", "2026-06-05T07:00:00Z", quality=5)
    second = await _get(week, f"/analytics/overview?{WEEK}")
    assert second["nights_tracked"] == first["nights_tracked"] + 1

    sessions = (await week.get(f"{API}/sleep/sessions?from=2026-06-04&to=2026-06-04")).json()
    await week.delete(f"{API}/sleep/sessions/{sessions['items'][0]['id']}")
    third = await _get(week, f"/analytics/overview?{WEEK}")
    assert third["nights_tracked"] == first["nights_tracked"]


async def test_timezone_preference_changes_local_times(week: Account) -> None:
    await week.patch(f"{API}/users/me/preferences", json={"timezone": "Asia/Tokyo"})
    days = await _get(week, f"/analytics/daily?{WEEK}")
    assert days[0]["bedtime"] == "08:00"  # 23:00 UTC is 08:00 in Tokyo


async def test_analytics_are_isolated(week: Account, make_account: AccountFactory) -> None:
    other = await make_account()
    body = await _get(other, f"/analytics/overview?{WEEK}")
    assert body["nights_tracked"] == 0


# --------------------------------------------------------------------------- insights
async def test_insights_for_week(week: Account) -> None:
    insights = await _get(week, f"/insights?{WEEK}")
    ids = [i["id"] for i in insights]
    assert "tag-impact-caffeine" in ids
    caffeine = next(i for i in insights if i["id"] == "tag-impact-caffeine")
    assert caffeine["kind"] == "warning"
    assert caffeine["category"] == "tags"
    assert caffeine["title"] == "Caffeine lowers your sleep quality"
    assert caffeine["metric"] == {"label": "Quality impact", "value": "-1.0"}
    assert "3.0 vs 4.0" in caffeine["message"]
    priorities = [i["priority"] for i in insights]
    assert priorities == sorted(priorities, reverse=True)
    assert ids[-1] == "tip-consistent-wake-time"


async def test_insights_without_data(account: Account) -> None:
    insights = await _get(account, "/insights")
    assert insights[0]["id"] == "not-enough-data"
    assert insights[0]["kind"] == "info"


async def test_insights_on_demo_history(app: FastAPI) -> None:
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        demo = await http.post(f"{API}/auth/demo")
        headers = {"Authorization": f"Bearer {demo.json()['access_token']}"}
        response = await http.get(f"{API}/insights?from=2020-01-01&to=2020-01-31", headers=headers)
        assert response.json()[0]["id"] == "not-enough-data"
        response = await http.get(f"{API}/insights", headers=headers)
        kinds = {i["kind"] for i in response.json()}
        assert len(response.json()) >= 3
        assert kinds & {"warning", "positive"}


# --------------------------------------------------------------------------- reports
async def test_weekly_report(week: Account) -> None:
    report = await _get(week, "/reports/weekly?week_start=2026-06-01")
    assert report["week_start"] == "2026-06-01"
    assert report["week_end"] == "2026-06-07"
    assert report["overview"]["nights_tracked"] == 6
    assert len(report["daily"]) == 7
    assert len(report["insights"]) <= 5
    best, worst = report["best_night"], report["worst_night"]
    assert best["night_of"] == "2026-06-03"
    assert worst["quality"] == 2
    assert best["score"] >= worst["score"]


async def test_weekly_report_defaults_to_last_full_week(account: Account) -> None:
    from datetime import timedelta

    from app.core.timeutils import local_today

    report = await _get(account, "/reports/weekly")
    today = local_today("UTC")
    this_monday = today - timedelta(days=today.weekday())
    assert report["week_start"] == (this_monday - timedelta(days=7)).isoformat()
    assert report["best_night"] is None
    assert report["worst_night"] is None

    await account.patch(f"{API}/users/me/preferences", json={"week_starts_on": "sunday"})
    sunday_report = await _get(account, "/reports/weekly")
    assert sunday_report["week_start"] != report["week_start"]


async def test_weekly_report_email(week: Account, mails: list[EmailMessage]) -> None:
    mails.clear()
    response = await week.post(f"{API}/reports/weekly/email", json={"week_start": "2026-06-01"})
    assert response.status_code == 202
    assert "2026-06-01" in response.json()["message"]
    assert len(mails) == 1
    message = mails[0]
    assert message.subject == "Your SleepTrack weekly report (Jun 01 - Jun 07, 2026)"
    assert "Nights tracked: 6" in message.text
    assert "Caffeine lowers your sleep quality" in message.html


async def test_weekly_report_email_blocked_for_demo(app: FastAPI) -> None:
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        demo = await http.post(f"{API}/auth/demo")
        response = await http.post(
            f"{API}/reports/weekly/email",
            headers={"Authorization": f"Bearer {demo.json()['access_token']}"},
        )
    assert response.status_code == 403


async def test_weekly_report_email_rate_limited(account: Account, app: FastAPI) -> None:
    app.state.settings.rate_limit_enabled = True
    statuses = [(await account.post(f"{API}/reports/weekly/email")).status_code for _ in range(6)]
    assert statuses == [202] * 5 + [429]
