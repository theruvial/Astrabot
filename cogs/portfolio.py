"""Astrabot cog: portfolio website (theruvial.com).

Serves the static portfolio page (portfolio.html, next to this file)
over HTTP on the bot host, the same pattern as the audiobook requests
web UI (cogs.audiobooks) and the same aiohttp stack discord.py already
depends on. No Discord commands, no auth, no database — a public,
read-only page.

Load it by adding ``"cogs.portfolio"`` to ``startup_extensions`` in
``bot.py`` on the HOST only. Do not push a ``bot.py`` change with it:
the host ``bot.py`` has local changes (the ``cogs.clip`` line is
commented out) and the self-update watcher never reloads ``bot.py``.

Configuration (no secrets in code or in a committed file):
- If the bot's ``config.json`` has a ``"portfolio"`` object, its keys
  are used. The cog never reads or prints the rest of config.json,
  and it never touches the bot token.
- Environment variables override / fill in the same keys:
  PORTFOLIO_WEB_HOST, PORTFOLIO_WEB_PORT.
- Defaults: web on 0.0.0.0:13384. Port 13384 is the next free one after
  the existing services on the host: 13380 audiobook requests UI,
  13381 absLogin signup, 13382 wiki backend, 13383 classifier service.
  Bind 0.0.0.0 (not 127.0.0.1) so nginx on the LAN can reach it at
  192.168.4.80:13384 — the wiki backend and classifier stay loopback
  because they are Astrabot-only APIs; this page is public, so it
  needs to be reachable from nginx.

Routing (nginx, see nginx-theruvial-with-portfolio.conf):
- DNS: A record for theruvial.com (@) pointing at the same public IP
  as audio.theruvial.com. No DNS entry for the cog itself.
- nginx theruvial.com :80 block proxies / to 192.168.4.80:13384.
  Ship :80-only first, then run ``certbot --nginx -d theruvial.com``
  and let certbot add the :443 block — never write a :443 block
  referencing a cert that does not exist yet (nginx -t fails and
  certbot then fails too).

Content: portfolio.html is the whole site. Only work with commits
authored by Thea (theruvial, and the older timmay237) is shown;
private repos are described, not linked; 2026 AI-assisted work is
presented as directed work, never as sole authorship. Edit the HTML
to change the copy — the cog serves whatever is in that file, and
the self-update watcher reloads the cog when the file changes.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from discord.ext import commands

log = logging.getLogger("portfolio")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 13384

_HTML_PATH = Path(__file__).with_name("portfolio.html")


def _portfolio_config(bot) -> dict:
    cfg: dict = {}
    try:
        raw = getattr(bot, "config", None)
        if isinstance(raw, dict):
            cfg.update(raw.get("portfolio") or {})
    except Exception:
        pass
    if not cfg:
        try:
            from config import config as module_config  # type: ignore

            if isinstance(module_config, dict):
                cfg.update(module_config.get("portfolio") or {})
        except Exception:
            pass
    if os.environ.get("PORTFOLIO_WEB_HOST"):
        cfg["web_host"] = os.environ["PORTFOLIO_WEB_HOST"]
    if os.environ.get("PORTFOLIO_WEB_PORT"):
        try:
            cfg["web_port"] = int(os.environ["PORTFOLIO_WEB_PORT"])
        except ValueError:
            pass
    return cfg


class Portfolio(commands.Cog):
    """Serves the portfolio site. No commands, no state."""

    def __init__(self, bot):
        self.bot = bot
        self.config = _portfolio_config(bot)
        self._web_runner = None
        self._web_site = None
        try:
            self._html = _HTML_PATH.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 -- a missing page must not kill the cog
            log.warning("Portfolio HTML could not be read (%s): %s", _HTML_PATH, exc)
            self._html = "<h1>Portfolio</h1><p>Page not available.</p>"

    async def cog_load(self) -> None:
        try:
            from aiohttp import web as aio_web

            async def index(request: aio_web.Request) -> aio_web.Response:
                return aio_web.Response(text=self._html, content_type="text/html")

            async def health(request: aio_web.Request) -> aio_web.Response:
                return aio_web.json_response({"ok": True, "service": "portfolio"})

            app = aio_web.Application()
            app.router.add_get("/", index)
            app.router.add_get("/health", health)

            self._web_runner = aio_web.AppRunner(app)
            await self._web_runner.setup()
            host = self.config.get("web_host", DEFAULT_HOST)
            port = int(self.config.get("web_port", DEFAULT_PORT))
            self._web_site = aio_web.TCPSite(self._web_runner, host, port)
            await self._web_site.start()
            log.info("Portfolio web UI listening on %s:%s", host, port)
        except Exception as exc:  # noqa: BLE001
            log.warning("Portfolio web UI could not start: %s", exc)

    async def cog_unload(self) -> None:
        # Awaited cleanup matters for cog reloads (see audiobooks cog):
        # awaiting here frees the port before a reloaded cog rebinds it.
        if self._web_runner is not None:
            try:
                await self._web_runner.cleanup()
            except Exception:
                pass
            self._web_runner = None
            self._web_site = None


async def setup(bot):
    await bot.add_cog(Portfolio(bot))
