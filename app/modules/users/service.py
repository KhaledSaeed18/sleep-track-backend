from __future__ import annotations

import bisect
import uuid
from datetime import UTC, datetime
from typing import Any

import orjson
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ClientInfo
from app.core.errors import ForbiddenError, NotFoundError, ValidationFailedError
from app.core.pagination import Page, PageParams
from app.core.rate_limit import RateLimit, RateLimiter
from app.core.security import AccessTokenClaims, PasswordHasher
from app.core.timeutils import night_of, utcnow
from app.modules.analytics.cache import AnalyticsCache
from app.modules.analytics.calculations import local_minutes, sleep_score
from app.modules.auth.audit import record_audit
from app.modules.auth.models import AuditEvent
from app.modules.auth.repository import AuthRepository
from app.modules.auth.revocation import TokenRevocationStore
from app.modules.goals.models import SleepGoal
from app.modules.goals.service import goal_targets, load_goal_targets
from app.modules.sleep.models import SleepSession, SleepSource, SleepStatus
from app.modules.sleep.repository import SleepRepository
from app.modules.sleep.service import duration_minutes, validate_window
from app.modules.tags.models import Tag
from app.modules.tags.repository import TagRepository
from app.modules.users.data_transfer import (
    IMPORT_REQUIRED_COLUMNS,
    CsvFormatError,
    ParsedRow,
    RowError,
    parse_import_row,
    read_csv,
    sessions_to_csv,
)
from app.modules.users.models import User
from app.modules.users.schemas import (
    AuditLogEntry,
    ChangePasswordRequest,
    DeleteAccountRequest,
    DeviceSession,
    ImportResult,
    ImportRowError,
    Preferences,
    PreferencesUpdate,
    UserRead,
    UserUpdate,
)

IMPORTED_TAG_COLOR = "#64748b"
# Re-entering the current password is a second factor for sensitive actions; a
# stolen access token must not be able to brute-force it (or burn Argon2 CPU).
PASSWORD_CHECK_LIMIT = RateLimit(10, 3600)
MAX_REPORTED_IMPORT_ERRORS = 100


class AuditLogPage(Page[AuditLogEntry]):
    pass


class _IntervalIndex:
    """Sorted, non-overlapping intervals with O(log n) overlap checks."""

    def __init__(self, intervals: list[tuple[datetime, datetime]]) -> None:
        self._items = sorted(intervals)
        self._starts = [start for start, _ in self._items]

    def overlaps(self, start: datetime, end: datetime) -> bool:
        index = bisect.bisect_left(self._starts, end)
        # Only the latest interval starting before ``end`` can reach ``start``
        # because stored intervals never overlap each other.
        return index > 0 and self._items[index - 1][1] > start

    def add(self, start: datetime, end: datetime) -> None:
        index = bisect.bisect_left(self._starts, start)
        self._starts.insert(index, start)
        self._items.insert(index, (start, end))


def _validated_row(
    position: int, raw: dict[str, str | None], tz: str, now: datetime, index: _IntervalIndex
) -> ParsedRow:
    parsed = parse_import_row(position, raw, tz)
    try:
        validate_window(parsed.started_at, parsed.ended_at, now)
    except ValidationFailedError as exc:
        error = exc.errors[0] if exc.errors else None
        field = error.field if error else "ended_at"
        message = error.message if error else "invalid time range"
        raise RowError(f"{field} {message}") from exc
    if index.overlaps(parsed.started_at, parsed.ended_at):
        raise RowError("overlaps an existing session")
    return parsed


