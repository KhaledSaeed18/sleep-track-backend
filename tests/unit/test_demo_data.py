from __future__ import annotations

import itertools
import random
import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from statistics import fmean
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutils import daterange, local_today, night_of
from app.modules.analytics.calculations import GoalTargets, local_minutes, sleep_score
from app.modules.sleep import demo_data
from app.modules.sleep.demo_data import NOTES, TAG_EFFECTS, build_history, populate_demo_history
from app.modules.sleep.models import SleepSession, SleepSource, SleepStatus
from app.modules.tags.models import Tag
from app.modules.users.models import User, UserPreferences

END = date(2026, 10, 11)
USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
AFTER_END = datetime(2026, 10, 13, tzinfo=UTC)
TIMEZONES = ["UTC", "Europe/London", "America/New_York", "Asia/Tokyo", "Pacific/Auckland"]


def make_tags(*names: str) -> dict[str, Tag]:
    return {
        name: Tag(id=uuid.uuid4(), user_id=USER_ID, name=name, color="#64748b", is_system=True)
        for name in names
    }


ALL_TAGS = make_tags(*TAG_EFFECTS)


def history(
    *,
    seed: int = 7,
    nights: int = 120,
    timezone: str = "UTC",
    tags: dict[str, Tag] | None = None,
    goal: GoalTargets | None = None,
    skip_probability: float = 0.08,
    now: datetime = AFTER_END,
) -> list[SleepSession]:
    return build_history(
        user_id=USER_ID,
        tags=ALL_TAGS if tags is None else tags,
        timezone=timezone,
        end=END,
        nights=nights,
        rng=random.Random(seed),
        goal=goal,
        skip_probability=skip_probability,
        now=now,
    )


def fingerprint(sessions: list[SleepSession]) -> list[tuple[object, ...]]:
    return [
        (
            s.night_of,
            s.started_at,
            s.ended_at,
            s.duration_minutes,
            s.quality,
            s.mood_on_wake,
            s.sleep_latency_minutes,
            s.awakenings,
            s.score,
            s.notes,
            tuple(tag.name for tag in s.tags),
        )
        for s in sessions
    ]


def test_deterministic_for_a_seed() -> None:
    assert fingerprint(history(seed=11)) == fingerprint(history(seed=11))


def test_different_seeds_differ() -> None:
    assert fingerprint(history(seed=1)) != fingerprint(history(seed=2))


def test_every_night_without_skips() -> None:
    sessions = history(nights=30, skip_probability=0.0)
    assert [s.night_of for s in sessions] == daterange(END - timedelta(days=29), END)


def test_sessions_ending_after_now_are_omitted() -> None:
    complete = history(nights=10, skip_probability=0.0)
    last = complete[-1]
    assert last.ended_at is not None

    cutoff = last.ended_at - timedelta(minutes=1)
    truncated = history(nights=10, skip_probability=0.0, now=cutoff)

    assert fingerprint(truncated) == fingerprint(complete[:-1])
    assert all(s.ended_at is not None and s.ended_at <= cutoff for s in truncated)
    assert fingerprint(history(nights=10, skip_probability=0.0, now=last.ended_at)) == (
        fingerprint(complete)
    )


def test_defaults_to_the_current_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo_data, "utcnow", lambda: datetime(2026, 10, 5, tzinfo=UTC))
    sessions = build_history(
        user_id=USER_ID, tags={}, timezone="UTC", end=END, nights=30, rng=random.Random(1)
    )
    assert sessions
    assert all(
        s.ended_at is not None and s.ended_at <= datetime(2026, 10, 5, tzinfo=UTC) for s in sessions
    )


def test_skip_probability_one_yields_nothing() -> None:
    assert history(skip_probability=1.0) == []


def test_skips_some_nights_by_default() -> None:
    sessions = history(nights=200)
    assert 150 < len(sessions) < 200
    assert all(END - timedelta(days=199) <= s.night_of <= END for s in sessions)


def test_single_night() -> None:
    (session,) = history(nights=1, skip_probability=0.0)
    assert session.night_of == END


@pytest.mark.parametrize("timezone", TIMEZONES)
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_sessions_never_overlap(timezone: str, seed: int) -> None:
    sessions = sorted(history(seed=seed, nights=400, timezone=timezone), key=lambda s: s.started_at)
    for previous, current in itertools.pairwise(sessions):
        assert previous.ended_at is not None
        assert previous.ended_at <= current.started_at


@pytest.mark.parametrize("timezone", TIMEZONES)
def test_night_of_matches_local_start(timezone: str) -> None:
    for session in history(seed=3, nights=400, timezone=timezone):
        assert night_of(session.started_at, timezone) == session.night_of


