"""Resources shared by background jobs, whether run by ARQ or eagerly in-process."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.session import SessionFactory
from app.integrations.email.renderer import EmailSender

RESOURCES_KEY = "resources"


@dataclass(slots=True)
class JobResources:
    settings: Settings
    engine: AsyncEngine
    session_factory: SessionFactory
    redis: Redis
    email: EmailSender


def resources(ctx: dict[str, Any]) -> JobResources:
    value: JobResources = ctx[RESOURCES_KEY]
    return value
