"""Transactional e-mail: a small ``Mailer`` seam over SMTP, a file transport for development, and none.

Only invitations use it today. Bodies may contain a setup link (a secret), so
nothing here logs message content — only recipient domain, subject and outcome.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from app.config import Settings
from app.errors import IntegrationConfigError, IntegrationError

logger = logging.getLogger(__name__)

SEND_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class Mail:
    """One outgoing message (plain text plus an optional HTML alternative)."""

    to: str
    subject: str
    text: str
    html: str | None = None


class Mailer(Protocol):
    """What the services need: send or raise ``IntegrationError``."""

    def send(self, mail: Mail) -> None:
        """Deliver ``mail``; raises ``IntegrationError`` (kind ``mail``) on failure."""
        ...


def _build(mail: Mail, sender: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = mail.to
    msg["Subject"] = mail.subject
    msg["Date"] = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg.set_content(mail.text)
    if mail.html:
        msg.add_alternative(mail.html, subtype="html")
    return msg


class SmtpMailer:
    """SMTP with STARTTLS (``SMTP_SECURE=false``) or implicit TLS (``SMTP_SECURE=true``)."""

    def __init__(self, settings: Settings) -> None:
        """Validate the SMTP settings up front so a misconfiguration surfaces at startup."""
        if not settings.smtp_host:
            raise IntegrationConfigError("SMTP not configured: missing SMTP_HOST")
        self._settings = settings

    def send(self, mail: Mail) -> None:
        """See ``Mailer.send``."""
        s = self._settings
        msg = _build(mail, s.email_from)
        try:
            if s.smtp_secure:
                client: smtplib.SMTP = smtplib.SMTP_SSL(
                    s.smtp_host or "",
                    s.smtp_port,
                    timeout=SEND_TIMEOUT_SECONDS,
                    context=ssl.create_default_context(),
                )
            else:
                client = smtplib.SMTP(s.smtp_host or "", s.smtp_port, timeout=SEND_TIMEOUT_SECONDS)
            with client:
                if not s.smtp_secure:
                    client.ehlo()
                    if client.has_extn("starttls"):
                        client.starttls(context=ssl.create_default_context())
                        client.ehlo()
                if s.smtp_user and s.smtp_pass:
                    client.login(s.smtp_user, s.smtp_pass)
                client.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            logger.error("mail delivery failed to %s: %s", _domain(mail.to), type(exc).__name__)
            raise IntegrationError("mail", "The invitation e-mail could not be sent") from exc
        logger.info("mail sent to %s: %s", _domain(mail.to), mail.subject)


class FileMailer:
    """Development transport: writes each message as an ``.eml`` file instead of sending it."""

    def __init__(self, directory: str) -> None:
        """Messages go to ``directory`` (created on first send)."""
        self._dir = Path(directory)

    def send(self, mail: Mail) -> None:
        """See ``Mailer.send``."""
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.eml"
        path.write_bytes(_build(mail, "AI4DRR Chatbot <no-reply@localhost>").as_bytes())
        logger.info("mail written to %s (to %s): %s", path.name, _domain(mail.to), mail.subject)


class NoMailer:
    """``EMAIL_TRANSPORT=none``: sending is a configuration error, reported as 503."""

    def send(self, mail: Mail) -> None:
        """Always refuses."""
        raise IntegrationConfigError("E-mail delivery is not configured (EMAIL_TRANSPORT)")


def build_mailer(settings: Settings) -> Mailer:
    """The transport named by ``EMAIL_TRANSPORT``; unknown names fail at startup."""
    if settings.email_transport == "smtp":
        return SmtpMailer(settings)
    if settings.email_transport == "file":
        return FileMailer(settings.mail_file_dir)
    if settings.email_transport == "none":
        return NoMailer()
    raise ValueError(f"EMAIL_TRANSPORT must be 'smtp', 'file' or 'none', got {settings.email_transport!r}")


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1] if "@" in address else "?"
