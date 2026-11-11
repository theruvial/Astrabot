"""Astrabot cog: portfolio website (theruvial.com).

Serves one public, read-only page over HTTP on the bot host, the same
pattern as the audiobook requests web UI (cogs.audiobooks). No Discord
commands, no auth, no database.

Where the page comes from
- Until a CV change has been approved, it serves the bundled page,
  portfolio.html (next to this file).
- The CV itself lives in the public profile repo (default
  theruvial/theruvial, cv/Thea_Arcadia_CV_full.md). The cog checks that
  file every few minutes with a plain, unauthenticated GET and an ETag, so
  nothing is fetched when nothing changed and no token is needed.
- A changed CV is first run through the publish rules in portfolio_cv.py.
  If it fails them, the live page stays as it was and the bot owner gets a
  message saying why. If it passes, the bot DMs the owner a summary of the
  changed lines with Approve and Reject buttons, and the page changes only
  after Approve. Set ``cv_auto_publish`` to true to skip the buttons.
- The approved page, the approved CV text and the watcher state are kept in
  ``data/`` (``portfolio_live.html``, ``portfolio_cv_approved.md``,
  ``portfolio_cv_pending.md``, ``portfolio_state.json``) so a restart serves
  the last approved page. They hold only what is already public.

Load it by adding ``"cogs.portfolio"`` to ``startup_extensions`` in
``bot.py`` on the HOST only. Do not push a ``bot.py`` change with it:
the host ``bot.py`` has local changes (the ``cogs.clip`` line is
commented out) and the self-update watcher never reloads ``bot.py``.

Configuration (no secrets in code or in a committed file):
- If the bot's ``config.json`` has a ``"portfolio"`` object, its keys are
  used. The cog never reads or prints the rest of config.json and never
  touches the bot token. Keys: ``owner_id`` (who gets the approval DM;
  defaults to Thea, see cogs/owner.py), ``web_host``, ``web_port``, ``cv_watch``,
  ``cv_repo``, ``cv_path``, ``cv_branch``, ``cv_poll_minutes``,
  ``cv_auto_publish``, ``data_dir``.
- Environment variables override the web keys: PORTFOLIO_WEB_HOST,
  PORTFOLIO_WEB_PORT.
- Defaults: web on 0.0.0.0:13384 (next free port after 13380 audiobook
  requests UI, 13381 absLogin, 13382 wiki backend, 13383 classifier). It
  binds 0.0.0.0 so nginx on the LAN can reach it at 192.168.4.80:13384.

Routing (nginx, see nginx-theruvial-with-portfolio.conf):
- DNS: A record for theruvial.com (@) pointing at the same public IP as
  audio.theruvial.com.
- nginx theruvial.com :80 block proxies / to 192.168.4.80:13384. Ship
  :80-only first, then run ``certbot --nginx -d theruvial.com`` and let
  certbot add the :443 block. Never write a :443 block for a cert that
  does not exist yet.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import commands

from cogs.owner import fetch_owner, owner_id
from cogs.portfolio_cv import (
    build_page,
    content_hash,
    decide_action,
    render_cv,
    summarize_change,
)

log = logging.getLogger("portfolio")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 13384
CV_DEFAULTS = {
    "cv_watch": True,
    "cv_repo": "theruvial/theruvial",
    "cv_path": "cv/Thea_Arcadia_CV_full.md",
    "cv_branch": "main",
    "cv_poll_minutes": 10,
    "cv_auto_publish": False,
}

_HERE = Path(__file__).resolve().parent
_HTML_PATH = _HERE / "portfolio.html"            # bundled fallback page
_TEMPLATE_PATH = _HERE / "portfolio_page.html"   # wraps the rendered CV
_DEFAULT_DATA_DIR = _HERE.parent / "data"
_ID_IN_MESSAGE = re.compile(r"id ([0-9a-f]{12})")


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


def _atomic_write(path: Path, text: str) -> None:
    """Write beside the target, then swap, so a crash never leaves half a page."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


