from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import str_enum
from app.modules.tags.models import Tag


class SleepStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class SleepSource(StrEnum):
    MANUAL = "manual"
    TIMER = "timer"
    IMPORT = "import"


sleep_session_tags = Table(
    "sleep_session_tags",
    Base.metadata,
    Column(
        "session_id",
        ForeignKey("sleep_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True),
)


class SleepSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sleep_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[SleepStatus] = mapped_column(
        str_enum(SleepStatus, "sleep_status"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    ended_at: Mapped[datetime | None]
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    night_of: Mapped[date] = mapped_column(nullable=False)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    quality: Mapped[int | None] = mapped_column(SmallInteger)
    mood_on_wake: Mapped[int | None] = mapped_column(SmallInteger)
    sleep_latency_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    awakenings: Mapped[int | None] = mapped_column(SmallInteger)
    notes: Mapped[str | None] = mapped_column(Text)
    score: Mapped[int | None] = mapped_column(SmallInteger)
    source: Mapped[SleepSource] = mapped_column(
        str_enum(SleepSource, "sleep_source"), default=SleepSource.MANUAL, nullable=False
    )

    tags: Mapped[list[Tag]] = relationship(
        secondary=sleep_session_tags, lazy="selectin", order_by=Tag.name
    )

    __table_args__ = (
        CheckConstraint("quality BETWEEN 1 AND 5", name="quality_range"),
        CheckConstraint("mood_on_wake BETWEEN 1 AND 5", name="mood_on_wake_range"),
        CheckConstraint("sleep_latency_minutes BETWEEN 0 AND 240", name="latency_range"),
        CheckConstraint("awakenings BETWEEN 0 AND 50", name="awakenings_range"),
        CheckConstraint("score BETWEEN 0 AND 100", name="score_range"),
        CheckConstraint("char_length(notes) <= 2000", name="notes_length"),
        CheckConstraint("ended_at IS NULL OR ended_at > started_at", name="ends_after_start"),
        CheckConstraint("(status = 'in_progress') = (ended_at IS NULL)", name="status_matches_end"),
        Index("ix_sleep_sessions_user_id_night_of", "user_id", "night_of"),
        Index("ix_sleep_sessions_user_id_started_at", "user_id", "started_at"),
        # Trigram index backs the case-insensitive notes search (ILIKE '%q%').
        Index(
            "ix_sleep_sessions_notes_trgm",
            "notes",
            postgresql_using="gin",
            postgresql_ops={"notes": "gin_trgm_ops"},
        ),
        # At most one running timer per user.
        Index(
            "ux_sleep_sessions_user_id_in_progress",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'in_progress'"),
        ),
        # Database-level guard against overlapping completed sessions; the service
        # checks first to return a friendly error, this closes the race window.
        ExcludeConstraint(
            ("user_id", "="),
            (text("tstzrange(started_at, ended_at, '[)')"), "&&"),
            name="ex_sleep_sessions_no_overlap",
            using="gist",
            where=text("ended_at IS NOT NULL"),
        ),
    )
