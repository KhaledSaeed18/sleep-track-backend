from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    NoActiveTimerError,
    NotFoundError,
    SessionOverlapError,
    TimerAlreadyRunningError,
    ValidationFailedError,
)
from app.core.pagination import PageParams, SortSpec
from app.core.timeutils import night_of, utcnow
from app.db.errors import EXCLUSION_VIOLATION, UNIQUE_VIOLATION, sqlstate
from app.modules.analytics.cache import AnalyticsCache
from app.modules.goals.service import load_goal_targets
from app.modules.sleep.models import SleepSession, SleepSource, SleepStatus
from app.modules.sleep.repository import SessionFilters, SleepRepository
from app.modules.sleep.schemas import (
    SleepSessionCreate,
    SleepSessionDetails,
    SleepSessionPage,
    SleepSessionRead,
    SleepSessionUpdate,
    TimerStart,
    TimerState,
    TimerStop,
)
from app.modules.sleep.scoring import score_session
from app.modules.tags.service import TagService
from app.modules.tags.service import to_read as tag_to_read
from app.modules.users.models import User

MIN_DURATION = timedelta(minutes=10)
MAX_DURATION = timedelta(hours=20)
FUTURE_SKEW = timedelta(seconds=60)
# Far-past instants overflow local-date conversion (year 1 minus an offset).
EARLIEST_START = datetime(1900, 1, 1, tzinfo=UTC)

_DETAIL_FIELDS = ("quality", "mood_on_wake", "sleep_latency_minutes", "awakenings", "notes")


def duration_minutes(start: datetime, end: datetime) -> int:
    return round((end - start).total_seconds() / 60)


def validate_window(start: datetime, end: datetime, now: datetime) -> None:
    """Domain rules from the contract: 10 min - 20 h, not in the future."""
    _validate_not_too_early(start)
    if end <= start:
        raise ValidationFailedError.for_field("ended_at", "must be after started_at")
    if end - start < MIN_DURATION:
        raise ValidationFailedError.for_field("ended_at", "sleep must last at least 10 minutes")
    if end - start > MAX_DURATION:
        raise ValidationFailedError.for_field("ended_at", "sleep cannot last longer than 20 hours")
    if end > now + FUTURE_SKEW:
        raise ValidationFailedError.for_field("ended_at", "cannot be in the future")


def _validate_not_too_early(start: datetime) -> None:
    if start < EARLIEST_START:
        raise ValidationFailedError.for_field("started_at", "must be in 1900 or later")


def validate_start(start: datetime, now: datetime) -> None:
    _validate_not_too_early(start)
    if start > now + FUTURE_SKEW:
        raise ValidationFailedError.for_field("started_at", "cannot be in the future")


def validate_running_start(start: datetime, now: datetime) -> None:
    """A running timer must remain stoppable 'now' without exceeding 20 hours."""
    validate_start(start, now)
    if now - start > MAX_DURATION:
        raise ValidationFailedError.for_field("started_at", "cannot be more than 20 hours ago")


