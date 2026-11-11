"""Who the bot sends private messages to.

``bot.application_info().owner`` is the wrong person here: the Discord
application is owned by a team, so its "owner" is a placeholder user
(name like ``team655316643148333067``) that can not receive messages, and
every send to it fails with 403 / error 50007 "Cannot send messages to this
user". The real person is set here instead. Override it with an
``owner_id`` key in config.json (top level for the moderation cog, or inside
the ``portfolio`` object for the portfolio cog).
"""

from __future__ import annotations

import logging

log = logging.getLogger("owner")

# Thea (Discord admin user ID, see DESIGN.md).
DEFAULT_OWNER_ID = 151408413744693248


def owner_id(settings) -> int:
    """The configured owner id, or the default."""
    try:
        return int((settings or {}).get("owner_id") or DEFAULT_OWNER_ID)
    except (TypeError, ValueError):
        return DEFAULT_OWNER_ID


async def fetch_owner(bot, user_id: int):
    """The owner as a User that can be sent a message, or None."""
    try:
        return await bot.fetch_user(user_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not look up the owner (%s): %s", user_id, exc)
        return None
