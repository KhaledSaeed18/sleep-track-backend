from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ClockTime = Annotated[
    str,
    StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$"),
    Field(description="Local clock time, 24h `HH:MM`", examples=["23:00"]),
]


class RecommendedRange(BaseModel):
    min_hours: int = Field(examples=[7])
    max_hours: int = Field(examples=[9])
    age_group: str | None = Field(
        examples=["adult"],
        description=(
            "National Sleep Foundation age group derived from date_of_birth: newborn, infant, "
            "toddler, preschool, school_age, teen, young_adult, adult, older_adult."
        ),
    )


class GoalRead(BaseModel):
    target_duration_minutes: int = Field(examples=[480])
    target_bedtime: str = Field(examples=["23:00"])
    target_wake_time: str = Field(examples=["07:00"])
    bedtime_window_minutes: int = Field(examples=[30])
    recommended: RecommendedRange


class GoalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_duration_minutes: int = Field(ge=180, le=900, examples=[480])
    target_bedtime: ClockTime
    target_wake_time: ClockTime
    bedtime_window_minutes: int = Field(ge=0, le=180, examples=[30])
