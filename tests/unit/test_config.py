from __future__ import annotations

import os
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import DEFAULT_DEV_SECRET, Environment, Settings

STRONG_SECRET = "a-production-secret-key-with-more-than-32-characters"


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the test-suite environment so only explicit arguments apply."""
    for name in list(os.environ):
        if name.lower() in Settings.model_fields:
            monkeypatch.delenv(name)


def make_settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


def production(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": Environment.PRODUCTION,
        "secret_key": SecretStr(STRONG_SECRET),
        "cors_origins": "https://app.example.com",
        "email_provider": "smtp",
    }
    values.update(overrides)
    return make_settings(**values)


# =============================================================================== production
class TestProductionValidation:
    def test_valid_production_settings(self) -> None:
        settings = production()
        assert settings.is_production
        assert not settings.is_test

    @pytest.mark.parametrize(
        "secret",
        [
            "x" * 31,
            DEFAULT_DEV_SECRET,
            DEFAULT_DEV_SECRET.upper(),
            "",
        ],
    )
    def test_weak_secret_key_rejected(self, secret: str) -> None:
        with pytest.raises(ValidationError, match="SECRET_KEY must be at least 32 characters"):
            production(secret_key=SecretStr(secret))

    def test_default_secret_key_rejected(self) -> None:
        with pytest.raises(ValidationError, match="SECRET_KEY"):
            make_settings(
                environment="production", cors_origins="https://a.example", email_provider="smtp"
            )

    def test_exactly_32_characters_is_enough(self) -> None:
        assert production(secret_key=SecretStr("k" * 32)).is_production

    def test_debug_rejected(self) -> None:
        with pytest.raises(ValidationError, match="DEBUG must be disabled in production"):
            production(debug=True)

    @pytest.mark.parametrize("origins", ["*", "https://app.example.com,*"])
    def test_wildcard_cors_rejected(self, origins: str) -> None:
        with pytest.raises(ValidationError, match="CORS_ORIGINS cannot contain"):
            production(cors_origins=origins)

    def test_memory_email_rejected(self) -> None:
        with pytest.raises(ValidationError, match="EMAIL_PROVIDER=memory is only for tests"):
            production(email_provider="memory")

    def test_console_email_allowed(self) -> None:
        assert production(email_provider="console").email_provider == "console"

    def test_insecure_cookies_rejected(self) -> None:
        with pytest.raises(ValidationError, match="COOKIE_SECURE cannot be disabled"):
            production(cookie_secure=False)
        assert production(cookie_secure=True).secure_cookies is True

    @pytest.mark.parametrize("environment", ["development", "test", "staging"])
    def test_other_environments_are_lenient(self, environment: str) -> None:
        settings = make_settings(
            environment=environment,
            secret_key=SecretStr("short"),
            debug=True,
            cors_origins="*",
            email_provider="memory",
        )
        assert settings.environment == environment

    def test_production_from_environment_variables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SECRET_KEY", "changeme")
        with pytest.raises(ValidationError, match="SECRET_KEY"):
            make_settings()


# =============================================================================== parsing
class TestCsvLists:
    def test_defaults(self) -> None:
        settings = make_settings()
        assert settings.cors_origins == ["http://localhost:3000"]
        assert settings.allowed_hosts == ["*"]

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("https://a.example", ["https://a.example"]),
            (" https://a.example , https://b.example ", ["https://a.example", "https://b.example"]),
            (
                "https://a.example,,  ,https://b.example,",
                ["https://a.example", "https://b.example"],
            ),
            ("", []),
        ],
    )
    def test_comma_separated_strings(self, raw: str, expected: list[str]) -> None:
        settings = make_settings(cors_origins=raw, allowed_hosts=raw)
        assert settings.cors_origins == expected
        assert settings.allowed_hosts == expected

    def test_lists_pass_through(self) -> None:
        settings = make_settings(cors_origins=["https://a.example", "https://b.example"])
        assert settings.cors_origins == ["https://a.example", "https://b.example"]

    def test_environment_variables_are_not_json_decoded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CORS_ORIGINS", "https://a.example,https://b.example")
        monkeypatch.setenv("ALLOWED_HOSTS", "api.example.com, example.com")
        settings = make_settings()
        assert settings.cors_origins == ["https://a.example", "https://b.example"]
        assert settings.allowed_hosts == ["api.example.com", "example.com"]


class TestUrls:
    def test_trailing_slashes_are_stripped(self) -> None:
        settings = make_settings(
            frontend_url="https://app.example.com/",
            public_api_url="https://api.example.com//",
            oauth_redirect_base_url="https://auth.example.com/",
        )
        assert settings.frontend_url == "https://app.example.com"
        assert settings.public_api_url == "https://api.example.com"
        assert settings.oauth_redirect_base_url == "https://auth.example.com"

    def test_oauth_callback_base_defaults_to_frontend(self) -> None:
        settings = make_settings(frontend_url="https://app.example.com/")
        assert settings.oauth_redirect_base_url is None
        assert settings.oauth_callback_base == "https://app.example.com"

    def test_oauth_callback_base_override(self) -> None:
        settings = make_settings(
            frontend_url="https://app.example.com", oauth_redirect_base_url="https://edge.example"
        )
        assert settings.oauth_callback_base == "https://edge.example"

    def test_refresh_cookie_path(self) -> None:
        assert make_settings().refresh_cookie_path == "/api/v1/auth"
        assert make_settings(api_prefix="/v2").refresh_cookie_path == "/v2/auth"


# =============================================================================== derived
class TestDerived:
    @pytest.mark.parametrize(
        ("environment", "expected"),
        [("development", False), ("test", False), ("staging", True), ("production", True)],
    )
    def test_secure_cookies_follow_environment(self, environment: str, expected: bool) -> None:
        overrides: dict[str, Any] = {"environment": environment}
        if environment == "production":
            overrides.update(
                secret_key=SecretStr(STRONG_SECRET),
                cors_origins="https://a.example",
                email_provider="smtp",
            )
        assert make_settings(**overrides).secure_cookies is expected

    @pytest.mark.parametrize("value", [True, False])
    def test_secure_cookies_explicit(self, value: bool) -> None:
        assert make_settings(environment="development", cookie_secure=value).secure_cookies is value
        assert make_settings(environment="staging", cookie_secure=value).secure_cookies is value

    @pytest.mark.parametrize(
        ("environment", "expected"),
        [("development", "console"), ("test", "json"), ("staging", "json")],
    )
    def test_effective_log_format_default(self, environment: str, expected: str) -> None:
        assert make_settings(environment=environment).effective_log_format == expected

    def test_effective_log_format_in_production(self) -> None:
        assert production().effective_log_format == "json"

    @pytest.mark.parametrize("log_format", ["json", "console"])
    def test_effective_log_format_explicit(self, log_format: str) -> None:
        for environment in ("development", "test"):
            settings = make_settings(environment=environment, log_format=log_format)
            assert settings.effective_log_format == log_format

    @pytest.mark.parametrize(
        ("client_id", "client_secret", "expected"),
        [
            ("client-id", SecretStr("client-secret"), True),
            ("client-id", None, False),
            (None, SecretStr("client-secret"), False),
            ("", SecretStr("client-secret"), False),
            (None, None, False),
        ],
    )
    def test_google_oauth_enabled(
        self, client_id: str | None, client_secret: SecretStr | None, expected: bool
    ) -> None:
        settings = make_settings(google_client_id=client_id, google_client_secret=client_secret)
        assert settings.google_oauth_enabled is expected

    def test_environment_flags(self) -> None:
        settings = make_settings(environment="test")
        assert settings.is_test
        assert not settings.is_production


class TestFieldConstraints:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"access_token_ttl_seconds": 59},
            {"access_token_ttl_seconds": 3601},
            {"argon2_memory_cost": 1000},
            {"max_request_body_bytes": 100},
            {"demo_nights": 6},
            {"sentry_traces_sample_rate": 1.5},
            {"jwt_algorithm": "RS256"},
            {"email_provider": "sendgrid"},
        ],
    )
    def test_out_of_range_values_rejected(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            make_settings(**overrides)
