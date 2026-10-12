"""Jinja2 rendering of transactional emails (HTML + plain-text pairs)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.core.config import Settings
from app.integrations.email.base import EmailMessage, EmailProvider

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / "email"

SUBJECTS: dict[str, str] = {
    "verify_email": "Confirm your SleepTrack email address",
    "reset_password": "Reset your SleepTrack password",
    "password_changed": "Your SleepTrack password was changed",
    "security_alert": "Security alert for your SleepTrack account",
    "weekly_report": "Your SleepTrack weekly report ({week_label})",
    "bedtime_reminder": "Time to start winding down",
}


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    html: str
    text: str


class EmailRenderer:
    def __init__(self, settings: Settings, template_dir: Path = TEMPLATE_DIR) -> None:
        self._settings = settings
        self._env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html"]),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, template: str, context: dict[str, Any]) -> RenderedEmail:
        if template not in SUBJECTS:
            raise ValueError(f"unknown email template '{template}'")
        full_context = {
            "app_name": "SleepTrack",
            "frontend_url": self._settings.frontend_url,
            **context,
        }
        subject = SUBJECTS[template].format(**full_context)
        html = self._env.get_template(f"{template}.html").render(subject=subject, **full_context)
        text = self._env.get_template(f"{template}.txt").render(subject=subject, **full_context)
        return RenderedEmail(subject=subject, html=html, text=text)


class EmailSender:
    def __init__(self, provider: EmailProvider, renderer: EmailRenderer) -> None:
        self.provider = provider
        self.renderer = renderer

    async def send_template(self, to: str, template: str, context: dict[str, Any]) -> None:
        rendered = self.renderer.render(template, context)
        await self.provider.send(
            EmailMessage(to=to, subject=rendered.subject, html=rendered.html, text=rendered.text)
        )
