from __future__ import annotations

import uuid
from datetime import time

from sqlalchemy import CheckConstraint, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class SleepGoal(TimestampMixin, Base):
    __tablename__ = "sleep_goals"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    target_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    target_bedtime: Mapped[time] = mapped_column(nullable=False)
    target_wake_time: Mapped[time] = mapped_column(nullable=False)
    bedtime_window_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("target_duration_minutes BETWEEN 180 AND 900", name="duration_range"),
        CheckConstraint("bedtime_window_minutes BETWEEN 0 AND 180", name="window_range"),
    )
