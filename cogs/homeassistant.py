"""Astrabot cog: talk to the house through the local Home Assistant agent.

The agent itself is a separate package, ``ha_agent`` (``C:\\codingShit\\ha-agent``,
installed editable into the bot's Python with
``py -3.14 -m pip install -e C:\\codingShit\\ha-agent``). It picks tools, asks the
local Qwen model on Ollama and calls Home Assistant. This cog only carries
messages between Discord and the agent; if replies are wrong, fix the agent,
not this file. The brief this follows is ``COG_GUIDE.md`` in that repo.

**The bot must run on Golurk**, the PC running Ollama: Ollama only listens on
127.0.0.1, on purpose. Moving the bot elsewhere needs an HTTP wrapper and a
decision about network exposure first.

Who can use it: only the Discord user IDs in ``allowed_user_ids`` (default:
Thea). Whoever can talk to this cog controls the house, so it is an ID list,
never a role (anyone with Manage Roles can hand out a role). Everyone else is
ignored without a reply, so strangers cannot even tell it is listening.

What reaches the agent: messages from allowed users that are a DM, in the
configured ``channel_id``, or that @mention the bot (mention stripped). Bots,
including this one, are always ignored. ``/ha ask`` does the same for servers
where message content is off; ``/ha reset`` and ``/ha reload`` manage memory and
the action list.

Thinking (Qwen reasons first: smarter, about 3x slower, off by default) is
per conversation and lives in the agent, so it resets to off on restart.
``/think`` flips it, ``/think on`` / ``/think off`` set it, and ``/think <message>``
thinks for that one message without touching the setting. A reply that was
thought about gets a brain in its footer and its reasoning, cut short, as a
spoiler in a last message.

Risky actions (restart, unlock, open covers, add-on/backup/update actions) are
decided by the agent, which awaits a ``confirm(summary)`` function before
running them. Here that posts Allow / Deny buttons that only the person who
asked can press; Deny, a 60 s timeout or any error means no. Nothing is ever
auto-approved.

Configuration (``config.json`` ``"homeassistant"`` object):
- ``allowed_user_ids`` (default ``[Thea]``): who may use it.
- ``channel_id`` (default 0 = none): a channel where every message from an
  allowed user goes to the agent, no mention needed.
- ``ha_token``: blank = the ``HA_TOKEN`` environment variable (recommended).
  A long-lived Home Assistant token; never commit it or log it.
- ``ha_url``, ``ollama_url``, ``model``: blank = the agent's own defaults or its
  ``HA_URL`` / ``OLLAMA_URL`` / ``HA_AGENT_MODEL`` environment variables.
- ``timeout_seconds`` (default 180): safety net around one answer.
- ``fake_ha`` (default false): dev mode. Swaps in the agent repo's
  ``tests/fake_ha.py`` Home Assistant, which records actions instead of running
  them, so the Discord side (typing, buttons, splitting, errors) can be tested
  without touching real devices. No token needed. ``fake_ha_path`` overrides
  where that file is.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import re
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cogs.owner import DEFAULT_OWNER_ID

try:
    import ha_agent
    from ha_agent import HAAgent, HAError
except ImportError:  # the cog still loads and says what is missing
    ha_agent = None
    HAAgent = None

    class HAError(Exception):
        """Stand-in so error handling works without the package."""

log = logging.getLogger("homeassistant")

DISCORD_LIMIT = 2000
CONFIRM_SECONDS = 60
REASONING_LIMIT = 1800  # leaves room for the label and spoiler bars
BRAIN = "\U0001f9e0"
NO_PINGS = discord.AllowedMentions.none()


# ---------------------------------------------------------------- settings --

def load_settings(cfg, env=None) -> dict:
    """The cog's settings from config.json's ``homeassistant`` object."""
    env = os.environ if env is None else env
    raw = cfg.get("homeassistant") if isinstance(cfg, dict) else None
    raw = raw if isinstance(raw, dict) else {}

    allowed = set()
    for value in raw.get("allowed_user_ids", [DEFAULT_OWNER_ID]) or []:
        try:
            allowed.add(int(value))
        except (TypeError, ValueError):
            log.warning("Ignoring a bad allowed_user_ids entry: %r", value)
    try:
        channel_id = int(raw.get("channel_id") or 0)
    except (TypeError, ValueError):
        channel_id = 0
    try:
        timeout = max(10.0, float(raw.get("timeout_seconds", 180)))
    except (TypeError, ValueError):
        timeout = 180.0

    def text(key):
        return str(raw.get(key) or "").strip() or None

    return {
        "allowed": frozenset(allowed),
        "channel_id": channel_id,
        "ha_token": text("ha_token") or (env.get("HA_TOKEN") or "").strip() or None,
        "ha_url": text("ha_url"),
        "ollama_url": text("ollama_url"),
        "model": text("model"),
        "timeout": timeout,
        "fake_ha": bool(raw.get("fake_ha", False)),
        "fake_ha_path": text("fake_ha_path"),
    }


# ------------------------------------------------------- message routing --

def should_handle(message, bot_user, settings) -> bool:
    """Whether a Discord message goes to the agent.

    Bots never; then only allowed users; then a DM, the configured channel,
    or a message that mentions the bot.
    """
    author = message.author
    if author.bot or (bot_user is not None and author.id == bot_user.id):
        return False
    if author.id not in settings["allowed"]:
        return False
    if message.guild is None:
        return True
    if settings["channel_id"] and message.channel.id == settings["channel_id"]:
        return True
    return bot_user is not None and bot_user in message.mentions


def strip_mention(text: str, bot_id: int) -> str:
    """The message without the bot's @mention (both mention forms)."""
    return re.sub(rf"<@!?{bot_id}>", "", text or "").strip()


