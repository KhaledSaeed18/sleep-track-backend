"""Security audit trail."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ClientInfo
from app.core.timeutils import utcnow
from app.modules.auth.models import AuditEvent, AuditLog

logger = structlog.get_logger("app.audit")


def record_audit(
    session: AsyncSession,
    event: AuditEvent,
    *,
    user_id: uuid.UUID | None,
    client: ClientInfo | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Stage an audit entry in the caller's unit of work."""
    session.add(
        AuditLog(
            user_id=user_id,
            event=event.value,
            ip_address=client.ip_address if client else None,
            user_agent=client.user_agent if client else None,
            event_metadata=metadata or {},
            created_at=utcnow(),
        )
    )
    logger.info("audit", audit_event=event.value, user_id=str(user_id) if user_id else None)
