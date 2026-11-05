"""Astrabot cog: self-update loaded cogs from GitHub, when they change.

One watcher, not one poller per cog: it watches every extension the
bot currently has loaded and reloads only the ones whose files
actually changed upstream. That is what "each cog monitors itself"
amounts to here -- a per-cog poller would just be N copies of the
same git fetch, and a fetch already tells you about every file.

How it works, on a loop (default every 15 minutes, after the bot is
ready):

1. ``git fetch origin <current branch>`` in the bot's checkout
   (the directory containing ``bot.py``, i.e. the parent of ``cogs/``).
   The branch is whatever the host checkout is on -- it is read with
   ``git rev-parse --abbrev-ref HEAD``, never hardcoded -- so this
   works on ``audiobook-requests`` or any other branch the host uses.
2. ``git diff --name-only HEAD FETCH_HEAD`` lists what changed
   upstream. Each changed path is mapped to a loaded extension:
   the extension's own file, plus helper modules in the same package
   whose name starts with the extension's base name and ``_``
   (e.g. a change to ``cogs/audiobooks_db.py`` maps to
   ``cogs.audiobooks``). A changed path that maps to no loaded
   extension -- ``bot.py``, ``config.json``, a README, another
   branch's cog -- maps to nothing.
3. If nothing maps, it stops there: no pull, no reload. Pulling for
   an unrelated change would drag in files (including config) for
   no benefit, and could overwrite nothing but still restart
   nothing -- so it simply does not pull.
4. If something does map, it checks ``git status --porcelain``:
   a local modification to a file that *also* changed upstream
   means a pull would conflict, so it skips the whole update and
   logs it. Local modifications to other files do not block it:
   a fast-forward pull keeps those, it only fails on a file both
   sides changed.
5. ``git pull --ff-only origin <branch>``. If that fails, nothing
   is reloaded -- a half-pulled or conflicted tree must never be
   loaded into a running bot.
6. Each affected extension is reloaded with
   ``bot.reload_extension()``. discord.py's reload is atomic: if
   the new version fails to load, the old version is restored
   instead of leaving the cog unloaded.

Safety notes, read before enabling:
- This runs ``git`` on the bot host and modifies its checkout.
  It only runs if the checkout is a git repository with a ``git``
  binary available; otherwise it logs once and disables itself.
- It never reloads a cog for a change to ``bot.py`` or
  ``config.json``: those need a full bot restart (and config on
  the host is deliberately local -- see AUDIOBOOKS.md), so they
  are logged and left alone.
- Reloading a cog while it is mid-interaction is still a reload:
  discord.py rolls back on a load failure, but an interaction
  in flight when the old cog is torn down will not complete
  against the new one. The 15-minute default keeps that rare.
- A manual, admin-only ``/check_updates`` slash command runs one
  check immediately and reports the result ephemerally, so you
  do not have to wait for the loop to test this.

Configuration (bot ``config.json`` ``"selfupdate"`` object, or env):
- ``enabled`` (default True) / ``SELFUPDATE_ENABLED=0`` to disable
  the loop (the slash command still works).
- ``interval_minutes`` (default 15) / ``SELFUPDATE_INTERVAL_MINUTES``.

Enable it by adding ``"cogs.selfupdate"`` to ``startup_extensions``
in the host's ``bot.py``. It is deliberately NOT added to the
staging copy here: the host's ``bot.py`` has local changes that
a pushed copy would overwrite.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger("selfupdate")


def _selfupdate_config(bot) -> dict:
    cfg: dict = {}
    try:
        raw = getattr(bot, "config", None)
        if isinstance(raw, dict):
            cfg.update(raw.get("selfupdate") or {})
    except Exception:
        pass
    if os.environ.get("SELFUPDATE_ENABLED") is not None:
        cfg["enabled"] = os.environ["SELFUPDATE_ENABLED"] not in ("0", "false", "no")
    if os.environ.get("SELFUPDATE_INTERVAL_MINUTES"):
        try:
            cfg["interval_minutes"] = int(os.environ["SELFUPDATE_INTERVAL_MINUTES"])
        except ValueError:
            pass
    return cfg


def map_changed_to_extensions(changed_paths, extension_files) -> set[str]:
    """Map changed repo-relative paths to loaded extension names.

    ``extension_files`` maps an extension name to the repo-relative
    paths that belong to it (its own file plus helpers). A changed
    path maps to an extension only if it is exactly one of those
    files. Everything else -- bot.py, config, docs, unloaded cogs --
    maps to nothing, which is what keeps the pull scoped.
    """
    changed = {p.replace("\\", "/") for p in changed_paths}
    affected: set[str] = set()
    for name, files in extension_files.items():
        if any(f.replace("\\", "/") in changed for f in files):
            affected.add(name)
    return affected


class SelfUpdate(commands.Cog):
    def __init__(self, bot, *, config_override=None):
        self.bot = bot
        self.config = config_override if config_override is not None else _selfupdate_config(bot)
        self.enabled = bool(self.config.get("enabled", True))
        try:
            self.interval_minutes = max(1, int(self.config.get("interval_minutes", 15)))
        except (TypeError, ValueError):
            self.interval_minutes = 15
        # The checkout root is the parent of this file's package dir.
        self.repo_root = Path(__file__).resolve().parents[1]
        self._disabled_reason: str | None = None
        # The loop interval is fixed at decoration time, so rebuild
        # the loop with the configured interval in cog_load.
        self.check_loop.change_interval(minutes=self.interval_minutes)

    # -- git helpers (all blocking work runs in a thread) ------------
    async def _git(self, *args: str, timeout: int = 120) -> tuple[int, str, str]:
        def run():
            try:
                proc = subprocess.run(
                    ["git", *args], cwd=self.repo_root,
                    capture_output=True, text=True, timeout=timeout)
                return proc.returncode, proc.stdout, proc.stderr
            except FileNotFoundError:
                return 127, "", "git not found"
            except subprocess.TimeoutExpired:
                return 124, "", "git timed out"

        return await asyncio.to_thread(run)

    async def _current_branch(self) -> str | None:
        code, out, _ = await self._git("rev-parse", "--abbrev-ref", "HEAD")
        branch = out.strip()
        if code != 0 or not branch or branch == "HEAD":
            return None
        return branch

    def extension_files(self) -> dict[str, list[str]]:
        """Repo-relative files belonging to each loaded extension.

        The extension's own module file, plus helper modules in the
        same directory whose stem starts with ``<base>_`` (the
        audiobooks helpers are the example: ``audiobooks_db``,
        ``audiobooks_web``, and so on belong to ``cogs.audiobooks``).
        Files outside the checkout are ignored.
        """
        result: dict[str, list[str]] = {}
        extensions = getattr(self.bot, "extensions", {}) or {}
        for name in extensions:
            module = sys.modules.get(name)
            main = getattr(module, "__file__", None) if module else None
            files: list[str] = []
            if main:
                try:
                    main_path = Path(main).resolve()
                    files.append(main_path.relative_to(self.repo_root).as_posix())
                    base = main_path.stem
                    for sibling in main_path.parent.iterdir():
                        if (sibling.is_file() and sibling.suffix == ".py"
                                and sibling.stem.startswith(base + "_")):
                            files.append(sibling.relative_to(self.repo_root).as_posix())
                except (ValueError, OSError):
                    pass
            result[name] = files
        return result

    async def check_and_update(self) -> tuple[set[str], str]:
        """One check cycle. Returns (reloaded extensions, status).

        Status is one of: ``no_repo``, ``no_git``, ``no_branch``,
        ``fetch_failed``, ``no_cog_changes``, ``conflict_skip``,
        ``pull_failed``, ``updated``, ``nothing_to_reload``.
        Nothing is pulled or reloaded unless a loaded cog changed.
        """
        if not (self.repo_root / ".git").exists():
            return set(), "no_repo"
        branch = await self._current_branch()
        if branch is None:
            return set(), "no_branch"
        code, _, err = await self._git("fetch", "origin", branch)
        if code != 0:
            log.warning("Self-update fetch failed for %s: %s", branch, err.strip())
            return set(), "fetch_failed"
        code, out, _ = await self._git("diff", "--name-only", "HEAD", "FETCH_HEAD")
        if code != 0:
            return set(), "fetch_failed"
        changed = [line.strip() for line in out.splitlines() if line.strip()]
        affected = map_changed_to_extensions(changed, self.extension_files())
        if not affected:
            return set(), "no_cog_changes"
        # A local edit to a file that also changed upstream would
        # conflict on pull: skip the whole update, leave the tree
        # exactly as it was, and say so in the log.
        code, out, _ = await self._git("status", "--porcelain")
        dirty = set()
        if code == 0:
            for line in out.splitlines():
                parts = line.strip().split()
                if parts:
                    dirty.add(parts[-1].replace("\\", "/"))
        if dirty & {p.replace("\\", "/") for p in changed}:
            log.warning(
                "Self-update skipped: local changes conflict with upstream "
                "changes to %s", sorted(dirty & set(changed)))
            return set(), "conflict_skip"
        code, _, err = await self._git("pull", "--ff-only", "origin", branch)
        if code != 0:
            log.warning("Self-update pull failed: %s", err.strip())
            return set(), "pull_failed"
        reloaded: set[str] = set()
        for name in sorted(affected):
            try:
                await self.bot.reload_extension(name)
                reloaded.add(name)
                log.info("Self-update reloaded %s", name)
            except Exception as exc:  # noqa: BLE001 -- one cog must not block the others
                log.warning("Self-update could not reload %s: %s", name, exc)
        return reloaded, "updated" if reloaded else "nothing_to_reload"

    # -- loop ----------------------------------------------------------
    @tasks.loop(minutes=15)
    async def check_loop(self):
        reloaded, status = await self.check_and_update()
        if status not in ("no_cog_changes",):
            log.info("Self-update check: %s (reloaded: %s)",
                     status, sorted(reloaded))

    @check_loop.before_loop
    async def _before_loop(self):
        await self.bot.wait_until_ready()

    async def cog_load(self) -> None:
        if not self.enabled:
            log.info("Self-update disabled by config; loop not started.")
            return
        if not (self.repo_root / ".git").exists():
            log.warning("Self-update disabled: %s is not a git checkout.",
                        self.repo_root)
            return
        self.check_loop.start()

    def cog_unload(self) -> None:
        self.check_loop.cancel()

    # -- manual check --------------------------------------------------
    @app_commands.command(name="check_updates",
                          description="Check GitHub for cog updates and reload changed cogs (admin only)")
    async def check_updates(self, interaction: discord.Interaction) -> None:
        # Reuse the audiobooks admin rule where possible: the
        # watcher cog has no is_admin of its own, so accept config
        # admin IDs, the guild owner, or the bot owner.
        user_id = int(interaction.user.id)
        admins = []
        try:
            raw = getattr(self.bot, "config", None)
            if isinstance(raw, dict):
                admins = (raw.get("audiobooks") or {}).get("admin_user_ids") or []
        except Exception:
            pass
        allowed = user_id in [int(x) for x in admins]
        if not allowed:
            guild = getattr(interaction, "guild", None)
            if guild is not None and int(getattr(guild, "owner_id", -1)) == user_id:
                allowed = True
        if not allowed:
            is_owner = getattr(self.bot, "is_owner", None)
            if is_owner is not None:
                try:
                    allowed = await is_owner(interaction.user)
                except Exception:
                    pass
        if not allowed:
            await interaction.response.send_message(
                "You are not allowed to do that.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        reloaded, status = await self.check_and_update()
        messages = {
            "no_repo": "This bot is not running from a git checkout, so self-update is unavailable.",
            "no_branch": "Could not determine the current git branch.",
            "fetch_failed": "Could not fetch from GitHub -- check the bot host's git/remote setup.",
            "no_cog_changes": "No loaded cog has changed upstream -- nothing pulled, nothing reloaded.",
            "conflict_skip": "Skipped: a local change conflicts with an upstream change. Resolve it on the host first.",
            "pull_failed": "Git pull failed -- nothing was reloaded.",
            "nothing_to_reload": "Upstream cog changes were pulled, but no reload succeeded.",
        }
        if status == "updated":
            await interaction.followup.send(
                "Updated and reloaded: " + ", ".join(sorted(reloaded)) + ".",
                ephemeral=True)
        else:
            await interaction.followup.send(messages.get(status, status),
                                            ephemeral=True)


async def setup(bot):
    await bot.add_cog(SelfUpdate(bot))
