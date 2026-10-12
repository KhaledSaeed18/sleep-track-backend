from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.modules.analytics.calculations import ChronotypeLabel, DurationLabel
from app.modules.tags.schemas import TagRead


class DateRange(BaseModel):
    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True)

    from_: date = Field(alias="from", examples=["2026-09-12"])
    to: date = Field(examples=["2026-10-11"])
    days: int = Field(examples=[30])


class OverviewAverages(BaseModel):
    duration_minutes: int | None = Field(examples=[444])
    quality: float | None = Field(examples=[3.8])
    score: int | None = Field(examples=[78])
    sleep_latency_minutes: int | None = Field(examples=[14])
    awakenings: float | None = Field(examples=[1.2])
    mood_on_wake: float | None = Field(examples=[3.6])
    bedtime: str | None = Field(examples=["23:41"], description="Circular mean, local HH:MM")
    wake_time: str | None = Field(examples=["07:12"], description="Circular mean, local HH:MM")


class PreviousPeriod(BaseModel):
    duration_minutes: int | None
    quality: float | None
    score: int | None
    consistency_score: int | None


class AnalyticsOverview(BaseModel):
    range: DateRange
    nights_tracked: int
    total_sleep_minutes: int
    averages: OverviewAverages
    consistency_score: int | None = Field(
        description="100 minus the scaled circular std-dev of bedtime and wake time"
    )
    goal_attainment_rate: float | None = Field(
        description="Share of tracked nights meeting the duration goal (15 min tolerance)"
    )
    sleep_debt_minutes: int = Field(description="Net shortfall vs goal over the last 14 days")
    current_streak: int
    longest_streak: int
    previous_period: PreviousPeriod | None


class DailyPoint(BaseModel):
    date: date
    duration_minutes: int | None
    quality: float | None
    score: int | None
    bedtime: str | None
    wake_time: str | None
    sessions: int


class WeekdayStat(BaseModel):
    weekday: int = Field(ge=0, le=6, description="0 = Monday")
    nights: int
    avg_duration_minutes: int | None
    avg_quality: float | None
    avg_bedtime: str | None
    avg_wake_time: str | None


class TagImpactRead(BaseModel):
    tag: TagRead
    nights_with: int
    avg_quality_with: float | None
    avg_quality_without: float | None
    avg_duration_with: int | None
    avg_duration_without: int | None
    quality_impact: float | None
    duration_impact_minutes: int | None


class DurationBucket(BaseModel):
    bucket: DurationLabel
    count: int


class QualityBucket(BaseModel):
    rating: int = Field(ge=1, le=5)
    count: int


class BedtimeBucket(BaseModel):
    hour: int = Field(ge=0, le=23)
    count: int


class Distribution(BaseModel):
    duration: list[DurationBucket]
    quality: list[QualityBucket]
    bedtime: list[BedtimeBucket]


class Chronotype(BaseModel):
    chronotype: ChronotypeLabel
    mid_sleep: str | None = Field(examples=["03:20"], description="Mid-sleep on free nights")
    social_jetlag_minutes: int | None = Field(examples=[55])
    nights_analyzed: int
