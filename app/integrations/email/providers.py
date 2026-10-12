"""Concrete email providers: SMTP, console (development) and in-memory (tests)."""

from __future__ import annotations

from email.message import EmailMessage as MimeMessage
from email.utils import formatdate, make_msgid

import aiosmtplib
import structlog

from app.core.config import Settings
from app.integrations.email.base import EmailMessage, EmailProvider

logger = structlog.get_logger(__name__)


class SmtpEmailProvider:
    name = "smtp"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _build(self, message: EmailMessage) -> MimeMessage:
        mime = MimeMessage()
        mime["From"] = self._settings.email_from
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime["Date"] = formatdate(localtime=False)
        mime["Message-ID"] = make_msgid(domain="sleeptrack.app")
        for key, value in message.headers.items():
            mime[key] = value
        mime.set_content(message.text)
        mime.add_alternative(message.html, subtype="html")
        return mime

    async def send(self, message: EmailMessage) -> None:
        settings = self._settings
        password = settings.smtp_password.get_secret_value() if settings.smtp_password else None
        await aiosmtplib.send(
            self._build(message),
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=password,
            use_tls=settings.smtp_use_tls,
            start_tls=settings.smtp_start_tls,
            timeout=settings.smtp_timeout_seconds,
        )
        logger.info("email_sent", provider=self.name, to=message.to, subject=message.subject)


class ConsoleEmailProvider:
    """Logs emails instead of sending them; the default for local development.

    Bodies carry single-use links (password reset, email verification), so they
    are only logged when ``include_body`` is set, which production never does:
    shipping those tokens to a log pipeline would hand out account access.
    """

    name = "console"

    def __init__(self, *, include_body: bool = True) -> None:
        self._include_body = include_body

    async def send(self, message: EmailMessage) -> None:
        if self._include_body:
            logger.info("email_console", to=message.to, subject=message.subject, body=message.text)
        else:
            logger.warning(
                "email_console_not_delivered",
                to=message.to,
                subject=message.subject,
                reason="console provider in production; body withheld",
            )


class MemoryEmailProvider:
    """Collects messages in memory so tests can assert on them."""

    name = "memory"

    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.outbox.append(message)


def build_email_provider(settings: Settings) -> EmailProvider:
    match settings.email_provider:
        case "smtp":
            return SmtpEmailProvider(settings)
        case "memory":
            return MemoryEmailProvider()
        case _:
            return ConsoleEmailProvider(include_body=not settings.is_production)
