import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from .config import Settings

logger = logging.getLogger("canvas_helper.auth")


class EmailBackend(Protocol):
    async def send_magic_link(self, recipient: str, url: str) -> None: ...


class DevelopmentEmailBackend:
    """Local-only backend. The address is omitted to avoid logging account PII."""

    def __init__(self) -> None:
        self.last_url: str | None = None

    async def send_magic_link(self, recipient: str, url: str) -> None:
        self.last_url = url
        logger.warning("Development magic-link URL: %s", url)


class SMTPEmailBackend:
    def __init__(self, settings: Settings):
        if not settings.smtp_host:
            raise ValueError("CANVAS_HELPER_SMTP_HOST is required for SMTP email")
        self.settings = settings

    async def send_magic_link(self, recipient: str, url: str) -> None:
        message = EmailMessage()
        message["Subject"] = "Sign in to Canvas Helper"
        message["From"] = self.settings.email_from
        message["To"] = recipient
        message.set_content(
            "Use this one-time link to sign in. It expires shortly:\n\n"
            f"{url}\n\nIf you did not request it, ignore this email."
        )
        await asyncio.to_thread(self._send, message)

    def _send(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port) as smtp:
            if self.settings.smtp_starttls:
                smtp.starttls()
            if self.settings.smtp_username:
                smtp.login(
                    self.settings.smtp_username, self.settings.smtp_password or ""
                )
            smtp.send_message(message)


def make_email_backend(settings: Settings) -> EmailBackend:
    if settings.email_backend == "smtp":
        return SMTPEmailBackend(settings)
    return DevelopmentEmailBackend()
