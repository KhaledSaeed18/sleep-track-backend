from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.core.pagination import Page
from app.modules.sleep.models import SleepSource, SleepStatus
from app.modules.tags.schemas import TagRead
from app.modules.users.schemas import Timezone

Rating = Annotated[int, Field(ge=1, le=5)]
Latency = Annotated[int, Field(ge=0, le=240)]
Awakenings = Annotated[int, Field(ge=0, le=50)]
Notes = Annotated[str, Field(max_length=2000)]
TagIds = Annotated[list[uuid.UUID], Field(max_length=20)]


class SleepSessionRead(BaseModel):
    id: uuid.UUID
    status: SleepStatus
    started_at: datetime
    ended_at: datetime | None
    timezone: str = Field(examples=["Asia/Beirut"])
    night_of: date = Field(description="Local date on which the night began")
    duration_minutes: int | None = Field(examples=[452])
    quality: int | None = Field(ge=1, le=5)
    mood_on_wake: int | None = Field(ge=1, le=5)
    sleep_latency_minutes: int | None
    awakenings: int | None
    notes: str | None
    tags: list[TagRead]
    score: int | None = Field(ge=0, le=100)
    source: SleepSource
    created_at: datetime
    updated_at: datetime


class SleepSessionPage(Page[SleepSessionRead]):
    pass


class SleepSessionDetails(BaseModel):
    """Optional details shared by create, update and timer stop."""

    quality: Rating | None = None
    mood_on_wake: Rating | None = None
    sleep_latency_minutes: Latency | None = None
    awakenings: Awakenings | None = None
    notes: Notes | None = None
    tag_ids: TagIds | None = None


class SleepSessionCreate(SleepSessionDetails):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "started_at": "2026-10-11T20:15:00Z",
                "ended_at": "2026-10-12T04:05:00Z",
                "quality": 4,
                "mood_on_wake": 4,
                "sleep_latency_minutes": 12,
                "awakenings": 1,
                "notes": "Slept well after an evening walk.",
                "tag_ids": [],
                "timezone": "Asia/Beirut",
            }
        },
    )

    started_at: AwareDatetime
    ended_at: AwareDatetime
    timezone: Timezone | None = Field(
        default=None, description="IANA timezone; defaults to the user's preference"
    )


class SleepSessionUpdate(SleepSessionDetails):
    model_config = ConfigDict(extra="forbid")

    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    timezone: Timezone | None = None


class BulkDeleteRequest(BaseModel):
    ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=100)]


class BulkDeleteResponse(BaseModel):
    deleted: int


class TimerState(BaseModel):
    active: bool
    session: SleepSessionRead | None


class TimerStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: Timezone | None = None
    started_at: AwareDatetime | None = Field(
        default=None, description="Defaults to now; useful when starting the timer late"
    )


class TimerStop(SleepSessionDetails):
    model_config = ConfigDict(extra="forbid")

    ended_at: AwareDatetime | None = Field(default=None, description="Defaults to now")