@pytest.mark.parametrize("timezone", TIMEZONES)
def test_values_respect_database_constraints(timezone: str) -> None:
    sessions = history(seed=5, nights=400, timezone=timezone)
    assert sessions
    for s in sessions:
        assert s.ended_at is not None
        assert s.duration_minutes is not None
        assert s.started_at.utcoffset() == timedelta(0)
        assert s.ended_at - s.started_at == timedelta(minutes=s.duration_minutes)
        assert 240 <= s.duration_minutes <= 660
        assert s.quality is not None
        assert 1 <= s.quality <= 5
        assert s.mood_on_wake is not None
        assert 1 <= s.mood_on_wake <= 5
        assert s.sleep_latency_minutes is not None
        assert 0 <= s.sleep_latency_minutes <= 240
        assert s.awakenings is not None
        assert 0 <= s.awakenings <= 50
        assert s.score is not None
        assert 0 <= s.score <= 100
        assert s.notes is None or (s.notes in NOTES and len(s.notes) <= 2000)


def test_session_metadata() -> None:
    for s in history(nights=20, timezone="Asia/Tokyo"):
        assert s.user_id == USER_ID
        assert s.status is SleepStatus.COMPLETED
        assert s.source is SleepSource.MANUAL
        assert s.timezone == "Asia/Tokyo"
        assert s.created_at == s.updated_at == s.ended_at


@pytest.mark.parametrize("timezone", ["UTC", "Asia/Tokyo"])
def test_score_matches_scoring_formula(timezone: str) -> None:
    goal = GoalTargets(duration_minutes=450, bedtime_minutes=22 * 60 + 30, window_minutes=20)
    for s in history(nights=60, timezone=timezone, goal=goal):
        assert s.duration_minutes is not None
        assert s.score == sleep_score(
            duration_minutes=s.duration_minutes,
            bedtime_minutes=local_minutes(s.started_at, timezone),
            goal=goal,
            quality=s.quality,
            sleep_latency_minutes=s.sleep_latency_minutes,
            awakenings=s.awakenings,
        )


def test_goal_changes_scores() -> None:
    default = [s.score for s in history(seed=9)]
    strict = [s.score for s in history(seed=9, goal=GoalTargets(duration_minutes=600))]
    assert default != strict


def test_tags_only_come_from_provided_dict() -> None:
    tags = make_tags("Caffeine", "Exercise", "Custom")
    sessions = history(nights=200, tags=tags)
    used = {id(tag) for s in sessions for tag in s.tags}
    assert used <= {id(tags["Caffeine"]), id(tags["Exercise"])}
    names = {tag.name for s in sessions for tag in s.tags}
    assert names == {"Caffeine", "Exercise"}


def test_no_tags_when_none_are_provided() -> None:
    assert all(s.tags == [] for s in history(tags={}))


def test_tags_have_their_documented_effects() -> None:
    sessions = history(seed=21, nights=400)
    with_caffeine = [s.quality for s in sessions if any(t.name == "Caffeine" for t in s.tags)]
    without = [s.quality for s in sessions if all(t.name != "Caffeine" for t in s.tags)]
    assert with_caffeine
    assert fmean(q for q in with_caffeine if q is not None) < fmean(
        q for q in without if q is not None
    )


def test_weekends_run_later_and_longer() -> None:
    sessions = history(seed=4, nights=400, tags={})

    def bedtime(s: SleepSession) -> float:
        minutes = local_minutes(s.started_at, "UTC")
        return minutes + 1440 if minutes < 12 * 60 else minutes

    weekend = [s for s in sessions if s.night_of.weekday() in (4, 5)]
    weekday = [s for s in sessions if s.night_of.weekday() not in (4, 5)]
    assert fmean(map(bedtime, weekend)) > fmean(map(bedtime, weekday)) + 30
    assert fmean(s.duration_minutes or 0 for s in weekend) > fmean(
        s.duration_minutes or 0 for s in weekday
    )


class FakeSession:
    """Just enough of ``AsyncSession`` for ``populate_demo_history``."""

    def __init__(self, tags: list[Tag]) -> None:
        self.tags = tags
        self.added: list[Any] = []
        self.flushes = 0

    async def scalars(self, statement: Any) -> list[Tag]:
        assert statement is not None
        return self.tags

    def add_all(self, items: Iterable[Any]) -> None:
        self.added.extend(items)

    async def flush(self) -> None:
        self.flushes += 1


async def test_populate_demo_history_stages_sessions() -> None:
    user = User(id=USER_ID, email="demo@example.com", first_name="Demo", last_name="")
    user.preferences = UserPreferences(timezone="Asia/Tokyo")
    fake = FakeSession(list(make_tags("Caffeine", "Reading").values()))

    count = await populate_demo_history(
        cast("AsyncSession", fake), user, nights=30, rng=random.Random(3)
    )

    assert count == len(fake.added) > 0
    assert fake.flushes == 1
    last_night = local_today("Asia/Tokyo") - timedelta(days=1)
    assert all(isinstance(s, SleepSession) for s in fake.added)
    assert max(s.night_of for s in fake.added) <= last_night
    assert min(s.night_of for s in fake.added) >= last_night - timedelta(days=29)
    assert {t.name for s in fake.added for t in s.tags} <= {"Caffeine", "Reading"}
