from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutils import local_today
from app.modules.analytics import builders
from app.modules.analytics.cache import AnalyticsCache
from app.modules.analytics.service import AnalyticsService
from app.modules.insights.providers import InsightProvider, format_duration
from app.modules.insights.service import InsightService
from app.modules.reports.schemas import WeeklyReport
from app.modules.sleep.models import SleepSession
from app.modules.sleep.service import SleepService
from app.modules.tags.service import TagService
from app.modules.users.models import User, WeekStart

REPORT_INSIGHT_LIMIT = 5


def last_full_week_start(today: date, week_starts_on: WeekStart) -> date:
    first_weekday = 0 if week_starts_on is WeekStart.MONDAY else 6
    current_week_start = today - timedelta(days=(today.weekday() - first_weekday) % 7)
    return current_week_start - timedelta(days=7)


class ReportService:
    def __init__(
        self, session: AsyncSession, cache: AnalyticsCache, provider: InsightProvider
    ) -> None:
        self.session = session
        self.analytics = AnalyticsService(session, cache)
        self.insights = InsightService(self.analytics, provider)
        self.sleep = SleepService(session, TagService(session, cache), cache)

    def default_week_start(self, user: User) -> date:
        prefs = user.preferences
        return last_full_week_start(local_today(prefs.timezone), prefs.week_starts_on)

    async def weekly(self, user: User, week_start: date | None) -> WeeklyReport:
        start = week_start or self.default_week_start(user)
        end = start + timedelta(days=6)
        points = await self.analytics.points(user, start, end)
        scored = [p for p in points if p.score is not None]
        best_id = max(scored, key=lambda p: (p.score, p.duration_minutes)).id if scored else None
        worst_id = min(scored, key=lambda p: (p.score, p.duration_minutes)).id if scored else None

        nights: dict[Any, SleepSession] = {}
        ids = [i for i in (best_id, worst_id) if i is not None]
        if ids:
            rows = await self.session.scalars(select(SleepSession).where(SleepSession.id.in_(ids)))
            nights = {row.id: row for row in rows}

        insights = await self.insights.insights(user, start, end)
        return WeeklyReport(
            week_start=start,
            week_end=end,
            overview=await self.analytics.overview(user, start, end),
            daily=builders.build_daily(points, start, end, user.preferences.timezone),
            insights=insights[:REPORT_INSIGHT_LIMIT],
            best_night=await self.sleep.to_read(nights[best_id]) if best_id else None,
            worst_night=(
                await self.sleep.to_read(nights[worst_id])
                if worst_id and worst_id != best_id
                else None
            ),
        )


def report_email_context(user: User, report: WeeklyReport) -> dict[str, Any]:
    overview = report.overview
    averages = overview.averages

    def fmt(value: object, suffix: str = "") -> str:
        return "n/a" if value is None else f"{value}{suffix}"

    stats = [
        {"label": "Nights tracked", "value": str(overview.nights_tracked)},
        {
            "label": "Average sleep",
            "value": format_duration(averages.duration_minutes)
            if averages.duration_minutes
            else "n/a",
        },
        {"label": "Average score", "value": fmt(averages.score, "/100")},
        {"label": "Average quality", "value": fmt(averages.quality, "/5")},
        {"label": "Average bedtime", "value": fmt(averages.bedtime)},
        {"label": "Consistency", "value": fmt(overview.consistency_score, "/100")},
        {"label": "Sleep debt", "value": format_duration(overview.sleep_debt_minutes)},
    ]
    label = f"{report.week_start:%b %d} - {report.week_end:%b %d, %Y}"
    return {
        "first_name": user.first_name,
        "week_label": label,
        "stats": stats,
        "insights": [{"title": i.title, "message": i.message} for i in report.insights[:3]],
    }
