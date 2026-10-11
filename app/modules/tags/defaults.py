"""Tags every new account starts with."""

from __future__ import annotations

import uuid
from typing import NamedTuple

from app.modules.tags.models import Tag


class DefaultTag(NamedTuple):
    name: str
    color: str
    icon: str


DEFAULT_TAGS: tuple[DefaultTag, ...] = (
    DefaultTag("Caffeine", "#f59e0b", "coffee"),
    DefaultTag("Alcohol", "#ef4444", "wine"),
    DefaultTag("Exercise", "#10b981", "dumbbell"),
    DefaultTag("Late meal", "#f97316", "utensils"),
    DefaultTag("Stress", "#e11d48", "brain"),
    DefaultTag("Screen time", "#6366f1", "smartphone"),
    DefaultTag("Nap", "#0ea5e9", "bed"),
    DefaultTag("Travel", "#8b5cf6", "plane"),
    DefaultTag("Medication", "#14b8a6", "pill"),
    DefaultTag("Reading", "#a855f7", "book-open"),
    DefaultTag("Meditation", "#22c55e", "leaf"),
)


def build_default_tags(user_id: uuid.UUID) -> list[Tag]:
    return [
        Tag(
            id=uuid.uuid4(),
            user_id=user_id,
            name=t.name,
            color=t.color,
            icon=t.icon,
            is_system=True,
        )
        for t in DEFAULT_TAGS
    ]
