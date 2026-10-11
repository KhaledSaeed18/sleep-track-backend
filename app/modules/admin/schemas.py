from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

from app.core.pagination import Page
from app.modules.users.models import UserRole
from app.modules.users.schemas import UserBase


class DailyCount(BaseModel):
    date: date
    count: int


class AdminStats(BaseModel):
    users_total: int
    users_active_7d: int
    users_new_7d: int
    sessions_total: int
    sessions_7d: int
    signups_daily: list[DailyCount]


class AdminUser(UserBase):
    is_active: bool
    sessions_count: int


class AdminUserPage(Page[AdminUser]):
    pass


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool | None = None
    role: UserRole | None = None
