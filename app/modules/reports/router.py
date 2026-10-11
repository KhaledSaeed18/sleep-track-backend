from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request, status

from app.core.errors import ForbiddenError, problem_responses
from app.core.rate_limit import RateLimit, RateLimiterDep
from app.db.session import DbSession
from app.modules.analytics.cache import AnalyticsCacheDep
from app.modules.auth.dependencies import CurrentUser
from app.modules.auth.schemas import MessageResponse
from app.modules.reports.schemas import WeeklyReport, WeeklyReportEmailRequest
from app.modules.reports.service import ReportService
from app.worker.queue import JobQueueDep

router = APIRouter(prefix="/reports", tags=["reports"], responses=problem_responses(401))

EMAIL_REPORT_LIMIT = RateLimit(5, 3600)


def get_report_service(
    request: Request, session: DbSession, cache: AnalyticsCacheDep
) -> ReportService:
    return ReportService(session, cache, request.app.state.insight_provider)


ReportServiceDep = Annotated[ReportService, Depends(get_report_service)]


@router.get("/weekly", summary="Weekly sleep report", responses=problem_responses(422))
async def get_weekly_report(
    user: CurrentUser,
    service: ReportServiceDep,
    week_start: Annotated[
        date | None,
        Query(description="First day of the week; defaults to the last full week"),
    ] = None,
) -> WeeklyReport:
    return await service.weekly(user, week_start)


@router.post(
    "/weekly/email",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email the weekly report",
    responses=problem_responses(403, 429),
)
async def email_weekly_report(
    user: CurrentUser,
    service: ReportServiceDep,
    queue: JobQueueDep,
    limiter: RateLimiterDep,
    data: Annotated[WeeklyReportEmailRequest | None, Body()] = None,
) -> MessageResponse:
    """Queues a background job that renders the report and emails it."""
    if user.is_demo:
        raise ForbiddenError("Demo accounts can't receive emails.")
    await limiter.enforce(f"report-email:user:{user.id}", EMAIL_REPORT_LIMIT)
    week_start = (data.week_start if data else None) or service.default_week_start(user)
    await queue.enqueue(
        "send_weekly_report", user_id=str(user.id), week_start=week_start.isoformat()
    )
    return MessageResponse(message=f"Your report for the week of {week_start} is on its way.")
