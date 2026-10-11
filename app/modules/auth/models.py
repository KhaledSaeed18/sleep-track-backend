from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from app.db.types import str_enum


class AuthSession(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A signed-in device. Each session owns a family of rotating refresh tokens."""

    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    device_label: Mapped[str | None] = mapped_column(String(128))
    remember_me: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    last_used_at: Mapped[datetime] = mapped_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None]
    revoked_reason: Mapped[str | None] = mapped_column(String(32))

    tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        Index(
            "ix_auth_sessions_user_id_active",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class RefreshToken(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "refresh_tokens"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    # Set when the token is rotated; presenting it again afterwards is reuse.
    used_at: Mapped[datetime | None]

    session: Mapped[AuthSession] = relationship(back_populates="tokens", lazy="joined")


class ActionTokenPurpose(StrEnum):
    VERIFY_EMAIL = "verify_email"
    RESET_PASSWORD = "reset_password"  # noqa: S105 - enum label


class ActionToken(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Single-use, hashed tokens for email verification and password resets."""

    __tablename__ = "action_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[ActionTokenPurpose] = mapped_column(
        str_enum(ActionTokenPurpose, "action_token_purpose"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    used_at: Mapped[datetime | None]

    __table_args__ = (Index("ix_action_tokens_user_id_purpose", "user_id", "purpose"),)


class AuditEvent(StrEnum):
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    LOGOUT_ALL = "logout_all"
    REGISTER = "register"
    PASSWORD_CHANGED = "password_changed"  # noqa: S105
    PASSWORD_RESET = "password_reset"  # noqa: S105
    PASSWORD_RESET_REQUESTED = "password_reset_requested"  # noqa: S105
    EMAIL_VERIFIED = "email_verified"
    SESSION_REVOKED = "session_revoked"
    TOKEN_REUSE_DETECTED = "token_reuse_detected"  # noqa: S105
    DATA_EXPORTED = "data_exported"
    DATA_IMPORTED = "data_imported"
    OAUTH_LINKED = "oauth_linked"
    ACCOUNT_UPDATED_BY_ADMIN = "account_updated_by_admin"


class AuditLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_logs"

    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    # ``metadata`` is reserved on declarative classes, hence the attribute alias.
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (Index("ix_audit_logs_user_id_created_at", "user_id", "created_at"),)