def conversation_id(channel, user) -> str:
    """Memory key: one per channel, one per person in DMs."""
    if getattr(channel, "guild", None) is None:
        return f"discord:dm:{user.id}"
    return f"discord:{channel.id}"


# ----------------------------------------------------------------- replies --

def split_message(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    """Split text into Discord-sized parts, at a line break or space if it can."""
    text = text.strip()
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit + 1)
        if cut <= 0:
            cut = text.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts or ["Done."]


def _options(options: dict) -> str:
    return "{" + ", ".join(f"{k}: {v}" for k, v in options.items()) + "}" if options else ""


def action_footer(reply) -> str:
    """Small-print lines listing what the agent ran, plus how long it took."""
    lines = []
    for act in reply.actions:
        mark = "\u2705" if act.ok else "\u274c"
        line = f"{mark} {act.action}"
        if act.targets:
            line += " \u2192 " + ", ".join(act.targets)
        if act.options:
            line += " " + _options(act.options)
        if not act.ok:
            line += f" ({act.result})"
        lines.append("-# " + discord.utils.escape_markdown(line[:300]))
    brain = f"{BRAIN} " if getattr(reply, "thought", False) else ""
    lines.append(f"-# {brain}{reply.seconds:.1f} s")
    return "\n".join(lines)


def reasoning_message(thinking: str, limit: int = REASONING_LIMIT) -> str | None:
    """The model's reasoning as one collapsed spoiler message, or None."""
    thinking = (thinking or "").strip()
    if not thinking:
        return None
    if len(thinking) > limit:
        thinking = thinking[:limit].rstrip() + " …"
    # "||" inside would close the spoiler early.
    thinking = thinking.replace("||", "| |")
    return f"-# {BRAIN} Reasoning (tap to show)\n||{thinking}||"


def reply_messages(reply) -> list[str]:
    """The Discord messages for one agent reply: text and footer, then reasoning."""
    parts = split_message(f"{reply.text.strip() or 'Done.'}\n{action_footer(reply)}")
    reasoning = None
    if getattr(reply, "thought", False):
        reasoning = reasoning_message(getattr(reply, "thinking", ""))
    return parts + [reasoning] if reasoning else parts


def think_choice(message: str | None):
    """What ``/think`` was asked: ("toggle", None), ("set", bool) or ("once", text)."""
    text = (message or "").strip()
    if not text:
        return "toggle", None
    if text.lower() in ("on", "off"):
        return "set", text.lower() == "on"
    return "once", text


def thinking_state(on: bool) -> str:
    return "Thinking is now ON (smarter, slower)." if on else "Thinking is now OFF (fast)."


def error_text(exc: BaseException) -> str:
    """What to tell the user for a failure. Never the exception's own text."""
    if isinstance(exc, HAError):
        return "Home Assistant isn't responding."
    if isinstance(exc, asyncio.TimeoutError):
        return "That took too long, try again."
    if isinstance(exc, aiohttp.ClientConnectorError) or (
            isinstance(exc, RuntimeError) and str(exc).startswith("Ollama")):
        return "The local model isn't running. Start Ollama on Golurk."
    return "Something went wrong talking to the house. It's in the bot log."


# ------------------------------------------------------------ confirmation --

