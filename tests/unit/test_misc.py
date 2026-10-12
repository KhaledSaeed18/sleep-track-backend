from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.core.config import Settings
from app.core.errors import ValidationFailedError
from app.core.pagination import MAX_PAGE_SIZE, Page, PageParams, SortSpec, _page_params, parse_sort
from app.integrations.email.renderer import EmailRenderer
from app.modules.analytics import builders
from app.modules.analytics.calculations import GoalTargets
from app.modules.insights.schemas import Insight
from app.modules.reports.schemas import WeeklyReport
from app.modules.reports.service import last_full_week_start, report_email_context
from app.modules.sleep.service import (
    MAX_DURATION,
    MIN_DURATION,
    duration_minutes,
    validate_start,
    validate_window,
)
from app.modules.users.models import User, WeekStart
from tests.unit.helpers import make_point, utc

NOW = utc(2026, 10, 12, 8, 0)


# =============================================================================== pagination
class TestPage:
    @pytest.mark.parametrize(
        ("total", "page_size", "pages"),
        [(0, 20, 0), (1, 20, 1), (20, 20, 1), (21, 20, 2), (45, 20, 3), (100, 100, 1), (7, 1, 7)],
    )
    def test_pages(self, total: int, page_size: int, pages: int) -> None:
        page = Page[int].build([], total=total, params=PageParams(page=1, page_size=page_size))
        assert page.pages == pages

    def test_build(self) -> None:
        page = Page[str].build(("a", "b"), total=42, params=PageParams(page=3, page_size=2))
        assert page.model_dump() == {
            "items": ["a", "b"],
            "total": 42,
            "page": 3,
            "page_size": 2,
            "pages": 21,
        }

    def test_page_size_is_bounded(self) -> None:
        with pytest.raises(ValueError, match="less than or equal to 100"):
            Page[int].build([], total=0, params=PageParams(page_size=MAX_PAGE_SIZE + 1))


class TestPageParams:
    @pytest.mark.parametrize(
        ("page", "page_size", "offset"), [(1, 20, 0), (2, 20, 20), (5, 10, 40), (1, 100, 0)]
    )
    def test_offset_and_limit(self, page: int, page_size: int, offset: int) -> None:
        params = PageParams(page=page, page_size=page_size)
        assert params.offset == offset
        assert params.limit == page_size

    def test_defaults(self) -> None:
        assert _page_params() == PageParams(page=1, page_size=20)


class TestParseSort:
    ALLOWED = ("started_at", "duration_minutes", "quality")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, SortSpec("started_at", descending=True)),
            ("", SortSpec("started_at", descending=True)),
            ("quality", SortSpec("quality", descending=False)),
            ("-quality", SortSpec("quality", descending=True)),
            ("+duration_minutes", SortSpec("duration_minutes", descending=False)),
            ("  -started_at  ", SortSpec("started_at", descending=True)),
        ],
    )
    def test_valid(self, value: str | None, expected: SortSpec) -> None:
        assert parse_sort(value, allowed=self.ALLOWED, default="-started_at") == expected

    @pytest.mark.parametrize("value", ["notes", "-password_hash", "started_at,quality", "-"])
    def test_invalid(self, value: str) -> None:
        with pytest.raises(ValidationFailedError) as caught:
            parse_sort(value, allowed=self.ALLOWED, default="-started_at")
        assert caught.value.errors is not None
        (error,) = caught.value.errors
        assert error.field == "sort"
        assert error.message == (
            "must be one of started_at, duration_minutes, quality (prefix with '-' for descending)"
        )


# =============================================================================== sleep windows
def _window_error(start: datetime, end: datetime) -> tuple[str, str] | None:
    try:
        validate_window(start, end, NOW)
    except ValidationFailedError as exc:
        (error,) = exc.errors or []
        return error.field, error.message
    return None


class TestValidateWindow:
    START = NOW - timedelta(hours=9)

    @pytest.mark.parametrize("length", [MIN_DURATION, timedelta(hours=8), MAX_DURATION])
    def test_valid_lengths(self, length: timedelta) -> None:
        assert _window_error(self.START - MAX_DURATION, self.START - MAX_DURATION + length) is None

    @pytest.mark.parametrize(
        ("length", "message"),
        [
            (timedelta(0), "must be after started_at"),
            (timedelta(minutes=-5), "must be after started_at"),
            (MIN_DURATION - timedelta(seconds=1), "sleep must last at least 10 minutes"),
            (MAX_DURATION + timedelta(seconds=1), "sleep cannot last longer than 20 hours"),
        ],
    )
    def test_invalid_lengths(self, length: timedelta, message: str) -> None:
        start = NOW - timedelta(days=2)
        assert _window_error(start, start + length) == ("ended_at", message)

    def test_future_skew_allowance(self) -> None:
        assert _window_error(self.START, NOW + timedelta(seconds=60)) is None
        assert _window_error(self.START, NOW + timedelta(seconds=61)) == (
            "ended_at",
            "cannot be in the future",
        )


class TestValidateStart:
    def test_past_and_skew(self) -> None:
        validate_start(NOW - timedelta(days=1), NOW)
        validate_start(NOW + timedelta(seconds=60), NOW)

    def test_future(self) -> None:
        with pytest.raises(ValidationFailedError) as caught:
            validate_start(NOW + timedelta(seconds=61), NOW)
        assert caught.value.errors is not None
        assert caught.value.errors[0].field == "started_at"
        assert caught.value.errors[0].message == "cannot be in the future"