class UserService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        hasher: PasswordHasher,
        revocations: TokenRevocationStore,
        cache: AnalyticsCache,
        limiter: RateLimiter,
    ) -> None:
        self.session = session
        self.hasher = hasher
        self.revocations = revocations
        self.cache = cache
        self.limiter = limiter
        self.auth_repo = AuthRepository(session)

    # ------------------------------------------------------------------ profile
    async def update_profile(self, user: User, data: UserUpdate) -> UserRead:
        provided = data.model_fields_set
        if data.first_name is not None:
            user.first_name = data.first_name
        if data.last_name is not None:
            user.last_name = data.last_name
        if "date_of_birth" in provided:
            user.date_of_birth = data.date_of_birth
        if "avatar_url" in provided:
            user.avatar_url = str(data.avatar_url) if data.avatar_url else None
        await self.session.commit()
        return UserRead.model_validate(user)

    async def update_preferences(self, user: User, data: PreferencesUpdate) -> Preferences:
        prefs = user.preferences
        changes = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
        timezone_changed = "timezone" in changes and changes["timezone"] != prefs.timezone
        for key, value in changes.items():
            setattr(prefs, key, value)
        await self.session.commit()
        if timezone_changed:
            await self.cache.invalidate(user.id)
        return Preferences.model_validate(prefs)

    # ------------------------------------------------------------------ password
    async def _limit_password_checks(self, user: User) -> None:
        await self.limiter.enforce(f"password-check:user:{user.id}", PASSWORD_CHECK_LIMIT)

    async def change_password(
        self,
        user: User,
        data: ChangePasswordRequest,
        claims: AccessTokenClaims,
        client: ClientInfo,
    ) -> None:
        if user.is_demo:
            raise ForbiddenError("Demo accounts cannot set a password.")
        if user.has_password:
            if not data.current_password:
                raise ValidationFailedError.for_field("current_password", "is required")
            await self._limit_password_checks(user)
            valid, _ = self.hasher.verify(data.current_password, user.password_hash)
            if not valid:
                raise ValidationFailedError.for_field(
                    "current_password", "is incorrect", code="invalid_credentials"
                )
        if data.current_password and data.current_password == data.new_password:
            raise ValidationFailedError.for_field(
                "new_password", "must differ from the current password"
            )
        user.password_hash = self.hasher.hash(data.new_password)
        revoked = await self.auth_repo.revoke_sessions(
            user.id, reason="password_changed", now=utcnow(), except_session_id=claims.session_id
        )
        record_audit(
            self.session,
            AuditEvent.PASSWORD_CHANGED,
            user_id=user.id,
            client=client,
            metadata={"sessions_revoked": len(revoked)},
        )
        await self.session.commit()
        await self.revocations.revoke_sessions(revoked)

    # ------------------------------------------------------------------ devices
    async def list_devices(self, user: User, claims: AccessTokenClaims) -> list[DeviceSession]:
        sessions = await self.auth_repo.list_active_sessions(user.id, utcnow())
        return [
            DeviceSession(
                id=s.id,
                user_agent=s.user_agent,
                ip_address=s.ip_address,
                device_label=s.device_label,
                created_at=s.created_at,
                last_used_at=s.last_used_at,
                expires_at=s.expires_at,
                current=s.id == claims.session_id,
            )
            for s in sessions
        ]

    async def revoke_device(self, user: User, session_id: uuid.UUID, client: ClientInfo) -> None:
        auth_session = await self.auth_repo.get_session(user.id, session_id)
        if auth_session is None or auth_session.revoked_at is not None:
            raise NotFoundError("Session not found.")
        auth_session.revoked_at = utcnow()
        auth_session.revoked_reason = "revoked_by_user"
        record_audit(
            self.session,
            AuditEvent.SESSION_REVOKED,
            user_id=user.id,
            client=client,
            metadata={"session_id": str(session_id)},
        )
        await self.session.commit()
        await self.revocations.revoke_sessions([session_id])

    # ------------------------------------------------------------------ audit log
    async def audit_log(self, user: User, page: PageParams) -> AuditLogPage:
        entries, total = await self.auth_repo.list_audit_logs(
            user.id, offset=page.offset, limit=page.limit
        )
        items = [
            AuditLogEntry(
                id=e.id,
                event=e.event,
                ip_address=e.ip_address,
                user_agent=e.user_agent,
                metadata=e.event_metadata,
                created_at=e.created_at,
            )
            for e in entries
        ]
        return AuditLogPage.build(items, total=total, params=page)

    # ------------------------------------------------------------------ export
    async def _session_rows(self, user: User) -> list[dict[str, Any]]:
        sessions = await SleepRepository(self.session).list_all(user.id)
        return [
            {
                "id": str(s.id),
                "status": s.status.value,
                "started_at": s.started_at.isoformat(),
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "timezone": s.timezone,
                "night_of": s.night_of.isoformat(),
                "duration_minutes": s.duration_minutes,
                "quality": s.quality,
                "mood_on_wake": s.mood_on_wake,
                "sleep_latency_minutes": s.sleep_latency_minutes,
                "awakenings": s.awakenings,
                "score": s.score,
                "source": s.source.value,
                "notes": s.notes,
                "tags": [t.name for t in s.tags],
            }
            for s in sessions
        ]

    async def export(self, user: User, fmt: str, client: ClientInfo) -> tuple[str, str, str]:
        """Return ``(body, media_type, filename)``."""
        rows = await self._session_rows(user)
        stamp = utcnow().strftime("%Y-%m-%d")
        record_audit(
            self.session,
            AuditEvent.DATA_EXPORTED,
            user_id=user.id,
            client=client,
            metadata={"format": fmt, "sessions": len(rows)},
        )
        await self.session.commit()
        if fmt == "csv":
            completed = [r for r in rows if r["status"] == SleepStatus.COMPLETED.value]
            return sessions_to_csv(completed), "text/csv", f"sleeptrack-sessions-{stamp}.csv"

        goal = await self.session.get(SleepGoal, user.id)
        tags = await TagRepository(self.session).list_all(user.id)
        targets = goal_targets(goal)
        payload = {
            "exported_at": utcnow().isoformat(),
            "format_version": 1,
            "user": UserRead.model_validate(user).model_dump(mode="json"),
            "goals": {
                "target_duration_minutes": targets.duration_minutes,
                "target_bedtime": goal.target_bedtime.strftime("%H:%M") if goal else "23:00",
                "target_wake_time": goal.target_wake_time.strftime("%H:%M") if goal else "07:00",
                "bedtime_window_minutes": targets.window_minutes,
            },
            "tags": [
                {"name": t.name, "color": t.color, "icon": t.icon, "is_system": t.is_system}
                for t in tags
            ],
            "sessions": rows,
        }
        body = orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode()
        return body, "application/json", f"sleeptrack-export-{stamp}.json"

    # ------------------------------------------------------------------ import
    async def import_csv(
        self, user: User, content: str, *, max_rows: int, client: ClientInfo
    ) -> ImportResult:
        try:
            header, rows = read_csv(content)
        except CsvFormatError as exc:
            raise ValidationFailedError.for_field("file", f"is not valid CSV ({exc})") from exc
        missing = [column for column in IMPORT_REQUIRED_COLUMNS if column not in header]
        if missing:
            raise ValidationFailedError.for_field(
                "file", f"CSV header must include: {', '.join(missing)}"
            )
        if len(rows) > max_rows:
            raise ValidationFailedError.for_field("file", f"at most {max_rows} rows per import")

        tz = user.preferences.timezone
        goal = await load_goal_targets(self.session, user.id)
        tag_repo = TagRepository(self.session)
        tags_by_name = {t.name.lower(): t for t in await tag_repo.list_all(user.id)}
        existing = await SleepRepository(self.session).list_all(user.id)
        open_ended = datetime.max.replace(tzinfo=UTC)
        index = _IntervalIndex([(s.started_at, s.ended_at or open_ended) for s in existing])

        imported = 0
        errors: list[ImportRowError] = []
        now = utcnow()
        for position, raw in enumerate(rows, start=2):  # row 1 is the header
            try:
                parsed = _validated_row(position, raw, tz, now, index)
            except RowError as exc:
                errors.append(ImportRowError(row=position, message=str(exc)))
                continue

            session_tags: list[Tag] = []
            for name in parsed.tags:
                tag = tags_by_name.get(name.lower())
                if tag is None:
                    tag = Tag(id=uuid.uuid4(), user_id=user.id, name=name, color=IMPORTED_TAG_COLOR)
                    tag_repo.add(tag)
                    tags_by_name[name.lower()] = tag
                session_tags.append(tag)

            minutes = duration_minutes(parsed.started_at, parsed.ended_at)
            self.session.add(
                SleepSession(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    status=SleepStatus.COMPLETED,
                    source=SleepSource.IMPORT,
                    started_at=parsed.started_at,
                    ended_at=parsed.ended_at,
                    timezone=tz,
                    night_of=night_of(parsed.started_at, tz),
                    duration_minutes=minutes,
                    quality=parsed.quality,
                    notes=parsed.notes,
                    score=sleep_score(
                        duration_minutes=minutes,
                        bedtime_minutes=local_minutes(parsed.started_at, tz),
                        goal=goal,
                        quality=parsed.quality,
                    ),
                    tags=session_tags,
                )
            )
            index.add(parsed.started_at, parsed.ended_at)
            imported += 1

        record_audit(
            self.session,
            AuditEvent.DATA_IMPORTED,
            user_id=user.id,
            client=client,
            metadata={"imported": imported, "skipped": len(errors)},
        )
        await self.session.commit()
        if imported:
            await self.cache.invalidate(user.id)
        return ImportResult(
            imported=imported, skipped=len(errors), errors=errors[:MAX_REPORTED_IMPORT_ERRORS]
        )

    # ------------------------------------------------------------------ deletion
    async def delete_account(self, user: User, data: DeleteAccountRequest) -> None:
        if user.has_password:
            if not data.password:
                raise ValidationFailedError.for_field("password", "is required")
            await self._limit_password_checks(user)
            valid, _ = self.hasher.verify(data.password, user.password_hash)
            if not valid:
                raise ValidationFailedError.for_field(
                    "password", "is incorrect", code="invalid_credentials"
                )
        active = await self.auth_repo.list_active_sessions(user.id, utcnow())
        await self.session.delete(user)
        await self.session.commit()
        await self.revocations.revoke_sessions([s.id for s in active])
