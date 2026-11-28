"""Which cogs the bot loads at startup, decided by config.json.

The ``"cogs"`` list in config.json names the cogs to load, e.g.
``"cogs": ["moderation", "tasks", "audiobooks"]``. Each host keeps its own
list, so turning a cog on or off is a config edit and never a code change
(no commit, and no clash with the self-updater's ``git pull``).

* ``"tasks"`` and ``"cogs.tasks"`` mean the same thing.
* No ``"cogs"`` key at all -> DEFAULT_COGS, so an old config keeps working.
* An empty list -> load nothing (deliberate).
* Entries that are not plain cog names are skipped with a warning, and
  repeats are dropped, so one typo never stops the bot starting.

Pure functions only: no discord, no config import, easy to test.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger("startup_cogs")

# Used only when config.json has no "cogs" key. The legacy 2021 cogs
# (roles, points, gambling, shop, pet) stay off unless config names them.
DEFAULT_COGS = (
    "moderation",
    "tasks",
    "clip",
    "audiobooks",
    "wiki",
    "portfolio",
    "selfupdate",
)

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def cog_extension_name(name: str) -> str:
    """Resolve a short or dotted cog name to the extension Discord loads."""
    name = name.strip().removeprefix("cogs.")
    if name.casefold() in {"homeassistant", "golurk-agent", "golurk_agent"}:
        name = "golurk_agent"
    return f"cogs.{name}"


def extensions_from_config(cfg) -> list[str]:
    """Extension names (``cogs.<name>``) to load, in the configured order."""
    raw = cfg.get("cogs") if isinstance(cfg, dict) else None
    if raw is None:
        raw = DEFAULT_COGS
    elif not isinstance(raw, (list, tuple)):
        log.warning('config "cogs" must be a list; using the defaults')
        raw = DEFAULT_COGS

    out: list[str] = []
    for entry in raw:
        ext = cog_extension_name(entry if isinstance(entry, str) else "")
        if not _NAME.fullmatch(ext.removeprefix("cogs.")):
            log.warning('Ignoring invalid "cogs" entry: %r', entry)
            continue
        if ext not in out:
            out.append(ext)
    return out
