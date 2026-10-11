from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

InsightKind = Literal["positive", "warning", "info", "tip"]
InsightCategory = Literal["duration", "consistency", "quality", "timing", "tags", "goal", "habit"]


class InsightMetric(BaseModel):
    label: str = Field(examples=["Quality impact"])
    value: str = Field(examples=["-1.0"])


class Insight(BaseModel):
    id: str = Field(examples=["tag-impact-caffeine"], description="Stable identifier for the rule")
    kind: InsightKind
    category: InsightCategory
    title: str = Field(examples=["Caffeine lowers your sleep quality"])
    message: str
    metric: InsightMetric | None = None
    priority: int = Field(ge=1, le=100, description="Higher is more important")
