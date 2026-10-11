from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

from app.modules.analytics.schemas import AnalyticsOverview, DailyPoint
from app.modules.insights.schemas import Insight
from app.modules.sleep.schemas import SleepSessionRead


class WeeklyReport(BaseModel):
    week_start: date
    week_end: date
    overview: AnalyticsOverview
    daily: list[DailyPoint]
    insights: list[Insight]
    best_night: SleepSessionRead | None
    worst_night: SleepSessionRead | None


class WeeklyReportEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date | None = None