class ConfirmView(discord.ui.View):
    """Allow / Deny buttons that only the person who asked can press."""

    def __init__(self, owner_id: int, timeout: float | None = CONFIRM_SECONDS):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.allowed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Only the person who asked can answer this.", ephemeral=True)
        return False

    def disable(self) -> None:
        for item in self.children:
            item.disabled = True

    async def _answer(self, interaction, allowed: bool, outcome: str) -> None:
        self.allowed = allowed
        self.disable()
        self.stop()
        content = (interaction.message.content if interaction.message else "") + f"\n{outcome}"
        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(label="Allow", style=discord.ButtonStyle.success)
    async def allow(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._answer(interaction, True, "\u2705 Allowed.")

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._answer(interaction, False, "\u274c Denied.")


def make_confirm(send, owner_id: int, timeout: float = CONFIRM_SECONDS):
    """A ``confirm(summary) -> bool`` for one message.

    ``send(content, view)`` posts the question and returns the Message. True
    only on Allow; Deny, the timeout and any error are False, never raised.
    """
    async def confirm(summary: str) -> bool:
        try:
            # The deadline is ours, not the View's: discord.py only starts a
            # View's own timer once a real send registers it.
            view = ConfirmView(owner_id, timeout=None)
            question = f"Allow **{discord.utils.escape_markdown(str(summary))}**?"
            prompt = await send(question, view)
            try:
                await asyncio.wait_for(view.wait(), timeout)
                timed_out = False
            except asyncio.TimeoutError:
                timed_out = True
                view.stop()
            if timed_out:
                view.disable()
                await prompt.edit(content=f"{question}\n\u231b No answer in "
                                          f"{timeout:g} s, so no.", view=view)
                return False
            return view.allowed
        except Exception:  # noqa: BLE001 -- confirm must never raise
            log.exception("Confirm prompt failed; treating it as Deny")
            return False
    return confirm


# --------------------------------------------------------------------- cog --

def _load_fake_ha(path: str | None):
    """The agent repo's FakeHA class (dev mode), or raise."""
    if path is None:
        path = Path(ha_agent.__file__).resolve().parent.parent / "tests" / "fake_ha.py"
    spec = importlib.util.spec_from_file_location("ha_agent_fake_ha", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FakeHA


class HomeAssistant(commands.Cog):
    """Carries messages between Discord and the local Home Assistant agent."""

    ha = app_commands.Group(name="ha", description="Talk to the house (Home Assistant).")

    def __init__(self, bot, settings: dict | None = None, agent=None):
        self.bot = bot
        if settings is None:
            from config import config
            settings = load_settings(config)
        self.settings = settings
        self.agent = agent
        self.problem = None  # why there is no agent, for allowed users
        self.started = False

    # -- lifecycle --

    def _build_agent(self):
        s = self.settings
        if HAAgent is None:
            self.problem = "The ha_agent package isn't installed in the bot's Python."
            return None
        token = "fake" if s["fake_ha"] else s["ha_token"]
        if not token:
            self.problem = "No Home Assistant token: set HA_TOKEN on Golurk."
            return None
        agent = HAAgent(ha_url=s["ha_url"], ha_token=token,
                        ollama_url=s["ollama_url"], model=s["model"])
        if s["fake_ha"]:
            agent.ha = _load_fake_ha(s["fake_ha_path"])()
            agent.home.ha = agent.ha
            log.warning("Dev mode: using the fake Home Assistant; nothing real is touched")
        return agent

    async def cog_load(self):
        if not self.settings["allowed"]:
            log.warning("allowed_user_ids is empty: nobody can use the house agent")
        if self.agent is None:
            try:
                self.agent = self._build_agent()
            except Exception:  # noqa: BLE001 -- never stop the bot over this
                log.exception("Could not create the Home Assistant agent")
                self.problem = "The house agent couldn't be created. It's in the bot log."
        if self.agent is None:
            log.warning("Home Assistant agent off: %s", self.problem)
            return
        await self._ensure_started()

    async def cog_unload(self):
        if self.agent is not None:
            try:
                await self.agent.close()
            except Exception:  # noqa: BLE001
                log.exception("Closing the Home Assistant agent failed")

    async def _ensure_started(self) -> bool:
        """Start the agent if it has not started yet; retried on every message."""
        if self.started:
            return True
        try:
            await self.agent.start()
        except HAError as exc:
            log.warning("Home Assistant isn't reachable: %s", exc)
            return False
        except Exception:  # noqa: BLE001
            log.exception("Starting the Home Assistant agent failed")
            return False
        self.started = True
        log.info("Home Assistant agent ready")
        return True

    # -- one answer --

    async def answer(self, cid: str, text: str, author, confirm,
                     think: bool | None = None) -> list[str]:
        """The messages to send back for one request (errors included).

        ``think`` True/False forces thinking for this message; None = the toggle.
        """
        if self.agent is None:
            return [self.problem or "The house agent is off."]
        if not await self._ensure_started():
            return ["Home Assistant isn't reachable right now. I'll try again next message."]
        try:
            reply = await asyncio.wait_for(
                self.agent.chat(cid, text, confirm=confirm, speaker=author.display_name,
                                think=think),
                self.settings["timeout"])
        except Exception as exc:  # noqa: BLE001
            log.exception("Home Assistant agent failed (%s)", cid)
            return [error_text(exc)]
        log.info("HA %s: %d tool calls, %d actions, %.1f s", cid, reply.tool_calls,
                 len(reply.actions), reply.seconds)
        return reply_messages(reply)

    def allowed(self, user) -> bool:
        return user.id in self.settings["allowed"]

    # -- messages --

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not should_handle(message, self.bot.user, self.settings):
            return
        text = strip_mention(message.content, self.bot.user.id)
        if not text:
            return

        async def send(content, view):
            return await message.reply(content, view=view, allowed_mentions=NO_PINGS)

        cid = conversation_id(message.channel, message.author)
        async with message.channel.typing():
            parts = await self.answer(cid, text, message.author,
                                      make_confirm(send, message.author.id))
        await message.reply(parts[0], allowed_mentions=NO_PINGS)
        for part in parts[1:]:
            await message.channel.send(part, allowed_mentions=NO_PINGS)

    # -- slash commands --

    async def _refuse(self, interaction) -> bool:
        if self.allowed(interaction.user):
            return False
        await interaction.response.send_message("Not allowed.", ephemeral=True)
        return True

    async def _ask(self, interaction, text: str, think: bool | None = None):
        """One request from a slash command (the caller checked the allowlist)."""
        await interaction.response.defer(thinking=True)

        async def send(content, view):
            return await interaction.followup.send(content, view=view, wait=True,
                                                   allowed_mentions=NO_PINGS)

        cid = conversation_id(interaction.channel, interaction.user)
        parts = await self.answer(cid, text, interaction.user,
                                  make_confirm(send, interaction.user.id), think=think)
        for part in parts:
            await interaction.followup.send(part, allowed_mentions=NO_PINGS)

    @ha.command(name="ask", description="Ask the house something or tell it to do something.")
    async def ask(self, interaction: discord.Interaction, text: str):
        if await self._refuse(interaction):
            return
        await self._ask(interaction, text)

    @app_commands.command(name="think", description="Toggle thinking for this conversation, "
                                                    "or think about one message.")
    @app_commands.describe(message="on / off, or a message to think about once. "
                                   "Leave empty to toggle.")
    async def think(self, interaction: discord.Interaction, message: str | None = None):
        if await self._refuse(interaction):
            return
        kind, value = think_choice(message)
        if kind == "once":
            await self._ask(interaction, value, think=True)
            return
        if self.agent is None:
            await interaction.response.send_message(self.problem or "The house agent is off.",
                                                    ephemeral=True)
            return
        on = self.agent.set_thinking(conversation_id(interaction.channel, interaction.user), value)
        await interaction.response.send_message(thinking_state(on))

    @ha.command(name="reset", description="Forget this conversation's short-term memory.")
    async def reset(self, interaction: discord.Interaction):
        if await self._refuse(interaction):
            return
        if self.agent is not None:
            self.agent.forget(conversation_id(interaction.channel, interaction.user))
        await interaction.response.send_message("Forgot this conversation.", ephemeral=True)

    @ha.command(name="reload", description="Re-read Home Assistant's actions (after adding an integration).")
    async def reload(self, interaction: discord.Interaction):
        if await self._refuse(interaction):
            return
        if self.agent is None:
            await interaction.response.send_message(self.problem or "The house agent is off.",
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            count = await self.agent.reload_catalog()
        except Exception as exc:  # noqa: BLE001
            log.exception("Reloading Home Assistant's actions failed")
            await interaction.followup.send(error_text(exc), ephemeral=True)
            return
        self.started = True
        await interaction.followup.send(f"Loaded {count} Home Assistant actions.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(HomeAssistant(bot))
