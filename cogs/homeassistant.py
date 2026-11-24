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

Where: the configured ``channel_id`` and threads under it, for allowed users
(an @mention is stripped but not needed), plus DMs from ``dm_user_ids``
(default: just the owner). Everything else is ignored, and the slash commands
answer "only in #channel" ephemerally elsewhere. With ``channel_id`` unset
only those DMs work. Bots, including this one, are
always ignored.

Threads: a new message in ``channel_id`` itself opens a thread named after the
request, and the agent answers there. The thread is the conversation: its own
memory, thinking toggle and confirm buttons. The agent keeps the last
``history_turns`` exchanges (default 6) in memory; after a bot restart the
first message in a thread re-reads the thread from Discord and hands it back
with ``agent.remember`` (``thread_turns`` drops footers, confirm prompts,
reasoning and error notices). If the bot can't create threads there (needs
"Create Public Threads"), it answers in the channel instead. ``/ha ask`` works
like a message where message content is off; ``/ha reset`` and ``/ha reload``
manage memory and the action list.

Thinking (Qwen reasons first: smarter, about 3x slower, off by default) is
per conversation and lives in the agent, so it resets to off on restart.
``/think`` flips it, ``/think on`` / ``/think off`` set it, and ``/think <message>``
thinks for that one message without touching the setting. A reply that was
thought about gets a brain in its footer and its reasoning, cut short, as a
spoiler in a last message.

Modes (the agent's, one model with three prompts): ``general`` (default:
questions, web lookups, math; hands house messages to ``home`` and coding to
``code`` by itself), ``home`` (Home Assistant) and ``code`` (answers only).
Per conversation, so per thread, and reset to general on restart. ``/mode``
shows it, ``/mode <name>`` sets it, ``/code <message>`` uses code mode once.
The footer says which mode answered ("🏠 home (auto)" when general handed it
over) and how many non-house tool calls ran (web lookups, device reads). Long
code replies are split without breaking their code blocks.

Risky actions (restart, unlock, open covers, add-on/backup/update actions) are
decided by the agent, which awaits a ``confirm(summary)`` function before
running them. Here that posts Allow / Deny buttons that only the person who
asked can press; Deny, a 60 s timeout or any error means no. Nothing is ever
auto-approved.