@pytest.mark.parametrize(
    ("seconds", "minutes"),
    [(0, 0), (29, 0), (31, 1), (90, 2), (150, 2), (8 * 3600, 480), (8 * 3600 + 30, 480)],
)
def test_duration_minutes(seconds: int, minutes: int) -> None:
    assert duration_minutes(NOW, NOW + timedelta(seconds=seconds)) == minutes


# =============================================================================== reports
@pytest.mark.parametrize(
    ("today", "week_starts_on", "expected"),
    [
        (date(2026, 10, 12), WeekStart.MONDAY, date(2026, 10, 5)),  # Monday
        (date(2026, 10, 11), WeekStart.MONDAY, date(2026, 9, 28)),  # Sunday
        (date(2026, 10, 14), WeekStart.MONDAY, date(2026, 10, 5)),  # Wednesday
        (date(2026, 10, 11), WeekStart.SUNDAY, date(2026, 10, 4)),  # Sunday
        (date(2026, 10, 12), WeekStart.SUNDAY, date(2026, 10, 4)),  # Monday
        (date(2026, 10, 10), WeekStart.SUNDAY, date(2026, 9, 27)),  # Saturday
        (date(2026, 1, 1), WeekStart.MONDAY, date(2025, 12, 22)),  # across a year
    ],
)
def test_last_full_week_start(today: date, week_starts_on: WeekStart, expected: date) -> None:
    assert last_full_week_start(today, week_starts_on) == expected


@pytest.mark.parametrize("week_starts_on", list(WeekStart))
def test_last_full_week_is_always_complete(week_starts_on: WeekStart) -> None:
    first_weekday = 0 if week_starts_on is WeekStart.MONDAY else 6
    for offset in range(14):
        today = date(2026, 10, 1) + timedelta(days=offset)
        start = last_full_week_start(today, week_starts_on)
        assert start.weekday() == first_weekday
        assert 7 <= (today - start).days <= 13


def _report(points_by_night: bool = True) -> WeeklyReport:
    start, end = date(2026, 3, 2), date(2026, 3, 8)
    points = (
        [
            make_point(date(2026, 3, 2), "23:00", 480, quality=4, score=80),
            make_point(date(2026, 3, 3), "23:30", 420, quality=3, score=70),
        ]
        if points_by_night
        else []
    )
    overview = builders.build_overview(
        points=points,
        previous_points=[],
        start=start,
        end=end,
        goal=GoalTargets(),
        tz="UTC",
        today=date(2026, 3, 9),
    )
    insights = [
        Insight(
            id=f"rule-{index}",
            kind="info",
            category="habit",
            title=f"Title {index}",
            message=f"Message {index}",
            priority=50,
        )
        for index in range(5)
    ]
    return WeeklyReport(
        week_start=start,
        week_end=end,
        overview=overview,
        daily=builders.build_daily(points, start, end, "UTC"),
        insights=insights,
        best_night=None,
        worst_night=None,
    )


def test_report_email_context() -> None:
    user = User(email="ada@example.com", first_name="Ada", last_name="Lovelace")
    context = report_email_context(user, _report())

    assert context["first_name"] == "Ada"
    assert context["week_label"] == "Mar 02 - Mar 08, 2026"
    assert context["stats"] == [
        {"label": "Nights tracked", "value": "2"},
        {"label": "Average sleep", "value": "7h 30m"},
        {"label": "Average score", "value": "75/100"},
        {"label": "Average quality", "value": "3.5/5"},
        {"label": "Average bedtime", "value": "23:15"},
        {"label": "Consistency", "value": "87/100"},
        {"label": "Sleep debt", "value": "1h"},
    ]
    assert context["insights"] == [
        {"title": f"Title {index}", "message": f"Message {index}"} for index in range(3)
    ]


def test_report_email_context_without_data() -> None:
    user = User(email="ada@example.com", first_name="Ada", last_name="")
    context = report_email_context(user, _report(points_by_night=False))
    values = {stat["label"]: stat["value"] for stat in context["stats"]}
    assert values == {
        "Nights tracked": "0",
        "Average sleep": "n/a",
        "Average score": "n/a",
        "Average quality": "n/a",
        "Average bedtime": "n/a",
        "Consistency": "n/a",
        "Sleep debt": "0m",
    }


def test_report_email_context_renders() -> None:
    user = User(email="ada@example.com", first_name="Ada", last_name="Lovelace")
    renderer = EmailRenderer(Settings(_env_file=None, frontend_url="https://app.example.com"))
    rendered = renderer.render("weekly_report", report_email_context(user, _report()))
    assert rendered.subject == "Your SleepTrack weekly report (Mar 02 - Mar 08, 2026)"
    assert "Average sleep: 7h 30m" in rendered.text
    assert "Title 2" in rendered.html
    assert "Title 3" not in rendered.html


async def test_seed_refuses_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seeding creates admin@sleeptrack.app with a published password."""
    from pydantic import SecretStr

    from app.core.config import Environment, Settings
    from app.scripts import seed

    production = Settings(
        _env_file=None,
        environment=Environment.PRODUCTION,
        secret_key=SecretStr("a-production-secret-key-with-more-than-32-characters"),
        cors_origins=["https://app.example.com"],
        email_provider="smtp",
    )
    monkeypatch.setattr(seed, "get_settings", lambda: production)

    def no_engine(*_: object, **__: object) -> None:
        raise AssertionError("must not connect to the database")

    monkeypatch.setattr(seed, "create_engine", no_engine)
    with pytest.raises(SystemExit, match="production"):
        await seed.seed()