class CVApprovalView(discord.ui.View):
    """Approve / Reject buttons on the DM. Persistent: the buttons keep working
    after a restart, because the pending change is stored on disk and the
    message carries the id of the version it is about."""

    def __init__(self, cog: "Portfolio"):
        super().__init__(timeout=None)
        self.cog = cog

    async def _owner_only(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.cog.owner_id:
            await interaction.response.send_message("Only the bot owner can do this.", ephemeral=True)
            return False
        return True

    async def _decide(self, interaction: discord.Interaction, approve: bool) -> None:
        if not await self._owner_only(interaction):
            return
        match = _ID_IN_MESSAGE.search(interaction.message.content or "")
        outcome = self.cog.resolve_pending(match.group(1) if match else "", approve)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=f"{interaction.message.content}\n\n**{outcome}**",
                                                view=self)

    @discord.ui.button(label="Approve and publish", style=discord.ButtonStyle.success,
                       custom_id="portfolio_cv_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._decide(interaction, True)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger,
                       custom_id="portfolio_cv_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._decide(interaction, False)


class Portfolio(commands.Cog):
    """Serves the portfolio site and keeps it in step with the CV."""

    def __init__(self, bot):
        self.bot = bot
        self.config = {**CV_DEFAULTS, **_portfolio_config(bot)}
        self.data_dir = Path(self.config.get("data_dir") or _DEFAULT_DATA_DIR)
        self._web_runner = None
        self._web_site = None
        self._watch_task = None
        self._state = self._load_state()
        self._html = self._initial_html()

    # -- files and state ------------------------------------------------
    @property
    def _live_path(self) -> Path:
        return self.data_dir / "portfolio_live.html"

    @property
    def _approved_path(self) -> Path:
        return self.data_dir / "portfolio_cv_approved.md"

    @property
    def _pending_path(self) -> Path:
        return self.data_dir / "portfolio_cv_pending.md"

    @property
    def _state_path(self) -> Path:
        return self.data_dir / "portfolio_state.json"

    def _load_state(self) -> dict:
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else {}
        except Exception:
            return {}

    def _save_state(self) -> None:
        try:
            _atomic_write(self._state_path, json.dumps(self._state, indent=2))
        except Exception as exc:  # noqa: BLE001 -- state loss must not kill the cog
            log.warning("Portfolio state could not be saved: %s", exc)

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _initial_html(self) -> str:
        live = self._read(self._live_path)
        if live.strip():
            return live
        fallback = self._read(_HTML_PATH)
        if fallback.strip():
            return fallback
        log.warning("Portfolio HTML could not be read (%s)", _HTML_PATH)
        return "<h1>Portfolio</h1><p>Page not available.</p>"

    # -- owner messages --------------------------------------------------
    @property
    def owner_id(self) -> int:
        return owner_id(self.config)

    async def _owner(self):
        return await fetch_owner(self.bot, self.owner_id)

    async def _notify(self, text: str, view: discord.ui.View | None = None) -> bool:
        """Send the owner a DM. True if it was delivered."""
        owner = await self._owner()
        if owner is None:
            return False
        try:
            if view:
                await owner.send(text[:1900], view=view)
            else:
                await owner.send(text[:1900])
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not message the bot owner: %s", exc)
            return False

    @staticmethod
    def _approval_text(version: str, approved: str, new: str) -> str:
        diff = "\n".join(summarize_change(approved, new)) or "(first publish)"
        return (f"CV change waiting for approval (id {version}). "
                f"The site changes only if you press Approve.\n```diff\n{diff}\n```")

    async def _resend_pending(self) -> None:
        """A staged change whose DM never arrived is sent again on the next check."""
        pending = self._state.get("pending")
        if not pending or self._state.get("pending_sent"):
            return
        markdown = self._read(self._pending_path)
        if not markdown.strip():
            return
        text = self._approval_text(pending, self._read(self._approved_path), markdown)
        self._state["pending_sent"] = await self._notify(text, view=CVApprovalView(self))
        self._save_state()

    # -- fetching and deciding -------------------------------------------
    async def _fetch_cv(self, etag: str | None):
        """Return (status, text, etag). Status is 200, 304 or 0 for an error."""
        import aiohttp

        url = (f"https://raw.githubusercontent.com/{self.config['cv_repo']}/"
               f"{self.config['cv_branch']}/{self.config['cv_path']}")
        headers = {"If-None-Match": etag} if etag else {}
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 304:
                        return 304, "", etag
                    if resp.status != 200:
                        log.warning("CV fetch returned HTTP %s", resp.status)
                        return 0, "", etag
                    return 200, await resp.text(encoding="utf-8"), resp.headers.get("ETag", "")
        except Exception as exc:  # noqa: BLE001
            log.warning("CV fetch failed: %s", exc)
            return 0, "", etag

    async def check_once(self, fetch=None) -> str:
        """One look at the CV. Returns what happened: unchanged, error, skip,
        blocked, staged, already-asked or published."""
        await self._resend_pending()
        status, text, etag = await (fetch or self._fetch_cv)(self._state.get("etag"))
        if status == 304:
            return "unchanged"
        if status != 200:
            return "error"
        self._state["etag"] = etag
        self._save_state()

        approved = self._read(self._approved_path)
        action, reason = decide_action(approved, text, bool(self.config["cv_auto_publish"]))
        version = content_hash(text)
        if action == "skip":
            return "skip"
        if version in self._state.get("answered", []):
            return "already-asked"   # blocked, or rejected, or asked about this version
        if action == "blocked":
            self._remember_answered(version)
            await self._notify(f"CV change not published (id {version}): {reason}. "
                               "The live page is unchanged.")
            return "blocked"
        if action == "publish":
            self.publish(text)
            await self._notify(f"CV updated on the site (id {version}).")
            return "published"
        # stage: ask for approval
        _atomic_write(self._pending_path, text)
        self._state["pending"] = version
        self._remember_answered(version)
        self._state["pending_sent"] = await self._notify(
            self._approval_text(version, approved, text), view=CVApprovalView(self))
        self._save_state()
        return "staged"

    def _remember_answered(self, version: str) -> None:
        answered = self._state.setdefault("answered", [])
        if version not in answered:
            answered.append(version)
            del answered[:-50]
        self._save_state()

    # -- publishing ------------------------------------------------------
    def publish(self, markdown: str) -> None:
        template = self._read(_TEMPLATE_PATH)
        if not template.strip():
            raise RuntimeError("page template is missing")
        updated = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        page = build_page(template, render_cv(markdown), updated)
        _atomic_write(self._live_path, page)
        _atomic_write(self._approved_path, markdown)
        self._html = page
        self._state.pop("pending", None)
        self._state.pop("pending_sent", None)
        # A published version is no longer "already answered": reverting to it
        # later must be offered again, not ignored.
        version = content_hash(markdown)
        self._state["answered"] = [v for v in self._state.get("answered", []) if v != version]
        self._save_state()
        log.info("Portfolio page published (cv id %s)", version)

    def resolve_pending(self, version: str, approve: bool) -> str:
        """Handle a button press. Returns the sentence to show on the message."""
        pending = self._state.get("pending")
        if not pending or pending != version:
            return "This preview is out of date. Nothing changed."
        markdown = self._read(self._pending_path)
        if not markdown.strip():
            return "The pending CV is missing on disk. Nothing changed."
        if not approve:
            self._state.pop("pending", None)
            self._state.pop("pending_sent", None)
            self._save_state()
            return "Rejected. The site is unchanged."
        try:
            self.publish(markdown)
        except Exception as exc:  # noqa: BLE001
            log.warning("Publish failed: %s", exc)
            return f"Publish failed ({exc}). The site is unchanged."
        return "Approved. The site is updated."

    # -- lifecycle -------------------------------------------------------
    async def _watch_loop(self) -> None:
        await self.bot.wait_until_ready()
        minutes = max(1, int(self.config.get("cv_poll_minutes", 10)))
        while True:
            try:
                log.info("CV check: %s", await self.check_once())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- the watcher must outlive any one bad check
                log.warning("CV check failed: %s", exc)
            await asyncio.sleep(minutes * 60)

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
        try:
            self.bot.add_view(CVApprovalView(self))
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not register the CV approval buttons: %s", exc)
        if self.config.get("cv_watch"):
            self._watch_task = asyncio.create_task(self._watch_loop())

    async def cog_unload(self) -> None:
        # Awaited cleanup matters for cog reloads (see audiobooks cog):
        # awaiting here frees the port before a reloaded cog rebinds it.
        if self._watch_task is not None:
            self._watch_task.cancel()
            self._watch_task = None
        if self._web_runner is not None:
            try:
                await self._web_runner.cleanup()
            except Exception:
                pass
            self._web_runner = None
            self._web_site = None


async def setup(bot):
    await bot.add_cog(Portfolio(bot))
