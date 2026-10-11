from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import str_enum


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class TimeFormat(StrEnum):
    H24 = "24h"
    H12 = "12h"


class WeekStart(StrEnum):
    MONDAY = "monday"
    SUNDAY = "sunday"


class Theme(StrEnum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False, server_default="")
    date_of_birth: Mapped[date | None]
    avatar_url: Mapped[str | None] = mapped_column(String(2048))
    role: Mapped[UserRole] = mapped_column(
        str_enum(UserRole, "user_role"), default=UserRole.USER, server_default=UserRole.USER.value
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    is_email_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    email_verified_at: Mapped[datetime | None]
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    last_login_at: Mapped[datetime | None]

    preferences: Mapped[UserPreferences] = relationship(
        back_populates="user", lazy="joined", cascade="all, delete-orphan", uselist=False
    )
    oauth_accounts: Mapped[list[OAuthAccount]] = relationship(
        back_populates="user", lazy="selectin", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ux_users_email_lower", func.lower(text("email")), unique=True),
        Index("ix_users_created_at", "created_at"),
        Index("ix_users_demo_created_at", "created_at", postgresql_where=text("is_demo")),
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def has_password(self) -> bool:
        return self.password_hash is not None

    @property
    def auth_providers(self) -> list[str]:
        providers = ["password"] if self.has_password else []
        providers.extend(sorted({account.provider for account in self.oauth_accounts}))
        return providers


class UserPreferences(TimestampMixin, Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    time_format: Mapped[TimeFormat] = mapped_column(
        str_enum(TimeFormat, "time_format"), default=TimeFormat.H24, server_default="24h"
    )
    week_starts_on: Mapped[WeekStart] = mapped_column(
        str_enum(WeekStart, "week_start"), default=WeekStart.MONDAY, server_default="monday"
    )
    theme: Mapped[Theme] = mapped_column(
        str_enum(Theme, "theme"), default=Theme.SYSTEM, server_default="system"
    )
    email_weekly_report: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    email_bedtime_reminder: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )

    user: Mapped[User] = relationship(back_populates="preferences")


class OAuthAccount(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "oauth_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))

    user: Mapped[User] = relationship(back_populates="oauth_accounts")

    __table_args__ = (UniqueConstraint("provider", "provider_account_id"),)
