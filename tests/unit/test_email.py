from __future__ import annotations

from email.message import EmailMessage as MimeMessage
from typing import Any

import aiosmtplib
import pytest
from jinja2 import UndefinedError
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.email import providers
from app.integrations.email.base import EmailMessage
from app.integrations.email.providers import (
    ConsoleEmailProvider,
    MemoryEmailProvider,
    SmtpEmailProvider,
    build_email_provider,
)
from app.integrations.email.renderer import SUBJECTS, EmailRenderer, EmailSender

FRONTEND = "https://app.example.com"

CONTEXTS: dict[str, dict[str, Any]] = {
    "bedtime_reminder": {"first_name": "Ada", "bedtime": "22:30", "lead_minutes": 30},
    "verify_email": {
        "first_name": "Ada",
        "link": f"{FRONTEND}/auth/verify-email?token=verify-token",
        "expires_hours": 48,
    },
    "reset_password": {
        "first_name": "Ada",
        "link": f"{FRONTEND}/auth/reset-password?token=reset-token",
        "expires_minutes": 30,
    },
    "password_changed": {"first_name": "Ada", "changed_at": "2026-10-12 08:30"},
    "security_alert": {
        "first_name": "Ada",
        "message": "A previously used sign-in token for your account was presented again.",
    },
    "weekly_report": {
        "first_name": "Ada",
        "week_label": "Oct 05 - Oct 11, 2026",
        "stats": [
            {"label": "Nights tracked", "value": "6"},
            {"label": "Average sleep", "value": "7h 25m"},
        ],
        "insights": [
            {"title": "You're meeting your sleep goal", "message": "Keep it up!"},
        ],
    },
}

EXPECTED_FRAGMENTS: dict[str, list[str]] = {
    "bedtime_reminder": ["22:30", "30 minutes from now", f"{FRONTEND}/sleep"],
    "verify_email": ["verify-email?token=verify-token", "48 hours"],
    "reset_password": ["reset-password?token=reset-token", "30 minutes"],
    "password_changed": ["2026-10-12 08:30 (UTC)", f"{FRONTEND}/auth/forgot-password"],
    "security_alert": ["presented again", f"{FRONTEND}/settings/security"],
    "weekly_report": ["Oct 05 - Oct 11, 2026", "7h 25m", "Keep it up!", f"{FRONTEND}/reports"],
}


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"frontend_url": FRONTEND}
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture(scope="module")
def renderer() -> EmailRenderer:
    return EmailRenderer(make_settings())


class RecordingLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **fields: Any) -> None:
        self.records.append((event, fields))


# =============================================================================== renderer
def test_every_template_has_test_context() -> None:
    assert set(CONTEXTS) == set(SUBJECTS) == set(EXPECTED_FRAGMENTS)


@pytest.mark.parametrize("template", sorted(SUBJECTS))
def test_render_template(renderer: EmailRenderer, template: str) -> None:
    rendered = renderer.render(template, CONTEXTS[template])

    assert rendered.subject == SUBJECTS[template].format(**CONTEXTS[template])
    assert rendered.html.lstrip().startswith("<!doctype html>")
    assert f"<title>{rendered.subject}</title>" in rendered.html
    assert f"{FRONTEND}/settings" in rendered.html
    for content in (rendered.html, rendered.text):
        assert "Hi Ada," in content
        for fragment in EXPECTED_FRAGMENTS[template]:
            assert fragment in content
    assert "<" not in rendered.text


def test_weekly_report_subject(renderer: EmailRenderer) -> None:
    rendered = renderer.render("weekly_report", CONTEXTS["weekly_report"])
    assert rendered.subject == "Your SleepTrack weekly report (Oct 05 - Oct 11, 2026)"


def test_weekly_report_without_insights(renderer: EmailRenderer) -> None:
    rendered = renderer.render("weekly_report", {**CONTEXTS["weekly_report"], "insights": []})
    assert "Highlights" not in rendered.html
    assert "Highlights" not in rendered.text


def test_unknown_template(renderer: EmailRenderer) -> None:
    with pytest.raises(ValueError, match="unknown email template 'welcome'"):
        renderer.render("welcome", {})


def test_missing_context_is_an_error(renderer: EmailRenderer) -> None:
    with pytest.raises(UndefinedError):
        renderer.render("password_changed", {"first_name": "Ada"})


def test_missing_subject_placeholder_is_an_error(renderer: EmailRenderer) -> None:
    context = {
        key: value for key, value in CONTEXTS["weekly_report"].items() if key != "week_label"
    }
    with pytest.raises(KeyError):
        renderer.render("weekly_report", context)


def test_html_autoescapes_user_input(renderer: EmailRenderer) -> None:
    name = '<img src=x onerror="alert(1)">'
    rendered = renderer.render("password_changed", {"first_name": name, "changed_at": "now"})
    assert name not in rendered.html
    assert "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;" in rendered.html
    assert name in rendered.text


def test_html_autoescapes_report_values(renderer: EmailRenderer) -> None:
    context = {
        **CONTEXTS["weekly_report"],
        "insights": [{"title": "<b>Tag</b>", "message": "Nights tagged <i>x</i>"}],
    }
    rendered = renderer.render("weekly_report", context)
    assert "<b>Tag</b>" not in rendered.html
    assert "&lt;b&gt;Tag&lt;/b&gt;" in rendered.html


