"""Email notifier for audiobook requests.

Previously this was SMS (Twilio); 2026-10-29 migrated everything to
email. The only contact detail the system collects is now an email
address, so "notify the requester" means send an email.

Sending is plain SMTP (stdlib ``smtplib`` only, no new dependency),
configured from environment variables first, and otherwise from an
``email`` dict passed in by the cog (from the bot config's
``audiobooks.email`` object). Supported keys:

- ``smtp_host`` (default ``smtp.gmail.com``)
- ``smtp_port`` (default 587; use 465 for implicit TLS)
- ``smtp_user`` / ``smtp_password``
- ``from_address`` (defaults to ``smtp_user``)

For Gmail, ``smtp_password`` must be an app password, not the Google
account password. Credentials are never logged. If email is not
configured, sending fails loudly in the log (with a masked address)
and returns False -- callers must then tell the admin the email did
not go out, rather than pretending it did.

Note: putting the SMTP password in ``config.json`` means it lives in
that branch's git history. Thea explicitly accepted that trade-off
for the bot config on this branch; environment variables remain the
safer option for any repo that might ever become public.
"""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from email.message import EmailMessage

from .audiobooks_db import mask_email

log = logging.getLogger("audiobooks.notify")

# Optional config-file credentials, set by the cog from
# config["audiobooks"]["email"]. Environment variables win if both exist.
_CONFIG: dict = {}


def configure_email(config_email: dict | None) -> None:
    global _CONFIG
    _CONFIG = dict(config_email or {})


def _creds() -> tuple[str, int, str | None, str | None, str | None]:
    host = os.environ.get("EMAIL_SMTP_HOST") or _CONFIG.get("smtp_host") or "smtp.gmail.com"
    port_raw = os.environ.get("EMAIL_SMTP_PORT") or _CONFIG.get("smtp_port") or 587
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 587
    user = os.environ.get("EMAIL_SMTP_USER") or _CONFIG.get("smtp_user")
    password = os.environ.get("EMAIL_SMTP_PASSWORD") or _CONFIG.get("smtp_password")
    from_address = (
        os.environ.get("EMAIL_FROM_ADDRESS") or _CONFIG.get("from_address") or user
    )
    return host, port, user, password, from_address


def email_configured() -> bool:
    host, port, user, password, from_address = _creds()
    return bool(user and password and from_address)


def _send_email_sync(to_email: str, subject: str, body: str) -> bool:
    host, port, user, password, from_address = _creds()
    assert user and password and from_address  # guarded by email_configured()
    msg = EmailMessage()
    msg["From"] = from_address
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.starttls()
        with server:
            server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001 -- log and report, don't crash
        log.warning("Email send failed for %s: %s", mask_email(to_email), exc)
        return False


async def send_email(to_email: str, subject: str, body: str) -> bool:
    """Send an email. Returns True on success, False otherwise."""
    if not email_configured():
        log.warning(
            "Email not configured -- would have sent to %s: %s",
            mask_email(to_email),
            body,
        )
        return False
    return await asyncio.to_thread(_send_email_sync, to_email, subject, body)
