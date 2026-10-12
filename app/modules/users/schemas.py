from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from app.core.passwords import validate_password
from app.core.timeutils import utcnow, validate_timezone
from app.modules.users.models import Theme, TimeFormat, UserRole, WeekStart

Timezone = Annotated[str, StringConstraints(max_length=64), AfterValidator(validate_timezone)]
Password = Annotated[str, AfterValidator(validate_password)]
PersonName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
OptionalLastName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]


class Preferences(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timezone: str = Field(examples=["Asia/Beirut"])
    time_format: TimeFormat
    week_starts_on: WeekStart
    theme: Theme
    email_weekly_report: bool
    email_bedtime_reminder: bool


class PreferencesUpdate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"timezone": "Asia/Beirut", "theme": "dark"}},
    )

    timezone: Timezone | None = None
    time_format: TimeFormat | None = None
    week_starts_on: WeekStart | None = None
    theme: Theme | None = None
    email_weekly_report: bool | None = None
    email_bedtime_reminder: bool | None = None


class UserBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str = Field(examples=["khaled@example.com"])
    first_name: str
    last_name: str
    full_name: str
    date_of_birth: date | None
    avatar_url: str | None
    role: UserRole
    is_email_verified: bool
    has_password: bool
    auth_providers: list[str] = Field(examples=[["password", "google"]])
    is_demo: bool
    created_at: datetime
    last_login_at: datetime | None


class UserRead(UserBase):
    preferences: Preferences


def _validate_birth_date(value: date | None) -> date | None:
    if value is None:
        return value
    today = utcnow().date()
    if value > today:
        raise ValueError("cannot be in the future")
    if value.year < today.year - 120:
        raise ValueError("is not a plausible date of birth")
    return value


class UserUpdate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"first_name": "Khaled", "last_name": "Saeed", "date_of_birth": "1999-05-01"}
        },
    )

    first_name: PersonName | None = None
    last_name: OptionalLastName | None = None
    date_of_birth: Annotated[date | None, AfterValidator(_validate_birth_date)] = None
    avatar_url: AnyHttpUrl | None = None

    @field_validator("avatar_url")
    @classmethod
    def _limit_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is not None and len(str(value)) > 2048:
            raise ValueError("must be at most 2048 characters")
        return value


class ChangePasswordRequest(BaseModel):
    current_password: str | None = Field(
        default=None,
        max_length=128,
        description="Required when the account already has a password.",
    )
    new_password: Password


class DeleteAccountRequest(BaseModel):
    password: str | None = Field(default=None, max_length=128)
    confirm: Literal["DELETE"]


class DeviceSession(BaseModel):
    id: uuid.UUID
    user_agent: str | None
    ip_address: str | None
    device_label: str | None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    current: bool


class AuditLogEntry(BaseModel):
    id: uuid.UUID
    event: str
    ip_address: str | None
    user_agent: str | None
    metadata: dict[str, Any]
    created_at: datetime


class ImportRowError(BaseModel):
    row: int
    message: str


class ImportResult(BaseModel):
    imported: int
    skipped: int
    errors: list[ImportRowError]