Configuration (``config.json`` ``"homeassistant"`` object):
- ``allowed_user_ids`` (default ``[Thea]``): who may use it in the channel.
- ``dm_user_ids`` (default: the bot's ``owner_id``, Thea): who may DM it. A
  separate list on purpose: a DM has no audience, so keep it short.
- ``channel_id`` (default 0 = off): the one channel the agent works in; each
  new message there opens a thread.
- ``history_turns`` (default 6): exchanges the agent remembers per
  conversation (per thread).
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
import sys
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cogs.owner import DEFAULT_OWNER_ID, owner_id

try:
    import ha_agent
    from ha_agent import HAAgent, HAError
except ImportError:  # the cog still loads and says what is missing
    ha_agent = None
    HAAgent = None

    class HAError(Exception):
        """Stand-in so error handling works without the package."""

try:
    from ha_agent import DEFAULT_MODE, MODES
except ImportError:  # no package, or one from before modes
    DEFAULT_MODE, MODES = "general", {}

# Names for the /mode choices; the agent's own list wins when it has one.
MODE_NAMES = list(MODES) or ["general", "home", "code"]
MODE_ICONS = {"general": "\U0001f4ac", "home": "\U0001f3e0", "code": "\U0001f4bb"}
FENCE = "```"

log = logging.getLogger("homeassistant")

DISCORD_LIMIT = 2000
THREAD_NAME_LIMIT = 100
THREAD_ARCHIVE_MINUTES = 1440  # Discord's choices: 60, 1440, 4320, 10080
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

    def ids(key, default):
        out = set()
        for value in raw.get(key, default) or []:
            try:
                out.add(int(value))
            except (TypeError, ValueError):
                log.warning("Ignoring a bad %s entry: %r", key, value)
        return frozenset(out)

    allowed = ids("allowed_user_ids", [DEFAULT_OWNER_ID])
    dm_users = ids("dm_user_ids", [owner_id(cfg if isinstance(cfg, dict) else {})])
    try:
        channel_id = int(raw.get("channel_id") or 0)
    except (TypeError, ValueError):
        channel_id = 0
    try:
        timeout = max(10.0, float(raw.get("timeout_seconds", 180)))
    except (TypeError, ValueError):
        timeout = 180.0
    try:
        history_turns = max(1, int(raw.get("history_turns", 6)))
    except (TypeError, ValueError):
        history_turns = 6

    def text(key):
        return str(raw.get(key) or "").strip() or None

    return {
        "allowed": allowed,
        "channel_id": channel_id,
        "ha_token": text("ha_token") or (env.get("HA_TOKEN") or "").strip() or None,
        "ha_url": text("ha_url"),
        "ollama_url": text("ollama_url"),
        "model": text("model"),
        "timeout": timeout,
        "history_turns": history_turns,
        "dm_users": dm_users,
        "fake_ha": bool(raw.get("fake_ha", False)),
        "fake_ha_path": text("fake_ha_path"),
    }


# ------------------------------------------------------- message routing --

def should_handle(message, bot_user, settings) -> bool:
    """Whether a Discord message goes to the agent.

    Never bots. DMs from ``dm_user_ids``; otherwise only allowed users, and
    only in the configured channel or a thread under it. Anything else is ignored.
    """
    author = message.author
    if author.bot or (bot_user is not None and author.id == bot_user.id):
        return False
    return usable_here(message.channel, author, settings)


def usable_here(channel, user, settings) -> bool:
    """May this user use the agent in this channel? (Messages and slash commands.)"""
    if getattr(channel, "guild", None) is None:
        return user.id in settings["dm_users"]  # DMs: their own list
    if user.id not in settings["allowed"]:
        return False
    return agent_place(channel, settings["channel_id"]) is not None


def agent_place(channel, channel_id: int) -> str | None:
    """``"channel"`` for the agent channel itself, ``"thread"`` for a thread
    under it, else None."""
    if not channel_id:
        return None
    if channel.id == channel_id:
        return "channel"
    if getattr(channel, "parent_id", None) == channel_id:
        return "thread"
    return None


def _bot_noise(text: str) -> bool:
    """Bot messages that aren't the agent's answers: prompts, errors, notices."""
    return (text.startswith(("Allow **", "||", "Thinking is now", "Home Assistant isn't",
                             "The local model isn't", "That took too long",
                             "Something went wrong talking", "The house agent",
                             "The ha_agent package", "No Home Assistant token")))


def thread_turns(messages, bot_id: int, allowed) -> list[tuple[str, str]]:
    """A thread's messages (oldest first) as ``(role, text)`` turns for
    ``agent.remember``, written the way ``chat()`` stores them.

    Allowed users' messages become ``"Name: text"`` user turns (mention
    stripped). The bot's answers become assistant turns without their ``-#``
    footer lines, with a reply that was split over several messages joined
    back up. Confirm prompts, reasoning spoilers, error notices and everyone
    else's messages are left out.
    """
    turns: list[tuple[str, str]] = []
    for msg in messages:
        content = (getattr(msg, "content", "") or "").strip()
        if msg.author.id == bot_id:
            text = "\n".join(line for line in content.splitlines()
                             if not line.startswith("-# ")).strip()
            if not text or _bot_noise(text):
                continue
            if turns and turns[-1][0] == "assistant":
                turns[-1] = ("assistant", f"{turns[-1][1]}\n{text}")
            else:
                turns.append(("assistant", text))
        elif msg.author.id in allowed:
            text = strip_mention(content, bot_id)
            if text:
                turns.append(("user", f"{msg.author.display_name}: {text}"))
    return turns


def thread_name(text: str) -> str:
    """A thread title from the request: first line, squashed, under Discord's 100."""
    line = " ".join((text or "").split("\n", 1)[0].split())
    if len(line) > THREAD_NAME_LIMIT:
        line = line[:THREAD_NAME_LIMIT - 1].rstrip() + "…"
    return line or "House request"


def strip_mention(text: str, bot_id: int) -> str:
    """The message without the bot's @mention (both mention forms)."""
    return re.sub(rf"<@!?{bot_id}>", "", text or "").strip()


def conversation_id(channel, user) -> str:
    """Memory key: one per channel or thread, one per person in DMs."""
    if getattr(channel, "guild", None) is None:
        return f"discord:dm:{user.id}"
    return f"discord:{channel.id}"


# ----------------------------------------------------------------- replies --

def _open_fence(text: str) -> str | None:
    """The opening line (e.g. ```python) of a code block still open at the end, or None."""
    opener = None
    for line in text.split("\n"):
        if line.lstrip().startswith(FENCE):
            opener = None if opener else line.strip()
    return opener


def split_message(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    """Split text into Discord-sized parts, at a line break or space if it can.

    A cut inside a code block closes it with ``` and reopens it (same language)
    at the top of the next part, so code replies stay formatted. Indentation
    after a cut is kept.
    """
    text = text.strip()
    parts = []
    room = limit - len(FENCE) - 1  # space to close a block at the cut
    while len(text) > limit:
        cut = text.rfind("\n", 0, room + 1)
        if cut <= 0:
            cut = text.rfind(" ", 0, room + 1)
        if cut > 0 and text[:cut].strip().startswith(FENCE) and "\n" not in text[:cut].strip():
            cut = -1  # only a fence line fits before the cut: cut hard, or it never ends
        if cut > 0:
            part, rest = text[:cut], text[cut + 1:]
        else:  # no space at all: cut hard, leaving room to close a code block only if needed
            hard = room if _open_fence(text[:limit]) else limit
            part, rest = text[:hard], text[hard:]
        part = part.rstrip()
        opener = _open_fence(part)
        if opener:
            part += "\n" + FENCE
            rest = opener + "\n" + rest
        else:
            rest = rest.lstrip("\n")
        if part.strip():
            parts.append(part)
        text = rest
    if text.strip():
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
    lines.append("-# " + " · ".join(footer_bits(reply)))
    return "\n".join(lines)


def footer_bits(reply) -> list[str]:
    """The last footer line's pieces: mode, thinking, tools, time.

    The mode that answered is shown so a wrong automatic pick is easy to spot:
    "🏠 home (auto)" when general mode handed it over, "💻 code" when set.
    """
    bits = []
    mode = getattr(reply, "mode", "") or ""
    if mode:
        auto = " (auto)" if getattr(reply, "routed_by", "") else ""
        bits.append(f"{MODE_ICONS.get(mode, '')} {mode}{auto}".strip())
    if getattr(reply, "thought", False):
        bits.append(BRAIN)
    # Web lookups and device reads aren't in reply.actions, but are tool calls.
    extra = getattr(reply, "tool_calls", 0) - len(reply.actions)
    if extra > 0:
        bits.append(f"\U0001f50e {extra} tool{'s' if extra != 1 else ''}")
    bits.append(f"{reply.seconds:.1f} s")
    return bits


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


def mode_title(name: str) -> str:
    """"🏠 Home" for a mode name, from the agent's own titles when it has them."""
    mode = MODES.get(name)
    title = getattr(mode, "title", None) or name
    return f"{MODE_ICONS.get(name, '')} {title}".strip()


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
    # ha_agent raises LLMError ("Model server ..." for Strata, "Ollama ..." for Ollama); older
    # versions raised a plain RuntimeError starting with "Ollama".
    if isinstance(exc, aiohttp.ClientConnectorError) or (
            isinstance(exc, RuntimeError) and str(exc).startswith(("Model server", "Ollama"))):
        return "The local model isn't running. Start Strata on Golurk."
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


def _forget_agent_modules() -> list[str]:
    """Drop ``ha_agent`` (and the dev-mode fake HA) from ``sys.modules``.

    Called on unload so that ``reload_extension("cogs.homeassistant")`` re-imports
    the agent too, and agent fixes apply without a bot restart. Safe if the new
    load fails: discord.py's rollback re-runs the old module's ``setup``, which
    still holds the old classes directly.
    """
    gone = [n for n in sys.modules
            if n == "ha_agent" or n.startswith("ha_agent.") or n == "ha_agent_fake_ha"]
    for name in gone:
        del sys.modules[name]
    return gone


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
                        ollama_url=s["ollama_url"], model=s["model"],
                        history_turns=s.get("history_turns", 6))
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
        _forget_agent_modules()

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
                     think: bool | None = None, mode: str | None = None) -> list[str]:
        """The messages to send back for one request (errors included).

        ``think`` True/False forces thinking for this message; None = the toggle.
        ``mode`` forces a mode for this message; None = the conversation's mode.
        """
        if self.agent is None:
            return [self.problem or "The house agent is off."]
        if not await self._ensure_started():
            return ["Home Assistant isn't reachable right now. I'll try again next message."]
        extra = {"mode": mode} if mode else {}  # older agents have no mode argument
        try:
            reply = await asyncio.wait_for(
                self.agent.chat(cid, text, confirm=confirm, speaker=author.display_name,
                                think=think, **extra),
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

        thread = None
        if agent_place(message.channel, self.settings["channel_id"]) == "channel":
            thread = await self._open_thread(message, text)
        where = thread or message.channel

        async def send(content, view):
            if thread is not None:
                return await thread.send(content, view=view, allowed_mentions=NO_PINGS)
            return await message.reply(content, view=view, allowed_mentions=NO_PINGS)

        cid = conversation_id(where, message.author)
        if thread is None and agent_place(where, self.settings["channel_id"]) == "thread":
            await self._restore_thread(where, cid, skip_id=message.id)
        async with where.typing():
            parts = await self.answer(cid, text, message.author,
                                      make_confirm(send, message.author.id))
        if thread is not None:
            first = await thread.send(parts[0], allowed_mentions=NO_PINGS)
        else:
            first = await message.reply(parts[0], allowed_mentions=NO_PINGS)
        for part in parts[1:]:
            await where.send(part, allowed_mentions=NO_PINGS)
        return first

    async def _restore_thread(self, thread, cid: str, skip_id: int | None = None) -> int:
        """After a restart the agent has forgotten this thread: re-read it from Discord.

        Returns how many turns were handed back (0 when nothing was needed or
        Discord couldn't be read; the answer then just starts without them).
        """
        agent = self.agent
        if agent is None or not hasattr(agent, "has_history") or agent.has_history(cid):
            return 0
        limit = 4 * self.settings.get("history_turns", 6) + 10
        try:
            messages = [m async for m in thread.history(limit=limit)]
        except discord.HTTPException as exc:
            log.warning("Couldn't read thread %s to restore its context: %s", thread.id, exc)
            return 0
        messages.reverse()  # oldest first
        if len(messages) < limit:
            # Reached the top. A thread opened on a message has the same id as that
            # message, which lives in the parent channel: it is the first request.
            try:
                messages.insert(0, await thread.parent.fetch_message(thread.id))
            except (discord.HTTPException, AttributeError):
                pass  # not opened on a message, or it was deleted
        turns = thread_turns([m for m in messages if m.id != skip_id],
                             self.bot.user.id, self.settings["allowed"])
        if turns:
            agent.remember(cid, turns)
            log.info("Restored %d turns for %s from Discord", len(turns), cid)
        return len(turns)

    async def _open_thread(self, message, text):
        """A thread on the request message, or None (then answer in the channel)."""
        try:
            return await message.create_thread(name=thread_name(text),
                                               auto_archive_duration=THREAD_ARCHIVE_MINUTES)
        except discord.HTTPException as exc:  # Forbidden is one; also "already has a thread"
            log.warning("Couldn't open a thread for a house request (%s); answering in the channel",
                        exc)
            return None

    # -- slash commands --

    async def _refuse(self, interaction) -> bool:
        if usable_here(interaction.channel, interaction.user, self.settings):
            return False
        channel_id = self.settings["channel_id"]
        if self.allowed(interaction.user) and channel_id:
            text = f"I only answer in <#{channel_id}>."
        else:
            text = "Not allowed."
        await interaction.response.send_message(text, ephemeral=True)
        return True

    async def _ask(self, interaction, text: str, think: bool | None = None,
                   mode: str | None = None):
        """One request from a slash command (the caller checked the allowlist)."""
        await interaction.response.defer(thinking=True)

        async def send(content, view):
            return await interaction.followup.send(content, view=view, wait=True,
                                                   allowed_mentions=NO_PINGS)

        cid = conversation_id(interaction.channel, interaction.user)
        if agent_place(interaction.channel, self.settings["channel_id"]) == "thread":
            await self._restore_thread(interaction.channel, cid)
        parts = await self.answer(cid, text, interaction.user,
                                  make_confirm(send, interaction.user.id), think=think,
                                  mode=mode)
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

    @app_commands.command(name="mode", description="Show or set this conversation's mode.")
    @app_commands.describe(name="general (default, hands off automatically), home or code. "
                                "Leave empty to see the current one.")
    @app_commands.choices(name=[app_commands.Choice(name=n, value=n) for n in MODE_NAMES])
    async def mode(self, interaction: discord.Interaction, name: str | None = None):
        if await self._refuse(interaction):
            return
        if self.agent is None or not hasattr(self.agent, "set_mode"):
            await interaction.response.send_message(
                self.problem or "The house agent is off (or too old for modes).", ephemeral=True)
            return
        cid = conversation_id(interaction.channel, interaction.user)
        if name is None:
            await interaction.response.send_message(f"Mode: {mode_title(self.agent.mode(cid))}.")
            return
        try:
            new = self.agent.set_mode(cid, name)
        except ValueError:
            await interaction.response.send_message(
                f"Unknown mode. Modes: {', '.join(MODE_NAMES)}.", ephemeral=True)
            return
        await interaction.response.send_message(f"Mode is now {mode_title(new)}.")

    @app_commands.command(name="code", description="Ask the code mode one thing "
                                                   "(answers only, it can't touch files).")
    async def code(self, interaction: discord.Interaction, message: str):
        if await self._refuse(interaction):
            return
        await self._ask(interaction, message, mode="code")

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
