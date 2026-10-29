"""Text-message (SMS) notifier for audiobook requests.

The only contact detail the system collects is a cellphone number, so
"send a text" means SMS. Twilio is the assumed provider, but it is
isolated behind ``send_text`` so another provider can be swapped in
without touching the cog or the web handlers.

Credentials are read from environment variables first, and otherwise
from a ``sms`` dict passed in by the cog (from the bot config's
``audiobooks.sms`` object, if Thea added one there). They are never
logged. If SMS is not configured, sending fails loudly in the log
(with a masked number) and returns False -- callers must then tell the
admin the text did not go out, rather than pretending it did.

Note: putting the Auth Token in ``config.json`` means it lives in
that branch's git history. Thea explicitly accepted that trade-off
for the bot config on this branch; environment variables remain the
safer option for any repo that might ever become public.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import urllib.parse
import urllib.request

from .audiobooks_db import mask_phone

log = logging.getLogger("audiobooks.notify")

# Optional config-file credentials, set by the cog from
# config["audiobooks"]["sms"]. Environment variables win if both exist.
_CONFIG: dict = {}


def configure_sms(config_sms: dict | None) -> None:
    global _CONFIG
    _CONFIG = dict(config_sms or {})


def _creds() -> tuple[str | None, str | None, str | None]:
    return (
        os.environ.get("TWILIO_ACCOUNT_SID") or _CONFIG.get("account_sid"),
        os.environ.get("TWILIO_AUTH_TOKEN") or _CONFIG.get("auth_token"),
        os.environ.get("TWILIO_FROM_NUMBER") or _CONFIG.get("from_number"),
    )


def sms_configured() -> bool:
    sid, token, from_number = _creds()
    return bool(sid and token and from_number)


def _send_twilio_sync(phone: str, message: str) -> bool:
    sid, token, from_number = _creds()
    assert sid and token and from_number  # guarded by sms_configured()
    data = urllib.parse.urlencode(
        {"To": f"+1{phone}", "From": from_number, "Body": message}
    ).encode()
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data=data,
        method="POST",
    )
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:  # noqa: BLE001 -- log and report, don't crash
        log.warning("SMS send failed for %s: %s", mask_phone(phone), exc)
        return False


async def send_text(phone: str, message: str) -> bool:
    """Send an SMS. Returns True on success, False otherwise."""
    if not sms_configured():
        log.warning(
            "SMS not configured -- would have sent to %s: %s",
            mask_phone(phone),
            message,
        )
        return False
    return await asyncio.to_thread(_send_twilio_sync, phone, message)