def test_caller_context_overrides_defaults(renderer: EmailRenderer) -> None:
    rendered = renderer.render(
        "password_changed", {"first_name": "Ada", "changed_at": "now", "app_name": "Brand"}
    )
    assert "your Brand account" in rendered.text


async def test_sender_delivers_rendered_message(renderer: EmailRenderer) -> None:
    provider = MemoryEmailProvider()
    sender = EmailSender(provider, renderer)
    await sender.send_template("ada@example.com", "reset_password", CONTEXTS["reset_password"])

    (message,) = provider.outbox
    assert message.to == "ada@example.com"
    assert message.subject == "Reset your SleepTrack password"
    assert "reset-token" in message.html
    assert "reset-token" in message.text
    assert message.headers == {}


# =============================================================================== providers
MESSAGE = EmailMessage(
    to="ada@example.com",
    subject="Reset your SleepTrack password",
    html="<p>Hello</p>",
    text="Hello",
    headers={"List-Unsubscribe": "<https://app.example.com/settings>"},
)


async def test_memory_provider_collects_messages() -> None:
    provider = MemoryEmailProvider()
    assert provider.name == "memory"
    await provider.send(MESSAGE)
    await provider.send(MESSAGE)
    assert provider.outbox == [MESSAGE, MESSAGE]


async def test_console_provider_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = RecordingLogger()
    monkeypatch.setattr(providers, "logger", recorder)
    await ConsoleEmailProvider().send(MESSAGE)
    assert recorder.records == [
        (
            "email_console",
            {"to": "ada@example.com", "subject": "Reset your SleepTrack password", "body": "Hello"},
        )
    ]


class TestSmtpProvider:
    def test_build_mime_structure(self) -> None:
        provider = SmtpEmailProvider(make_settings(email_from="SleepTrack <hello@example.com>"))
        mime = provider._build(MESSAGE)

        assert mime["From"] == "SleepTrack <hello@example.com>"
        assert mime["To"] == "ada@example.com"
        assert mime["Subject"] == "Reset your SleepTrack password"
        assert mime["Date"]
        assert mime["Message-ID"].endswith("@sleeptrack.app>")
        assert mime["List-Unsubscribe"] == "<https://app.example.com/settings>"
        assert mime.get_content_type() == "multipart/alternative"

        parts = list(mime.iter_parts())
        assert [part.get_content_type() for part in parts] == ["text/plain", "text/html"]
        assert isinstance(parts[0], MimeMessage)
        assert isinstance(parts[1], MimeMessage)
        assert parts[0].get_content().strip() == "Hello"
        assert parts[1].get_content().strip() == "<p>Hello</p>"

    def test_message_ids_are_unique(self) -> None:
        provider = SmtpEmailProvider(make_settings())
        assert provider._build(MESSAGE)["Message-ID"] != provider._build(MESSAGE)["Message-ID"]

    async def test_send_passes_connection_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[tuple[MimeMessage, dict[str, Any]]] = []

        async def fake_send(message: MimeMessage, **kwargs: Any) -> None:
            calls.append((message, kwargs))

        monkeypatch.setattr(aiosmtplib, "send", fake_send)
        monkeypatch.setattr(providers, "logger", RecordingLogger())
        settings = make_settings(
            smtp_host="smtp.example.com",
            smtp_port=2525,
            smtp_username="mailer",
            smtp_password=SecretStr("s3cret"),
            smtp_use_tls=True,
            smtp_start_tls=False,
            smtp_timeout_seconds=7.5,
        )

        await SmtpEmailProvider(settings).send(MESSAGE)

        ((mime, kwargs),) = calls
        assert mime["To"] == "ada@example.com"
        assert kwargs == {
            "hostname": "smtp.example.com",
            "port": 2525,
            "username": "mailer",
            "password": "s3cret",
            "use_tls": True,
            "start_tls": False,
            "timeout": 7.5,
        }

    async def test_send_without_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        async def fake_send(message: MimeMessage, **kwargs: Any) -> None:
            captured.update(kwargs)

        monkeypatch.setattr(aiosmtplib, "send", fake_send)
        recorder = RecordingLogger()
        monkeypatch.setattr(providers, "logger", recorder)

        await SmtpEmailProvider(make_settings()).send(MESSAGE)

        assert captured["username"] is None
        assert captured["password"] is None
        assert recorder.records[0][0] == "email_sent"

    async def test_send_errors_propagate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def failing_send(message: MimeMessage, **kwargs: Any) -> None:
            raise ConnectionRefusedError

        monkeypatch.setattr(aiosmtplib, "send", failing_send)
        with pytest.raises(ConnectionRefusedError):
            await SmtpEmailProvider(make_settings()).send(MESSAGE)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("smtp", SmtpEmailProvider),
        ("memory", MemoryEmailProvider),
        ("console", ConsoleEmailProvider),
    ],
)
def test_build_email_provider(name: str, expected: type[object]) -> None:
    provider = build_email_provider(make_settings(email_provider=name))
    assert isinstance(provider, expected)
    assert provider.name == name
