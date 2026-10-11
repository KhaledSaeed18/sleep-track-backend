"""initial schema

Revision ID: 1223fbdf2993
Revises:
Create Date: 2026-10-11 23:20:22.353611+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1223fbdf2993"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # btree_gist powers the overlap exclusion constraint; pg_trgm backs notes search.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), server_default="", nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("avatar_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "role",
            sa.Enum("user", "admin", name="user_role", native_enum=False, create_constraint=True),
            server_default="user",
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "is_email_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index("ix_users_created_at", "users", ["created_at"], unique=False)
    op.create_index(
        "ix_users_demo_created_at",
        "users",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("is_demo"),
    )
    op.create_index(
        "ux_users_email_lower", "users", [sa.literal_column("lower(email)")], unique=True
    )
    op.create_table(
        "action_tokens",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum(
                "verify_email",
                "reset_password",
                name="action_token_purpose",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_action_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_action_tokens_token_hash")),
    )
    op.create_index(
        "ix_action_tokens_user_id_purpose", "action_tokens", ["user_id", "purpose"], unique=False
    )
    op.create_table(
        "audit_logs",
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_audit_logs_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        "ix_audit_logs_user_id_created_at", "audit_logs", ["user_id", "created_at"], unique=False
    )
    op.create_table(
        "auth_sessions",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("device_label", sa.String(length=128), nullable=True),
        sa.Column("remember_me", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
    )
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"], unique=False)
    op.create_index(
        "ix_auth_sessions_user_id_active",
        "auth_sessions",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "oauth_accounts",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_account_id", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_oauth_accounts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_accounts")),
        sa.UniqueConstraint(
            "provider",
            "provider_account_id",
            name=op.f("uq_oauth_accounts_provider_provider_account_id"),
        ),
    )
    op.create_index(op.f("ix_oauth_accounts_user_id"), "oauth_accounts", ["user_id"], unique=False)
    op.create_table(
        "sleep_goals",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("target_duration_minutes", sa.Integer(), nullable=False),
        sa.Column("target_bedtime", sa.Time(), nullable=False),
        sa.Column("target_wake_time", sa.Time(), nullable=False),
        sa.Column("bedtime_window_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "bedtime_window_minutes BETWEEN 0 AND 180", name=op.f("ck_sleep_goals_window_range")
        ),
        sa.CheckConstraint(
            "target_duration_minutes BETWEEN 180 AND 900",
            name=op.f("ck_sleep_goals_duration_range"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_sleep_goals_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_sleep_goals")),
    )
    op.create_table(
        "sleep_sessions",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "in_progress",
                "completed",
                name="sleep_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("night_of", sa.Date(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("quality", sa.SmallInteger(), nullable=True),
        sa.Column("mood_on_wake", sa.SmallInteger(), nullable=True),
        sa.Column("sleep_latency_minutes", sa.SmallInteger(), nullable=True),
        sa.Column("awakenings", sa.SmallInteger(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("score", sa.SmallInteger(), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "manual",
                "timer",
                "import",
                name="sleep_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        postgresql.ExcludeConstraint(
            (sa.column("user_id"), "="),
            (sa.text("tstzrange(started_at, ended_at, '[)')"), "&&"),
            where=sa.text("ended_at IS NOT NULL"),
            using="gist",
            name="ex_sleep_sessions_no_overlap",
        ),
        sa.CheckConstraint(
            "(status = 'in_progress') = (ended_at IS NULL)",
            name=op.f("ck_sleep_sessions_status_matches_end"),
        ),
        sa.CheckConstraint(
            "awakenings BETWEEN 0 AND 50", name=op.f("ck_sleep_sessions_awakenings_range")
        ),
        sa.CheckConstraint(
            "char_length(notes) <= 2000", name=op.f("ck_sleep_sessions_notes_length")
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at > started_at",
            name=op.f("ck_sleep_sessions_ends_after_start"),
        ),
        sa.CheckConstraint(
            "mood_on_wake BETWEEN 1 AND 5", name=op.f("ck_sleep_sessions_mood_on_wake_range")
        ),
        sa.CheckConstraint("quality BETWEEN 1 AND 5", name=op.f("ck_sleep_sessions_quality_range")),
        sa.CheckConstraint("score BETWEEN 0 AND 100", name=op.f("ck_sleep_sessions_score_range")),
        sa.CheckConstraint(
            "sleep_latency_minutes BETWEEN 0 AND 240", name=op.f("ck_sleep_sessions_latency_range")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_sleep_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sleep_sessions")),
    )
    op.create_index(
        "ix_sleep_sessions_notes_trgm",
        "sleep_sessions",
        ["notes"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"notes": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_sleep_sessions_user_id_night_of",
        "sleep_sessions",
        ["user_id", "night_of"],
        unique=False,
    )
    op.create_index(
        "ix_sleep_sessions_user_id_started_at",
        "sleep_sessions",
        ["user_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "ux_sleep_sessions_user_id_in_progress",
        "sleep_sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'in_progress'"),
    )
    op.create_table(
        "tags",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("color", sa.String(length=7), nullable=False),
        sa.Column("icon", sa.String(length=50), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("color ~ '^#[0-9a-fA-F]{6}$'", name=op.f("ck_tags_color_hex")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_tags_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
    )
    op.create_index(
        "ux_tags_user_id_name_lower",
        "tags",
        ["user_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
        sa.Column(
            "time_format",
            sa.Enum("24h", "12h", name="time_format", native_enum=False, create_constraint=True),
            server_default="24h",
            nullable=False,
        ),
        sa.Column(
            "week_starts_on",
            sa.Enum(
                "monday", "sunday", name="week_start", native_enum=False, create_constraint=True
            ),
            server_default="monday",
            nullable=False,
        ),
        sa.Column(
            "theme",
            sa.Enum(
                "system", "light", "dark", name="theme", native_enum=False, create_constraint=True
            ),
            server_default="system",
            nullable=False,
        ),
        sa.Column(
            "email_weekly_report", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "email_bedtime_reminder", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_preferences_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_preferences")),
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["auth_sessions.id"],
            name=op.f("fk_refresh_tokens_session_id_auth_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(
        op.f("ix_refresh_tokens_session_id"), "refresh_tokens", ["session_id"], unique=False
    )
    op.create_table(
        "sleep_session_tags",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("tag_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sleep_sessions.id"],
            name=op.f("fk_sleep_session_tags_session_id_sleep_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tags.id"],
            name=op.f("fk_sleep_session_tags_tag_id_tags"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "tag_id", name=op.f("pk_sleep_session_tags")),
    )
    op.create_index(
        op.f("ix_sleep_session_tags_tag_id"), "sleep_session_tags", ["tag_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sleep_session_tags_tag_id"), table_name="sleep_session_tags")
    op.drop_table("sleep_session_tags")
    op.drop_index(op.f("ix_refresh_tokens_session_id"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("user_preferences")
    op.drop_index("ux_tags_user_id_name_lower", table_name="tags")
    op.drop_table("tags")
    op.drop_index(
        "ux_sleep_sessions_user_id_in_progress",
        table_name="sleep_sessions",
        postgresql_where=sa.text("status = 'in_progress'"),
    )
    op.drop_index("ix_sleep_sessions_user_id_started_at", table_name="sleep_sessions")
    op.drop_index("ix_sleep_sessions_user_id_night_of", table_name="sleep_sessions")
    op.drop_index(
        "ix_sleep_sessions_notes_trgm",
        table_name="sleep_sessions",
        postgresql_using="gin",
        postgresql_ops={"notes": "gin_trgm_ops"},
    )
    op.drop_table("sleep_sessions")
    op.drop_table("sleep_goals")
    op.drop_index(op.f("ix_oauth_accounts_user_id"), table_name="oauth_accounts")
    op.drop_table("oauth_accounts")
    op.drop_index(
        "ix_auth_sessions_user_id_active",
        table_name="auth_sessions",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_audit_logs_user_id_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_action_tokens_user_id_purpose", table_name="action_tokens")
    op.drop_table("action_tokens")
    op.drop_index("ux_users_email_lower", table_name="users")
    op.drop_index(
        "ix_users_demo_created_at", table_name="users", postgresql_where=sa.text("is_demo")
    )
    op.drop_index("ix_users_created_at", table_name="users")
    op.drop_table("users")