class SleepService:
    def __init__(self, session: AsyncSession, tags: TagService, cache: AnalyticsCache) -> None:
        self.session = session
        self.repo = SleepRepository(session)
        self.tags = tags
        self.cache = cache

    # ------------------------------------------------------------------ mapping
    async def to_read_many(self, sessions: Sequence[SleepSession]) -> list[SleepSessionRead]:
        tag_ids = list({tag.id for s in sessions for tag in s.tags})
        usage = await self.repo.tag_usage(tag_ids)
        return [
            SleepSessionRead(
                id=s.id,
                status=s.status,
                started_at=s.started_at,
                ended_at=s.ended_at,
                timezone=s.timezone,
                night_of=s.night_of,
                duration_minutes=s.duration_minutes,
                quality=s.quality,
                mood_on_wake=s.mood_on_wake,
                sleep_latency_minutes=s.sleep_latency_minutes,
                awakenings=s.awakenings,
                notes=s.notes,
                tags=[tag_to_read(tag, usage.get(tag.id, 0)) for tag in s.tags],
                score=s.score,
                source=s.source,
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
            for s in sessions
        ]

    async def to_read(self, sleep_session: SleepSession) -> SleepSessionRead:
        return (await self.to_read_many([sleep_session]))[0]

    # ------------------------------------------------------------------ queries
    async def list_sessions(
        self, user: User, filters: SessionFilters, sort: SortSpec, page: PageParams
    ) -> SleepSessionPage:
        rows, total = await self.repo.search(user.id, filters, sort, page)
        return SleepSessionPage.build(await self.to_read_many(rows), total=total, params=page)

    async def get(self, user: User, session_id: uuid.UUID) -> SleepSession:
        sleep_session = await self.repo.get(user.id, session_id)
        if sleep_session is None:
            raise NotFoundError("Sleep session not found.")
        return sleep_session

    # ------------------------------------------------------------------ helpers
    async def _apply_details(
        self, user: User, sleep_session: SleepSession, data: SleepSessionDetails
    ) -> None:
        provided = data.model_fields_set
        for field in _DETAIL_FIELDS:
            if field in provided:
                value = getattr(data, field)
                if field == "notes" and isinstance(value, str):
                    value = value.strip() or None
                setattr(sleep_session, field, value)
        if "tag_ids" in provided:
            sleep_session.tags = await self.tags.resolve(user.id, data.tag_ids)

    async def _finalize(self, user: User, sleep_session: SleepSession) -> None:
        if sleep_session.ended_at is not None:
            sleep_session.duration_minutes = duration_minutes(
                sleep_session.started_at, sleep_session.ended_at
            )
            goal = await load_goal_targets(self.session, user.id)
            sleep_session.score = score_session(sleep_session, goal)
        else:
            sleep_session.duration_minutes = None
            sleep_session.score = None
        sleep_session.night_of = night_of(sleep_session.started_at, sleep_session.timezone)

    async def _commit(self) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            code = sqlstate(exc)
            if code == EXCLUSION_VIOLATION:
                raise SessionOverlapError("This session overlaps another session.") from exc
            if code == UNIQUE_VIOLATION:
                raise TimerAlreadyRunningError("A sleep timer is already running.") from exc
            raise

    async def _ensure_no_overlap(
        self,
        user: User,
        start: datetime,
        end: datetime | None,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        if await self.repo.has_overlap(user.id, start, end, exclude_id=exclude_id):
            raise SessionOverlapError("This session overlaps another session.")

    # ------------------------------------------------------------------ commands
    async def create(
        self, user: User, data: SleepSessionCreate, *, source: SleepSource = SleepSource.MANUAL
    ) -> SleepSession:
        validate_window(data.started_at, data.ended_at, utcnow())
        await self._ensure_no_overlap(user, data.started_at, data.ended_at)
        sleep_session = SleepSession(
            id=uuid.uuid4(),
            user_id=user.id,
            status=SleepStatus.COMPLETED,
            started_at=data.started_at,
            ended_at=data.ended_at,
            timezone=data.timezone or user.preferences.timezone,
            source=source,
            tags=[],
        )
        await self._apply_details(user, sleep_session, data)
        await self._finalize(user, sleep_session)
        self.session.add(sleep_session)
        await self._commit()
        await self.cache.invalidate(user.id)
        return sleep_session

    async def update(
        self, user: User, session_id: uuid.UUID, data: SleepSessionUpdate
    ) -> SleepSession:
        sleep_session = await self.get(user, session_id)
        provided = data.model_fields_set
        start = data.started_at if "started_at" in provided and data.started_at else None
        end = data.ended_at if "ended_at" in provided and data.ended_at else None

        if sleep_session.status is SleepStatus.IN_PROGRESS and end is not None:
            raise ValidationFailedError.for_field(
                "ended_at", "stop the running timer to set an end time"
            )
        new_start = start or sleep_session.started_at
        new_end = end or sleep_session.ended_at
        now = utcnow()
        if new_end is not None:
            validate_window(new_start, new_end, now)
        else:
            validate_running_start(new_start, now)
        if start is not None or end is not None:
            await self._ensure_no_overlap(user, new_start, new_end, exclude_id=sleep_session.id)

        sleep_session.started_at = new_start
        sleep_session.ended_at = new_end
        if "timezone" in provided and data.timezone:
            sleep_session.timezone = data.timezone
        await self._apply_details(user, sleep_session, data)
        await self._finalize(user, sleep_session)
        await self._commit()
        await self.cache.invalidate(user.id)
        return sleep_session

    async def delete(self, user: User, session_id: uuid.UUID) -> None:
        sleep_session = await self.get(user, session_id)
        await self.session.delete(sleep_session)
        await self.session.commit()
        await self.cache.invalidate(user.id)

    async def bulk_delete(self, user: User, ids: Sequence[uuid.UUID]) -> int:
        deleted = await self.repo.bulk_delete(user.id, list(dict.fromkeys(ids)))
        await self.session.commit()
        if deleted:
            await self.cache.invalidate(user.id)
        return deleted

    # ------------------------------------------------------------------ timer
    async def timer_state(self, user: User) -> TimerState:
        active = await self.repo.get_active(user.id)
        return TimerState(
            active=active is not None, session=await self.to_read(active) if active else None
        )

    async def start_timer(self, user: User, data: TimerStart) -> SleepSession:
        now = utcnow()
        started_at = data.started_at or now
        validate_running_start(started_at, now)
        if await self.repo.get_active(user.id) is not None:
            raise TimerAlreadyRunningError("A sleep timer is already running.")
        await self._ensure_no_overlap(user, started_at, max(now, started_at))
        sleep_session = SleepSession(
            id=uuid.uuid4(),
            user_id=user.id,
            status=SleepStatus.IN_PROGRESS,
            started_at=started_at,
            ended_at=None,
            timezone=data.timezone or user.preferences.timezone,
            source=SleepSource.TIMER,
            tags=[],
        )
        await self._finalize(user, sleep_session)
        self.session.add(sleep_session)
        await self._commit()
        return sleep_session

    async def stop_timer(self, user: User, data: TimerStop) -> SleepSession:
        sleep_session = await self.repo.get_active(user.id, for_update=True)
        if sleep_session is None:
            raise NoActiveTimerError("There is no running sleep timer.")
        ended_at = data.ended_at or utcnow()
        validate_window(sleep_session.started_at, ended_at, utcnow())
        await self._ensure_no_overlap(
            user, sleep_session.started_at, ended_at, exclude_id=sleep_session.id
        )
        sleep_session.ended_at = ended_at
        sleep_session.status = SleepStatus.COMPLETED
        await self._apply_details(user, sleep_session, data)
        await self._finalize(user, sleep_session)
        await self._commit()
        await self.cache.invalidate(user.id)
        return sleep_session

    async def cancel_timer(self, user: User) -> None:
        sleep_session = await self.repo.get_active(user.id, for_update=True)
        if sleep_session is None:
            raise NoActiveTimerError("There is no running sleep timer.")
        await self.session.delete(sleep_session)
        await self.session.commit()
