"""Text-message (SMS) notifier for audiobook requests.

The only contact detail the system collects is a cellphone number, so
"send a text" means SMS. Twilio is the assumed provider, but it is
isolated behind ``send_text`` so another provider can be swapped in
without touching the cog or the web handlers.

Credentials are read from environment variables only, never from a
committed file, and never logged. If SMS is not configured, sending
fails loudly in the log (with a masked number) and returns False --
callers must then tell the admin the text did not go out, rather than
pretending it did.
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


def sms_configured() -> bool:
    return bool(
        os.environ.get("TWILIO_ACCOUNT_SID")
        and os.environ.get("TWILIO_AUTH_TOKEN")
        and os.environ.get("TWILIO_FROM_NUMBER")
    )


def _send_twilio_sync(phone: str, message: str) -> bool:
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_FROM_NUMBER"]
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
