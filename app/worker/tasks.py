"""Background jobs (executed by ARQ, or inline when ``JOBS_EAGER`` is set)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

import structlog
from redis.exceptions import RedisError
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.core.timeutils import get_zone, utcnow
from app.modules.analytics.cache import AnalyticsCache
from app.modules.auth.audit import record_audit
from app.modules.auth.models import ActionTokenPurpose, AuditEvent
from app.modules.auth.repository import AuthRepository
from app.modules.auth.service import create_action_token
from app.modules.insights.providers import build_insight_provider
from app.modules.reports.service import ReportService, last_full_week_start, report_email_context
from app.modules.users.models import User, UserPreferences, WeekStart
from app.modules.users.repository import UserRepository
from app.worker.context import resources
from app.worker.queue import JobFunction

logger = structlog.get_logger(__name__)

WEEKLY_REPORT_LOCAL_HOUR = 8
_WEEKLY_DEDUPE_TTL = 8 * 86400


async def send_email(
    ctx: dict[str, Any], *, to: str, template: str, context: dict[str, Any]
) -> None:
    await resources(ctx).email.send_template(to, template, context)


async def send_password_reset(ctx: dict[str, Any], *, email: str) -> bool:
    """Issue a single-use reset token and email it. Returns whether one was sent."""
    res = resources(ctx)
    settings = res.settings
    async with res.session_factory() as session:
        user = await UserRepository(session).get_by_email(email)
        if user is None or not user.is_active or user.is_demo:
            logger.info("password_reset_skipped")
            return False
        repo = AuthRepository(session)
        await repo.invalidate_action_tokens(user.id, ActionTokenPurpose.RESET_PASSWORD, utcnow())
        raw = create_action_token(
            repo,
            user.id,
            ActionTokenPurpose.RESET_PASSWORD,
            timedelta(minutes=settings.password_reset_ttl_minutes),
        )
        record_audit(session, AuditEvent.PASSWORD_RESET_REQUESTED, user_id=user.id)
        await session.commit()
        to, first_name = user.email, user.first_name
    await res.email.send_template(
        to,
        "reset_password",
        {
            "first_name": first_name,
            "link": f"{settings.frontend_url}/auth/reset-password?token={raw}",
            "expires_minutes": settings.password_reset_ttl_minutes,
        },
    )
    return True


async def send_weekly_report(ctx: dict[str, Any], *, user_id: str, week_start: str) -> bool:
    res = resources(ctx)
    async with res.session_factory() as session:
        user = await session.get(User, uuid.UUID(user_id))
        if user is None or not user.is_active or user.is_demo:
            return False
        service = ReportService(
            session,
            AnalyticsCache(res.redis, res.settings.analytics_cache_ttl_seconds),
            build_insight_provider(),
        )
        report = await service.weekly(user, date.fromisoformat(week_start))
        context = report_email_context(user, report)
        to = user.email
    await res.email.send_template(to, "weekly_report", context)
    logger.info("weekly_report_sent", user_id=user_id, week_start=week_start)
    return True


async def dispatch_weekly_reports(ctx: dict[str, Any]) -> int:
    """Hourly: send last week's report to users for whom it is now 08:00 on the
    first day of their week, so everyone gets it on their own Monday (or Sunday)."""
    res = resources(ctx)
    now = utcnow()
    async with res.session_factory() as session:
        rows = await session.scalars(
            select(User)
            .join(UserPreferences)
            .where(
                User.is_active.is_(True),
                User.is_demo.is_(False),
                User.is_email_verified.is_(True),
                UserPreferences.email_weekly_report.is_(True),
            )
            .options(selectinload(User.preferences))
        )
        due: list[tuple[str, str]] = []
        for user in rows:
            prefs = user.preferences
            local = now.astimezone(get_zone(prefs.timezone))
            first_weekday = 0 if prefs.week_starts_on is WeekStart.MONDAY else 6
            if local.weekday() != first_weekday or local.hour != WEEKLY_REPORT_LOCAL_HOUR:
                continue
            week_start = last_full_week_start(local.date(), prefs.week_starts_on).isoformat()
            due.append((str(user.id), week_start))

    sent = 0
    for user_id, week_start in due:
        try:
            # Idempotency guard in case the cron fires twice within the hour.
            fresh = await res.redis.set(
                f"weekly-report:{user_id}:{week_start}", "1", nx=True, ex=_WEEKLY_DEDUPE_TTL
            )
        except RedisError:
            fresh = True
        if not fresh:
            continue
        try:
            if await send_weekly_report(ctx, user_id=user_id, week_start=week_start):
                sent += 1
        except Exception:
            logger.exception("weekly_report_failed", user_id=user_id)
    logger.info("weekly_reports_dispatched", sent=sent, due=len(due))
    return sent


async def cleanup_demo_accounts(ctx: dict[str, Any]) -> int:
    res = resources(ctx)
    cutoff = utcnow() - timedelta(hours=res.settings.demo_account_ttl_hours)
    async with res.session_factory() as session:
        result = await session.execute(
            delete(User).where(User.is_demo.is_(True), User.created_at < cutoff)
        )
        await session.commit()
    deleted = int(getattr(result, "rowcount", 0) or 0)
    logger.info("demo_accounts_cleaned", deleted=deleted)
    return deleted


async def cleanup_expired_tokens(ctx: dict[str, Any]) -> dict[str, int]:
    """Purge auth sessions and action tokens that expired or were revoked over a
    week ago; the grace period keeps recent history visible for investigations."""
    res = resources(ctx)
    cutoff = utcnow() - timedelta(days=7)
    async with res.session_factory() as session:
        repo = AuthRepository(session)
        sessions = await repo.delete_expired_sessions(cutoff)
        tokens = await repo.delete_expired_action_tokens(cutoff)
        await session.commit()
    logger.info("expired_tokens_cleaned", sessions=sessions, action_tokens=tokens)
    return {"sessions": sessions, "action_tokens": tokens}


JOB_FUNCTIONS: dict[str, JobFunction] = {
    "send_email": send_email,
    "send_password_reset": send_password_reset,
    "send_weekly_report": send_weekly_report,
    "dispatch_weekly_reports": dispatch_weekly_reports,
    "cleanup_demo_accounts": cleanup_demo_accounts,
    "cleanup_expired_tokens": cleanup_expired_tokens,
}
