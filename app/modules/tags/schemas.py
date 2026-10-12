from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

TagName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$", to_lower=True)]
IconName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=50),
]


class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str = Field(examples=["Caffeine"])
    color: str = Field(examples=["#f59e0b"])
    icon: str | None = Field(examples=["coffee"], description="lucide icon name (kebab-case)")
    is_system: bool
    usage_count: int = 0
    created_at: datetime


class TagCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"name": "Hot room", "color": "#ef4444", "icon": "thermometer"}
        },
    )

    name: TagName
    color: HexColor = Field(examples=["#6366f1"])
    icon: IconName | None = None


class TagUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: TagName | None = None
    color: HexColor | None = None
    icon: IconName | None = None
