"""Application settings loaded from the environment (12-factor style)."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app import __version__

_INSECURE_SECRET_KEYS = frozenset(
    {"change-me", "changeme", "secret", "development-secret-key-change-me-please-32b"}
)
DEFAULT_DEV_SECRET = "development-secret-key-change-me-please-32b"  # noqa: S105 - dev only, rejected in production


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ app
    app_name: str = "SleepTrack API"
    version: str = __version__
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False
    api_prefix: str = "/api/v1"
    frontend_url: str = "http://localhost:3000"
    public_api_url: str = "http://localhost:8000"

    # ------------------------------------------------------------------ security
    secret_key: SecretStr = SecretStr(DEFAULT_DEV_SECRET)
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "sleeptrack-api"
    jwt_audience: str = "sleeptrack-app"
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=365)
    refresh_session_ttl_hours: int = Field(default=24, ge=1, le=24 * 7)
    # Concurrent refreshes from several tabs present the same token within a few
    # milliseconds; inside this window a reused token is rejected without
    # revoking the whole family.
    refresh_reuse_grace_seconds: int = Field(default=10, ge=0, le=120)
    email_token_ttl_hours: int = Field(default=48, ge=1)
    password_reset_ttl_minutes: int = Field(default=30, ge=5)
    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_cost: int = Field(default=65536, ge=1024)
    argon2_parallelism: int = Field(default=4, ge=1)

    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    allowed_hosts: Annotated[list[str], NoDecode] = ["*"]
    cookie_secure: bool | None = None
    cookie_domain: str | None = None
    max_request_body_bytes: int = Field(default=2 * 1024 * 1024, ge=1024)
    max_import_rows: int = Field(default=5000, ge=1)

    # ------------------------------------------------------------------ features
    registration_open: bool = True
    email_verification_required: bool = False
    demo_mode_enabled: bool = True
    demo_account_ttl_hours: int = Field(default=24, ge=1)
    demo_nights: int = Field(default=90, ge=7, le=400)
    rate_limit_enabled: bool = True

    # ------------------------------------------------------------------ infrastructure
    database_url: str = "postgresql+asyncpg://sleeptrack:sleeptrack@localhost:5432/sleeptrack"
    database_pool_size: int = Field(default=10, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)
    database_echo: bool = False
    redis_url: str = "redis://localhost:6379/0"
    analytics_cache_ttl_seconds: int = Field(default=300, ge=0)
    # Execute background jobs in-process instead of enqueueing them to ARQ.
    # Used by the test-suite and handy for single-process deployments.
    jobs_eager: bool = False

    # ------------------------------------------------------------------ email
    email_provider: Literal["console", "smtp", "memory"] = "console"
    email_from: str = "SleepTrack <no-reply@sleeptrack.app>"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_use_tls: bool = False
    smtp_start_tls: bool = False
    smtp_timeout_seconds: float = 10.0

    # ------------------------------------------------------------------ oauth
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    # OAuth callbacks are routed through the frontend origin so the session
    # cookies land on the same site the browser talks to.
    oauth_redirect_base_url: str | None = None

    # ------------------------------------------------------------------ observability
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] | None = None
    metrics_enabled: bool = True
    sentry_dsn: SecretStr | None = None
    sentry_traces_sample_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    otel_enabled: bool = False
    otel_service_name: str = "sleeptrack-api"
    otel_exporter_otlp_endpoint: str | None = None

    # ------------------------------------------------------------------ validators
    @field_validator("cors_origins", "allowed_hosts", mode="before")
    @classmethod
    def _parse_csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("frontend_url", "public_api_url", "oauth_redirect_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str | None) -> str | None:
        return value.rstrip("/") if value else value

    @model_validator(mode="after")
    def _validate_production(self) -> Self:
        if self.environment is Environment.PRODUCTION:
            secret = self.secret_key.get_secret_value()
            if len(secret) < 32 or secret.lower() in _INSECURE_SECRET_KEYS:
                raise ValueError(
                    "SECRET_KEY must be at least 32 characters and not a default in production"
                )
            if self.debug:
                raise ValueError("DEBUG must be disabled in production")
            if "*" in self.cors_origins:
                raise ValueError("CORS_ORIGINS cannot contain '*' in production")
            if self.email_provider == "memory":
                raise ValueError("EMAIL_PROVIDER=memory is only for tests")
            if self.cookie_secure is False:
                raise ValueError("COOKIE_SECURE cannot be disabled in production")
        return self

    # ------------------------------------------------------------------ derived
    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def is_test(self) -> bool:
        return self.environment is Environment.TEST

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.environment in {Environment.PRODUCTION, Environment.STAGING}

    @property
    def effective_log_format(self) -> Literal["json", "console"]:
        if self.log_format:
            return self.log_format
        return "console" if self.environment is Environment.DEVELOPMENT else "json"

    @property
    def google_oauth_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def oauth_callback_base(self) -> str:
        return self.oauth_redirect_base_url or self.frontend_url

    @property
    def refresh_cookie_path(self) -> str:
        return f"{self.api_prefix}/auth"


@lru_cache
def get_settings() -> Settings:
    return Settings()
