"""Golurk-Agent, Astrabot’s Discord interface for the local agent.

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
like a message where message content is off; ``/ha reset`` forgets the
conversation. ``/ha reload`` reloads this whole extension (``reload_extension``):
``cog_unload`` drops ``ha_agent`` from ``sys.modules``, so the new cog imports the
agent's current code from its editable install, builds and starts a fresh agent and
re-reads Home Assistant's actions. The new cog reads ``config.json`` from disk
(``read_config``), not the copy ``config.py`` cached at bot start, so changed
settings (say a new ``llm_url``) apply too. Agent memory starts fresh (threads re-read
themselves on their next message); a load that fails leaves the old code running.

Thinking (Qwen reasons first: smarter, about 3x slower, off by default) is
per conversation and lives in the agent, so it resets to off on restart.
``/think`` flips it, ``/think on`` / ``/think off`` set it, and ``/think <message>``
thinks for that one message without touching the setting. A reply that was
thought about gets a brain in its footer. When live steps lack its reasoning,
a spoiler with the reasoning goes to the configured progress channel.

Steps (``show_steps``, on by default): while the agent works, the cog shows its
thought process and tool calls as they happen, one message per step in the
configured ``channel_id`` for all requests, including DMs and threads (``StepFeed``):
"💭 **Thinking**" with the reasoning (only when thinking is on; there is
none otherwise), "🔧 Calling: **names**", then a card per result with the
arguments and the first 500 characters of what came back ("\u274c ... failed" on an
error), plus the agent's own notes (mode handoff, retries). After 20 s with
nothing new it posts "\u23f3 Still working…", then at most every 120 s. Unlike
LangChat they are sent silently, with no pings or link previews, and thread
restore skips older feeds (``STEP_PREFIXES``). When live reasoning was missing
or failed to post, the spoiler uses the same configured channel. Live tool
events and thinking events need an agent whose
``chat()`` takes ``on_step`` (``docs/AGENT_STEPS_DESIGN.md``). Tool results
(notes, documents, device states) show to everyone who can read the channel.

Modes (the agent's, one model with two prompts): ``general`` (default: the
house, questions, web lookups, math, notes, timers; hands coding to ``code``
by itself) and ``code`` (answers only). ``home`` is an old alias of general.
Per conversation, so per thread, and reset to general on restart. ``/mode``
shows it, ``/mode <name>`` sets it, ``/code <message>`` uses code mode once.
The footer says which mode answered ("💻 code (auto)" when general handed it
over), whether a fast or learned route skipped the model, and how many non-house
tool calls ran (web lookups, device reads). Long
code replies are split without breaking their code blocks.

Timers: the agent's ``set_timer`` calls back ``deliver_timer`` when one ends,
which posts "⏰ Timer done ..." in the thread, channel or DM that set it (the
owner's DMs if that place is gone). The agent saves timers and re-arms them on
start, so they survive restarts.

Risky actions (restart, unlock, open covers, add-on/backup/update actions) are
decided by the agent, which awaits a ``confirm(summary)`` function before
running them. Here that posts Allow / Deny buttons that only the person who
asked can press; Deny, a 60 s timeout or any error means no. Nothing is ever
auto-approved.

Configuration uses ``config.json``’s ``"golurk_agent"`` object (``"homeassistant"`` also works):
- ``allowed_user_ids`` (default ``[Thea]``): who may use it in the channel.
- ``dm_user_ids`` (default: the bot's ``owner_id``, Thea): who may DM it. A
  separate list on purpose: a DM has no audience, so keep it short.
- ``channel_id`` (default 0 = off): the one channel the agent works in; each
  new message there opens a thread.
- ``history_turns`` (default 6): exchanges the agent remembers per
  conversation (per thread).
- ``show_steps`` (default true): post reasoning, tool calls and "still working"
  in ``channel_id``. An unset or unavailable channel suppresses progress posts.
- ``ha_token``: blank = the ``HA_TOKEN`` environment variable (recommended).
  A long-lived Home Assistant token; never commit it or log it.
- ``ha_url``, ``ollama_url``, ``model``: blank = the agent's own defaults or its
  ``HA_URL`` / ``OLLAMA_URL`` / ``HA_AGENT_MODEL`` environment variables.
- ``folder_approver_ids``: Discord IDs that can approve folder access. An
  empty list uses the agent's ``HA_AGENT_FOLDER_APPROVER_IDS`` environment setting.
- ``llm_url``: blank = the agent's default (Strata, ``HA_AGENT_LLM_URL``). A
  URL ending in ``/v1`` is spoken to as OpenAI-compatible.
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
import inspect
import io
import json
import logging
import os
import re
import sys
from collections import deque
from dataclasses import dataclass, field
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
MODE_NAMES = list(MODES) or ["general", "code"]  # "home" is now an alias of general
MODE_ICONS = {"general": "\U0001f4ac", "home": "\U0001f3e0", "code": "\U0001f4bb"}
FENCE = "```"

log = logging.getLogger("golurk_agent")

DISCORD_LIMIT = 2000
THREAD_NAME_LIMIT = 100
THREAD_ARCHIVE_MINUTES = 1440  # Discord's choices: 60, 1440, 4320, 10080
CONFIRM_SECONDS = 60
CONFIRM_EDIT_RETRY_SECONDS = 0.5
CONFIRM_NOTICE_SECONDS = 2
AGENT_STOP_WAIT_SECONDS = 10
REASONING_LIMIT = 1800  # leaves room for the label and spoiler bars
BRAIN = "\U0001f9e0"
NO_PINGS = discord.AllowedMentions.none()
ATTACHMENT_NOTICE = "Attachment notice: "
FOLDER_APPROVAL_STATUS = "Folder approval: "

STEP_PAGE_LIMIT = 1900
STEP_THINKING_LIMIT = 8000  # per model call: a runaway thought can't flood the thread
STEP_SNIPPET = 500          # of a tool result, as LangChat shows it
STEP_ARG_LIMIT = 70         # one argument's value in a result card's header
STEP_ARGS_LIMIT = 160       # the whole header's arguments
STEP_HISTORY_ROOM = 20      # extra messages per exchange to read when restoring a thread
HEARTBEAT_SECONDS = 20      # quiet this long: "Still working"
HEARTBEAT_GAP_SECONDS = 120  # and at most this often after that
THINKING_HEADER = "\U0001f4ad **Thinking**"
# How every step message starts, so thread restore never reads one as an answer.
STEP_PREFIXES = ("\U0001f527 Calling: **", "\U0001f527 **`", "\u274c **`", THINKING_HEADER[:-2],
                 "\u23f3 Still working", "\U0001f501 ")

# ---------------------------------------------------------------- settings --

def load_settings(cfg, env=None) -> dict:
    """Read the Golurk-Agent settings, accepting the legacy configuration key."""
    env = os.environ if env is None else env
    raw = cfg.get("golurk_agent", cfg.get("homeassistant")) if isinstance(cfg, dict) else None
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

    approvers = raw.get("folder_approver_ids")
    folder_approver_ids = tuple(dict.fromkeys(
        value for item in approvers
        if (value := str(item).strip()).isascii() and value.isdecimal()
    )) if isinstance(approvers, list) else ()

    return {
        "allowed": allowed,
        "channel_id": channel_id,
        "ha_token": text("ha_token") or (env.get("HA_TOKEN") or "").strip() or None,
        "ha_url": text("ha_url"),
        "ollama_url": text("ollama_url"),
        "llm_url": text("llm_url"),
        "model": text("model"),
        "timeout": timeout,
        "history_turns": history_turns,
        "dm_users": dm_users,
        "folder_approver_ids": folder_approver_ids,
        "show_steps": bool(raw.get("show_steps", True)),
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
    """Bot messages that aren't the agent's answers: prompts, errors, notices,
    and finished-timer messages (``deliver_timer``; the agent writes them)."""
    return text.startswith(STEP_PREFIXES) or (text.startswith(("Allow **", "||", "Thinking is now", "Home Assistant isn't",
                             "The local model isn't", "That took too long",
                             "Something went wrong talking", "The house agent",
                             "Golurk-Agent is off", "Golurk-Agent couldn't be created",
                             "The ha_agent package", "No Home Assistant token",
                             "⏰ Timer done", ATTACHMENT_NOTICE, FOLDER_APPROVAL_STATUS, "Request stopped.")))


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
    upload_turn = False
    for msg in messages:
        content = (getattr(msg, "content", "") or "").strip()
        if msg.author.id == bot_id:
            text = "\n".join(line for line in content.splitlines()
                             if not line.startswith("-# ")).strip()
            if upload_turn or not text or _bot_noise(text):
                continue
            if turns and turns[-1][0] == "assistant":
                turns[-1] = ("assistant", f"{turns[-1][1]}\n{text}")
            else:
                turns.append(("assistant", text))
        elif msg.author.id in allowed:
            upload_turn = bool(getattr(msg, "attachments", ()))
            text = strip_mention(content, bot_id)
            if text and not upload_turn:
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


def timer_target(cid: str) -> tuple[str, int] | None:
    """Where a finished timer goes: ``("dm", user id)``, ``("channel", id)``
    (a channel or thread) or None for an id this cog didn't make.

    The reverse of ``conversation_id``: the agent hands back the id of the
    conversation that set the timer.
    """
    kind, _, rest = (cid or "").removeprefix("discord:").rpartition(":")
    if not (cid or "").startswith("discord:") or not rest.isdigit():
        return None
    if kind == "dm":
        return "dm", int(rest)
    if kind == "":
        return "channel", int(rest)
    return None


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
    """The last footer line's pieces: mode, routing, thinking, tools, time."""
    bits = []
    mode = getattr(reply, "mode", "") or ""
    routed_by = getattr(reply, "routed_by", "")
    skipped_model = routed_by in ("fast", "learned")
    if mode:
        auto = " (auto)" if routed_by and not skipped_model else ""
        bits.append(f"{MODE_ICONS.get(mode, '')} {mode}{auto}".strip())
    if skipped_model:
        bits.append(f"⚡ {routed_by}")
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
    """Format the answer and its footer. Progress uses a separate destination."""
    return split_message(f"{reply.text.strip() or 'Done.'}\n{action_footer(reply)}")


@dataclass
class DiscordAnswer:
    """Text and generated files ready to send to the original conversation."""

    parts: list[str] = field(default_factory=list)
    files: list = field(default_factory=list)
    stopped: bool = False


def chat_parameters(agent):
    """Explicit keywords provided by this version of the agent."""
    try:
        return inspect.signature(agent.chat).parameters
    except (AttributeError, TypeError, ValueError):
        return {}


# ------------------------------------------------------------------- steps --


def _clipped(text: str, limit: int, mark: str = "…") -> str:
    return text[:limit] + mark if len(text) > limit else text


def args_summary(args) -> str:
    """A tool call's arguments on one line for inline code: ``query="x", page=2``."""
    if not isinstance(args, dict):
        return ""
    parts = []
    for key, value in args.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, str):
            shown = '"' + _clipped(" \u23ce ".join(value.splitlines()), STEP_ARG_LIMIT) + '"'
        else:
            shown = _clipped(json.dumps(value, default=str, ensure_ascii=False), STEP_ARG_LIMIT)
        parts.append(f"{key}={shown}")
    return _clipped(", ".join(parts), STEP_ARGS_LIMIT).replace("`", "'")


def _card_head(mark: str, name: str, tail: str, args) -> str:
    name = _clipped(str(name).replace("`", "'"), 80)
    summary = args_summary(args)
    return f"{mark} **`{name}`**{tail}" + (f" \u2014 `{summary}`" if summary else "")


def thinking_messages(thinking: str) -> list[str]:
    """Reasoning as one or more messages. Every part carries the header, so each
    is recognised as a step on its own; code blocks cut in two are reopened."""
    thinking = _clipped((thinking or "").strip(), STEP_THINKING_LIMIT, " …")
    if not thinking:
        return []
    more = f"{THINKING_HEADER} (continued)"
    parts = split_message(thinking, STEP_PAGE_LIMIT - len(more) - 1)
    return [f"{THINKING_HEADER if i == 0 else more}\n{part}" for i, part in enumerate(parts)]


def step_messages(event: dict, args=None) -> list[str]:
    """The Discord messages for one ``on_step`` event (docs/AGENT_STEPS_DESIGN.md),
    in LangChat's formats. ``args``: for a ``tool`` event, its call's arguments.
    Unknown types give nothing. Pure."""
    kind = event.get("type")
    if kind == "model":
        out = thinking_messages(event.get("thinking", ""))
        names = ", ".join(str(c.get("name", "tool")) for c in event.get("calls") or [])
        if names:
            out.append(f"\U0001f527 Calling: **{_clipped(discord.utils.escape_markdown(names), 500)}**")
        return out
    if kind == "tool":
        ok = event.get("ok", True)
        name = event.get("name", "tool")
        if name == "request_folder_access":
            # The embed already shows the proposal. Do not echo modal text in live steps.
            return []
        head = _card_head("\U0001f527" if ok else "\u274c", name, "" if ok else " failed", args)
        snippet = _clipped(str(event.get("result") or "").strip(), STEP_SNIPPET, "...")
        snippet = snippet.replace("```", "`\u200b``")  # can't close the block early
        return [f"{head}\n```\n{snippet or '(no output)'}\n```"]
    if kind == "note":
        text = " ".join(str(event.get("text") or "").split())
        return [f"\U0001f501 {_clipped(discord.utils.escape_markdown(text), 500)}"] if text else []
    return []


class StepFeed:
    """Posts what the agent is doing for one request, one message per step.

    ``add`` is the agent's ``on_step``: it only queues, so Discord's rate limits
    never slow the agent; one task sends the queue in order. Between ``start``
    and ``close`` a quiet stretch gets "Still working". Nothing here raises: a
    message that can't be sent is logged and dropped. ``send(content)`` posts it.
    """

    def __init__(self, send, heartbeat: float = HEARTBEAT_SECONDS,
                 gap: float = HEARTBEAT_GAP_SECONDS):
        self.send = send
        self.heartbeat = heartbeat
        self.gap = gap
        self.queue: deque = deque()
        self.pending: dict = {}      # tool call id -> (name, args), until its result
        self.failed = False          # some message didn't reach Discord
        self._thought = False
        self._cancelled = False
        self._paused = False         # waiting for a person (confirm buttons)
        self._activity = 0           # counts events; a change restarts the quiet clock
        self._sender: asyncio.Task | None = None
        self._beat: asyncio.Task | None = None

    def start(self) -> None:
        """Begin watching for quiet stretches (call when the agent starts)."""
        if self._beat is None:
            self._beat = asyncio.get_running_loop().create_task(self._heartbeats())

    def add(self, event: dict) -> None:
        if self._cancelled:
            return
        try:
            kind, args = event.get("type"), None
            self._activity += 1
            self._paused = False
            if kind == "model":
                for call in event.get("calls") or []:
                    self.pending[call.get("id")] = (call.get("name", "tool"), call.get("args"))
                self._thought = self._thought or bool((event.get("thinking") or "").strip())
            elif kind == "tool":
                args = self.pending.pop(event.get("id"), (None, None))[1]
            for message in step_messages(event, args):
                self._post(message)
        except Exception:  # noqa: BLE001 -- showing a step must never break an answer
            log.exception("Couldn't show a step: %r", event)

    def _post(self, message: str) -> None:
        self.queue.append(message)
        if self._sender is None or self._sender.done():
            self._sender = asyncio.get_running_loop().create_task(self._send_queued())

    async def _send_queued(self) -> None:
        while self.queue:
            message = self.queue.popleft()
            try:
                await self.send(message)
            except Exception:  # noqa: BLE001
                self.failed = True
                log.warning("Couldn't post one of the agent's steps", exc_info=True)

    async def flush(self) -> None:
        """Wait until everything queued has been posted."""
        while self._sender is not None and not self._sender.done():
            await self._sender

    async def pause(self) -> None:
        """Before a confirm prompt: post what there is, so the prompt comes after
        it, and say nothing more until the agent does."""
        self._paused = True
        await self.flush()

    def cancel(self) -> None:
        """Drop unsent progress and stop the sender and heartbeat."""
        self._cancelled = True
        self.queue.clear()
        self.pending.clear()
        for task in (self._sender, self._beat):
            if task is not None:
                task.cancel()

    async def close(self, reply=None) -> None:
        """Flush the last steps and post a reasoning spoiler if needed."""
        if self._cancelled:
            await asyncio.gather(*(task for task in (self._sender, self._beat) if task is not None),
                                 return_exceptions=True)
            return
        if self._beat is not None:
            self._beat.cancel()
            await asyncio.gather(self._beat, return_exceptions=True)
            self._beat = None
        for name, args in self.pending.values():
            self._post(_card_head("\u274c", name, " not run", args))
        self.pending.clear()
        await self.flush()
        if getattr(reply, "thought", False) and not self.showed_thinking():
            spoiler = reasoning_message(getattr(reply, "thinking", ""))
            if spoiler:
                self._post(spoiler)
                await self.flush()

    async def _heartbeats(self) -> None:
        loop = asyncio.get_running_loop()
        seen, quiet, posted = self._activity, 0.0, None
        while True:
            await asyncio.sleep(self.heartbeat)
            if self._paused or self._activity != seen:
                seen, quiet = self._activity, 0.0
                continue
            quiet += self.heartbeat
            if posted is None or loop.time() - posted >= self.gap:
                posted = loop.time()
                self._post(f"\u23f3 Still working… ({quiet:g}s)")

    def showed_thinking(self) -> bool:
        """The reasoning was posted, so the spoiler would only repeat it."""
        return self._thought and not self.failed and not self.queue


@dataclass
class ActiveRequest:
    cid: str
    user_id: int
    steps: StepFeed | None
    lock: asyncio.Lock
    running: bool = False
    stopped: bool = False


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


def read_config(path="config.json") -> dict:
    """``config.json`` as it is on disk now.

    ``config.py`` loads the file once when the bot starts and every
    ``from config import config`` returns that same dict, so reading it here
    would make ``/ha reload`` keep the old settings. A file that doesn't parse
    raises, which makes the reload fail and leaves the old cog running."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def reload_report(cog) -> str:
    """What ``/ha reload`` says about the freshly loaded cog. Pure."""
    if cog is None:
        return "The Golurk-Agent cog didn't come back after the reload. It's in the bot log."
    if cog.agent is None:
        return f"Reloaded the code, but Golurk-Agent is off: {cog.problem or 'see the bot log.'}"
    server = (cog.settings or {}).get("llm_url") or "the agent's default"
    if not cog.started:
        return ("Reloaded Golurk-Agent's code and settings (model server: "
                f"{server}), but Home Assistant isn't reachable yet. "
                "It will try again on the next message.")
    catalog = getattr(cog.agent, "catalog", None)
    count = len(getattr(catalog, "entries", ()) or ())
    return (f"Reloaded Golurk-Agent's code, settings and {count} Home Assistant "
            f"actions. Model server: {server}.")


# ------------------------------------------------------------ confirmation --

class ConfirmView(discord.ui.View):
    """Allow / Deny buttons that only the person who asked can press."""

    def __init__(self, owner_id: int, timeout: float | None = CONFIRM_SECONDS):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.decision = asyncio.get_running_loop().create_future()

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
        if not self.decision.done():
            self.decision.set_result((interaction, allowed, outcome))

    @discord.ui.button(label="Allow", style=discord.ButtonStyle.success)
    async def allow(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._answer(interaction, True, "\u2705 Allowed.")

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._answer(interaction, False, "\u274c Denied.")


async def _cancel_prompt(prompt, view: discord.ui.View, content: str) -> None:
    for button in view.children:
        button.disabled = True
    view.stop()
    if prompt is not None:
        try:
            await asyncio.wait_for(
                prompt.edit(content=content, view=view, allowed_mentions=NO_PINGS),
                timeout=CONFIRM_NOTICE_SECONDS,
            )
        except Exception:  # keep cancellation moving even if the UI can't be edited
            log.warning("Could not update a cancelled prompt", exc_info=True)


async def _edit_confirm_prompt(prompt, content: str, view: discord.ui.View) -> None:
    """Retry transient message-edit failures until the caller's deadline."""
    while True:
        try:
            await prompt.edit(content=content, view=view, allowed_mentions=NO_PINGS)
            return
        except (discord.HTTPException, aiohttp.ClientConnectionError, OSError) as exc:
            if isinstance(exc, discord.HTTPException) and exc.status != 429 and exc.status < 500:
                raise
            await asyncio.sleep(CONFIRM_EDIT_RETRY_SECONDS)


def make_confirm(send, owner_id: int, timeout: float = CONFIRM_SECONDS):
    """A ``confirm(summary) -> bool`` for one message.

    ``send(content, view)`` posts the question and returns the Message. True
    only after an Allow click is acknowledged and the prompt edit succeeds.
    Deny, the deadline and an unrecoverable error return False.
    """
    async def confirm(summary: str) -> bool:
        try:
            # The deadline is ours, not the View's: discord.py only starts a
            # View's own timer once a real send registers it.
            view = ConfirmView(owner_id, timeout=None)
            question = f"Allow **{discord.utils.escape_markdown(str(summary))}**?"
            prompt = None
            clicked = False
            confirmation_window = asyncio.timeout(timeout)
            try:
                prompt = await send(question, view)
                async with confirmation_window:
                    interaction, allowed, outcome = await view.decision
                    clicked = True
                    view.disable()
                    # Acknowledge the button once, then edit the sent message.
                    # Message edits can be retried after the interaction's short response window.
                    await interaction.response.defer()
                    await _edit_confirm_prompt(prompt, f"{question}\n{outcome}", view)
                    return allowed
            except asyncio.CancelledError:
                await _cancel_prompt(prompt, view, f"{question}\nRequest stopped.")
                raise
            except TimeoutError:
                if not confirmation_window.expired():
                    raise
                if clicked:
                    log.warning("Confirmation timed out while updating the prompt")
            finally:
                view.stop()

            view.disable()
            reason = "Couldn't update the confirmation" if clicked else "No answer"
            await asyncio.wait_for(
                prompt.edit(content=f"{question}\n⌛ {reason} in {timeout:g} s, so no.",
                            view=view, allowed_mentions=NO_PINGS),
                timeout=CONFIRM_NOTICE_SECONDS,
            )
            return False
        except Exception:  # noqa: BLE001 -- confirm must never raise
            log.exception("Confirm prompt failed; treating it as Deny")
            return False
    return confirm


# --------------------------------------------------------- folder approval --

class FolderApprovalView(discord.ui.View):
    """Wait for a configured approver's decision on one proposed folder grant."""

    def __init__(self, approver_ids, timeout: float):
        super().__init__(timeout=None)
        self.approver_ids = frozenset(approver_ids)
        self.modal_timeout = timeout
        self.decision = asyncio.get_running_loop().create_future()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) not in self.approver_ids:
            text = "Only a configured folder approver can answer this request."
        elif self.decision.done():
            text = "This folder request is closed."
        else:
            return True
        await interaction.response.send_message(text, ephemeral=True)
        return False

    def finish(self, interaction, decision: str, text: str = "") -> None:
        if not self.decision.done():
            self.decision.set_result((interaction, decision, text))

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.success)
    async def yes(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.finish(interaction, "yes")

    @discord.ui.button(label="No", style=discord.ButtonStyle.danger)
    async def no(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.finish(interaction, "no")

    @discord.ui.button(label="Ask", style=discord.ButtonStyle.secondary)
    async def ask(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(FolderApprovalModal(self))


class FolderApprovalModal(discord.ui.Modal, title="Question or revision"):
    def __init__(self, approval: FolderApprovalView):
        super().__init__(timeout=approval.modal_timeout)
        self.approval = approval
        self.comment = discord.ui.TextInput(style=discord.TextStyle.paragraph, max_length=1000)
        self.add_item(discord.ui.Label(text="Question or revision", component=self.comment))

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.approval.interaction_check(interaction):
            return
        text = self.comment.value.strip()
        if not text:
            await interaction.response.send_message(
                "Enter a question or revision using Ask.", ephemeral=True)
            return
        self.approval.finish(interaction, "ask", text)


def make_folder_approval(send, cid: str, user_id: str, decision_type, timeout: float = CONFIRM_SECONDS):
    """Present an agent proposal and return a FolderGrantDecision."""
    async def approve_folder(request):
        declined = decision_type("no")
        if request.conversation_id != cid or request.user_id != user_id or not request.approver_ids:
            return declined

        path = request.path
        access = {"read": "Read only", "read_write": "Read and write"}.get(request.access)
        label = discord.utils.escape_mentions(discord.utils.escape_markdown(path))
        if (not access or not path or path != path.strip() or len(label) > 1024
                or any(ord(char) < 32 for char in path)):
            return declined

        embed = discord.Embed(
            title="Folder access request",
            description="Configured approvers can decide. Ask opens a text box for a question or revision.",
        )
        embed.add_field(name="Path", value=label, inline=False)
        embed.add_field(name="Requested by", value=f"<@{user_id}>")
        embed.add_field(name="Access", value=access)
        scope = "Requester's DMs" if cid.startswith("discord:dm:") else "This conversation"
        embed.add_field(name="Scope", value=scope)
        view = FolderApprovalView(request.approver_ids, timeout)
        prompt = None
        try:
            prompt = await send(None, view, embed=embed)
            async with asyncio.timeout(timeout):
                interaction, action, text = await view.decision
                for button in view.children:
                    button.disabled = True
                await interaction.response.defer()
                status = {"yes": "Approved.", "no": "Declined.", "ask": "Question or revision sent."}
                await _edit_confirm_prompt(prompt, FOLDER_APPROVAL_STATUS + status[action], view)
                return decision_type(action, str(interaction.user.id), text)
        except asyncio.CancelledError:
            await _cancel_prompt(prompt, view, FOLDER_APPROVAL_STATUS + "Request stopped.")
            raise
        except TimeoutError:
            if prompt is not None:
                for button in view.children:
                    button.disabled = True
                try:
                    await asyncio.wait_for(
                        prompt.edit(content=FOLDER_APPROVAL_STATUS + "Expired.", view=view,
                                    allowed_mentions=NO_PINGS),
                        timeout=CONFIRM_NOTICE_SECONDS,
                    )
                except (discord.HTTPException, aiohttp.ClientError, OSError):
                    log.warning("Could not mark the folder prompt expired", exc_info=True)
        except Exception:  # approval must fail closed
            log.exception("Folder approval prompt failed")
        finally:
            view.decision.cancel()
            view.stop()
        return declined
    return approve_folder


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

    Called on unload so that ``reload_extension("cogs.golurk_agent")`` re-imports
    the agent too, and agent fixes apply without a bot restart. Safe if the new
    load fails: discord.py's rollback re-runs the old module's ``setup``, which
    still holds the old classes directly.
    """
    gone = [n for n in sys.modules
            if n == "ha_agent" or n.startswith("ha_agent.") or n == "ha_agent_fake_ha"]
    for name in gone:
        del sys.modules[name]
    return gone


class GolurkAgent(commands.Cog, name="Golurk-Agent"):
    """Carries messages between Discord and Golurk-Agent."""

    ha = app_commands.Group(name="ha", description="Talk to Golurk-Agent.")

    def __init__(self, bot, settings: dict | None = None, agent=None):
        self.bot = bot
        if settings is None:
            settings = load_settings(read_config())
        self.settings = settings
        self.agent = agent
        self.problem = None  # why there is no agent, for allowed users
        self.started = False
        self._active_requests: dict[asyncio.Task, ActiveRequest] = {}

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
        options = {"llm_url": s["llm_url"]} if s.get("llm_url") else {}  # older agents: no llm_url
        approvers = s.get("folder_approver_ids")
        if approvers and "folder_approver_ids" in inspect.signature(HAAgent).parameters:
            options["folder_approver_ids"] = approvers
        agent = HAAgent(ha_url=s["ha_url"], ha_token=token,
                        ollama_url=s["ollama_url"], model=s["model"],
                        history_turns=s.get("history_turns", 6), **options)
        # Set after creation, not as a constructor argument, so agents from before
        # timers still build. The agent looks it up each time a timer ends.
        agent.on_timer = self.deliver_timer
        if s["fake_ha"]:
            agent.ha = _load_fake_ha(s["fake_ha_path"])()
            agent.home.ha = agent.ha
            log.warning("Dev mode: using the fake Home Assistant; nothing real is touched")
        return agent

    async def cog_load(self):
        if not self.settings["allowed"]:
            log.warning("allowed_user_ids is empty: nobody can use Golurk-Agent")
        if self.agent is None:
            try:
                self.agent = self._build_agent()
            except Exception:  # noqa: BLE001 -- never stop the bot over this
                log.exception("Could not create Golurk-Agent")
                self.problem = "Golurk-Agent couldn't be created. It's in the bot log."
        if self.agent is None:
            log.warning("Golurk-Agent off: %s", self.problem)
            return
        await self._ensure_started()

    async def cog_unload(self):
        await self._stop_requests()
        if self.agent is not None:
            try:
                await self.agent.close()
            except Exception:  # noqa: BLE001
                log.exception("Closing Golurk-Agent failed")
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
            log.exception("Starting Golurk-Agent failed")
            return False
        self.started = True
        log.info("Golurk-Agent ready")
        return True

    # -- timers --

    async def deliver_timer(self, cid: str, text: str) -> None:
        """The agent's ``on_timer``: post "⏰ Timer done ..." where it was set.

        Runs as the timer's own task. Saved timers that ended while the bot was
        down fire as soon as the agent starts, which is before Discord is
        connected, hence the wait. Never raises: a failed send is logged.
        """
        target = timer_target(cid)
        if target is None:
            log.warning("Timer for an unknown conversation %r: %s", cid, text)
            return
        try:
            await self.bot.wait_until_ready()
            where = await self._timer_destination(*target)
        except Exception:  # noqa: BLE001
            log.exception("Couldn't find where timer %r goes", cid)
            where = None
        if where is None:
            # The thread, channel or user is gone: a timer should never vanish
            # silently, so it goes to the owner's DMs instead.
            log.warning("Timer for %r has nowhere to go; sending it to the owner", cid)
            try:
                where = await self._timer_destination("dm", self._owner_for_timers())
            except Exception:  # noqa: BLE001
                log.exception("Couldn't reach the owner for timer %r", cid)
                return
            if where is None:
                return
        try:
            await where.send(text, allowed_mentions=NO_PINGS)
        except Exception:  # noqa: BLE001
            log.exception("Couldn't send timer message to %r", cid)

    def _owner_for_timers(self) -> int:
        """Who gets timers that lost their place: the first DM user, else the owner."""
        dm_users = sorted(self.settings.get("dm_users") or ())
        return dm_users[0] if dm_users else DEFAULT_OWNER_ID

    async def _timer_destination(self, kind: str, ident: int):
        """The Messageable for a timer: the user (DMs) or channel/thread, or None."""
        if kind == "dm":
            user = self.bot.get_user(ident)
            return user or await self.bot.fetch_user(ident)
        return await self._find_channel(ident)

    async def _find_channel(self, ident: int):
        """Find a channel or thread, including one missing from the cache."""
        channel = self.bot.get_channel(ident)
        if channel is None:  # archived threads aren't cached
            try:
                channel = await self.bot.fetch_channel(ident)
            except (discord.NotFound, discord.Forbidden):
                return None
        return channel

    # -- one answer --

    async def _stop_requests(self, user_id: int | None = None) -> int:
        requests = {
            task: request for task, request in self._active_requests.items()
            if not task.done() and (user_id is None or (
                request.running and request.user_id == user_id and not request.stopped
            ))
        }
        if not requests:
            return 0
        for task, request in requests.items():
            request.stopped = True
            if request.steps is not None:
                request.steps.cancel()
            if not request.running:  # shutdown also removes waiters
                task.cancel()
        stop = getattr(self.agent, "stop", None)
        contexts = {(request.cid, str(request.user_id)) for request in requests.values() if request.running}
        if callable(stop) and contexts:
            try:
                outcomes = await asyncio.wait_for(
                    asyncio.gather(*(stop(cid, user_id=caller) for cid, caller in contexts),
                                   return_exceptions=True),
                    timeout=AGENT_STOP_WAIT_SECONDS,
                )
                for outcome in outcomes:
                    if isinstance(outcome, Exception):
                        log.warning("Golurk-Agent stop API failed (%s)", type(outcome).__name__)
            except TimeoutError:
                log.warning("Golurk-Agent did not finish stopping in time")
        # Older agents and requests still downloading uploads need local cancellation.
        for task in requests:
            if not task.done():
                task.cancel()
        await asyncio.gather(*requests, return_exceptions=True)
        return len(requests)

    async def stop_here(self, interaction: discord.Interaction) -> bool:
        """Handle the shared /stop in the configured agent channel, threads or DMs."""
        if not usable_here(interaction.channel, interaction.user, self.settings):
            return False
        if not any(request.running and not task.done() and not request.stopped
                   and request.user_id == interaction.user.id
                   for task, request in self._active_requests.items()):
            await interaction.response.send_message("You have no active Golurk-Agent requests.", ephemeral=True)
            return True
        await interaction.response.defer(ephemeral=True, thinking=True)
        count = await self._stop_requests(interaction.user.id)
        text = (f"Stopped {count} Golurk-Agent request{'s' if count != 1 else ''}."
                if count else "Your Golurk-Agent requests already finished.")
        await interaction.edit_original_response(content=text)
        return True

    def _step_feed(self) -> StepFeed | None:
        """Post progress exclusively in the configured server channel."""
        channel_id = self.settings["channel_id"]
        if not channel_id or not self.settings.get("show_steps", True):
            return None
        channel = None
        resolved = False

        async def post_step(content):
            nonlocal channel, resolved
            if not resolved:
                candidate = await self._find_channel(channel_id)
                if (getattr(candidate, "id", None) == channel_id
                        and getattr(candidate, "guild", None) is not None
                        and callable(getattr(candidate, "send", None))):
                    channel = candidate
                else:
                    log.warning("Golurk-Agent progress channel %s is unavailable", channel_id)
                resolved = True
            if channel is not None:
                await channel.send(content, allowed_mentions=NO_PINGS, silent=True,
                                   suppress_embeds=True)

        return StepFeed(post_step)

    async def answer(self, cid: str, text: str, author, confirm,
                     think: bool | None = None, mode: str | None = None,
                     steps: StepFeed | None = None, *, message_id: int | None = None,
                     uploads=(), send_approval=None) -> DiscordAnswer:
        """Ask the agent and collect text, upload notices and generated files."""
        if self.agent is None:
            return DiscordAnswer([self.problem or "Golurk-Agent is off."])
        if not await self._ensure_started():
            return DiscordAnswer(["Home Assistant isn't reachable right now. I'll try again next message."])

        parameters = chat_parameters(self.agent)
        extra = {"mode": mode} if mode else {}
        if {"user_id", "message_id", "is_dm"} <= parameters.keys():
            extra.update(user_id=str(author.id),
                         message_id=str(message_id) if message_id is not None else None,
                         is_dm=cid.startswith("discord:dm:"))
        folder_decision = getattr(ha_agent, "FolderGrantDecision", None)
        if "approve_folder" in parameters and send_approval is not None and folder_decision is not None:
            extra["approve_folder"] = make_folder_approval(send_approval, cid, str(author.id), folder_decision)
        read_uploads = getattr(ha_agent, "read_discord_attachments", None)
        notices = []
        if uploads and ("attachments" not in parameters or read_uploads is None):
            notices.append(ATTACHMENT_NOTICE + "Uploads need the updated Golurk-Agent package.")
            if not text:
                return DiscordAnswer(notices)

        if steps is not None:
            steps.start()
            if "on_step" in parameters:
                extra["on_step"] = steps.add
        # Keep the active host request distinct from turns queued in the same conversation.
        lock = next((turn.lock for turn in self._active_requests.values() if turn.cid == cid), None)
        request = ActiveRequest(cid, author.id, steps, lock or asyncio.Lock())

        async def run():
            async with asyncio.timeout(self.settings["timeout"]):
                async with request.lock:
                    request.running = True
                    if uploads and not notices:
                        extra["attachments"] = await read_uploads(uploads)
                    return await self.agent.chat(cid, text, confirm=confirm, speaker=author.display_name,
                                                 think=think, **extra)

        task = asyncio.create_task(run())
        self._active_requests[task] = request
        reply = None
        try:
            reply = await task
        except asyncio.CancelledError:
            if not request.stopped or asyncio.current_task().cancelling():
                if steps is not None:
                    steps.cancel()
                raise
        except Exception as exc:  # noqa: BLE001
            if not request.stopped:
                log.exception("Golurk-Agent failed (%s)", cid)
                return DiscordAnswer([error_text(exc), *notices])
        finally:
            self._active_requests.pop(task, None)
            request.stopped = request.stopped or getattr(reply, "stopped", False)
            if steps is not None:
                if request.stopped:
                    steps.cancel()
                await steps.close(reply)
        if request.stopped:
            return DiscordAnswer(stopped=True)
        if getattr(reply, "duplicate", False):
            return DiscordAnswer()

        log.info("HA %s: %d tool calls, %d actions, %.1f s", cid, reply.tool_calls,
                 len(reply.actions), reply.seconds)
        parts = reply_messages(reply)
        for error in getattr(reply, "attachment_errors", ()):
            notices.extend(split_message(ATTACHMENT_NOTICE + discord.utils.escape_markdown(error)))
        return DiscordAnswer(parts + notices, getattr(reply, "files", []))

    async def _send_answer(self, answer: DiscordAnswer, send_first, send):
        """Send text before uploading files so a failed upload cannot hide the answer."""
        if not answer.parts:  # the agent suppressed a replayed event
            return None
        first = await send_first(answer.parts[0], allowed_mentions=NO_PINGS)
        for part in answer.parts[1:]:
            await send(part, allowed_mentions=NO_PINGS)
        for file in answer.files:
            try:
                with io.BytesIO(file.data) as data:
                    upload = discord.File(data, filename=file.filename)
                    try:
                        await send(file=upload, allowed_mentions=NO_PINGS)
                    finally:
                        upload.close()
            except (discord.HTTPException, aiohttp.ClientError, OSError, ValueError):
                log.warning("Could not upload a Golurk-Agent reply file", exc_info=True)
                name = discord.utils.escape_markdown(" ".join(file.filename.split())[:200])
                try:
                    await send(f"{ATTACHMENT_NOTICE}Could not upload {name}.",
                               allowed_mentions=NO_PINGS)
                except (discord.HTTPException, aiohttp.ClientError, OSError):
                    log.warning("Could not post the upload failure notice", exc_info=True)
        return first

    def allowed(self, user) -> bool:
        return user.id in self.settings["allowed"]

    # -- messages --

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not should_handle(message, self.bot.user, self.settings):
            return
        text = strip_mention(message.content, self.bot.user.id)
        uploads = getattr(message, "attachments", ())
        if not (text or uploads):
            return

        thread = None
        if agent_place(message.channel, self.settings["channel_id"]) == "channel":
            thread = await self._open_thread(message, text)
        where = thread or message.channel

        steps = self._step_feed()

        async def send(content, view, **kwargs):
            if steps is not None:
                await steps.pause()
            if thread is not None:
                return await thread.send(content, view=view, allowed_mentions=NO_PINGS, **kwargs)
            return await message.reply(content, view=view, allowed_mentions=NO_PINGS, **kwargs)

        cid = conversation_id(where, message.author)
        if thread is None and agent_place(where, self.settings["channel_id"]) == "thread":
            await self._restore_thread(where, cid, skip_id=message.id)
        async with where.typing():
            answer = await self.answer(cid, text, message.author,
                                       make_confirm(send, message.author.id), steps=steps,
                                       message_id=message.id, uploads=uploads, send_approval=send)
        send_first = thread.send if thread is not None else message.reply
        return await self._send_answer(answer, send_first, where.send)

    async def _restore_thread(self, thread, cid: str, skip_id: int | None = None) -> int:
        """After a restart the agent has forgotten this thread: re-read it from Discord.

        Returns how many turns were handed back (0 when nothing was needed or
        Discord couldn't be read; the answer then just starts without them).
        """
        agent = self.agent
        if agent is None or not hasattr(agent, "has_history") or agent.has_history(cid):
            return 0
        seen = getattr(getattr(agent, "conversations", None), "seen", None)
        if seen is not None and skip_id is not None and seen(cid, str(skip_id)):
            return 0
        # Room for each exchange's step messages, which thread_turns then drops.
        limit = (4 + STEP_HISTORY_ROOM) * self.settings.get("history_turns", 6) + 10
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
        if seen is not None:
            # A persisted Discord ID marks an upgraded conversation even when its text
            # was excluded from durable memory or cleared with /ha reset.
            for m in messages:
                interaction_id = getattr(getattr(m, "interaction_metadata", None), "id", None)
                if seen(cid, str(interaction_id or m.id)):
                    return 0
        turns = thread_turns([m for m in messages if m.id != skip_id],
                             self.bot.user.id, self.settings["allowed"])
        if turns:
            agent.remember(cid, turns)
            log.info("Restored %d turns for %s from Discord", len(turns), cid)
        return len(turns)

    async def _open_thread(self, message, text):
        """Create or recover the thread so a replay keeps its conversation ID."""
        cached = getattr(message, "thread", None)
        if cached is not None:
            return cached
        try:
            return await message.create_thread(name=thread_name(text),
                                               auto_archive_duration=THREAD_ARCHIVE_MINUTES)
        except discord.HTTPException as exc:
            # Discord may have created the thread already. A public thread created
            # from a message has the same ID as its starter, even when archived.
            fetch_channel = getattr(self.bot, "fetch_channel", None)
            if fetch_channel is not None:
                try:
                    existing = await fetch_channel(message.id)
                except discord.HTTPException:
                    pass
                else:
                    if isinstance(existing, discord.Thread) and existing.parent_id == message.channel.id:
                        return existing
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

        steps = self._step_feed()

        async def send(content, view, **kwargs):
            if steps is not None:
                await steps.pause()
            return await interaction.followup.send(content, view=view, wait=True,
                                                   allowed_mentions=NO_PINGS, **kwargs)

        cid = conversation_id(interaction.channel, interaction.user)
        if agent_place(interaction.channel, self.settings["channel_id"]) == "thread":
            await self._restore_thread(interaction.channel, cid)
        answer = await self.answer(cid, text, interaction.user,
                                   make_confirm(send, interaction.user.id), think=think,
                                   mode=mode, steps=steps,
                                   message_id=getattr(interaction, "id", None), send_approval=send)
        if answer.stopped:
            try:
                await interaction.delete_original_response()
            except discord.NotFound:
                pass
            except discord.HTTPException:
                log.warning("Could not clear a stopped slash request", exc_info=True)
            return
        await self._send_answer(answer, interaction.followup.send, interaction.followup.send)

    @ha.command(name="ask", description="Send a request to Golurk-Agent.")
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
            await interaction.response.send_message(self.problem or "Golurk-Agent is off.",
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
                self.problem or "Golurk-Agent is off (or too old for modes).", ephemeral=True)
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

    @ha.command(name="reload",
                description="Reload Golurk-Agent's code and settings, and re-read Home Assistant's actions.")
    async def reload(self, interaction: discord.Interaction):
        """Reload this extension, which re-imports ha_agent (see cog_unload)
        and re-reads config.json (see read_config).

        Until 2026-11-25 this only re-read the action list, so agent changes
        needed a bot restart. ``self`` is the old cog once the reload is done:
        the report reads the new one from the bot."""
        if await self._refuse(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        bot, name = self.bot, self.qualified_name
        try:
            await bot.reload_extension(__name__)
        except Exception:  # noqa: BLE001 -- discord.py rolled back to the old code
            log.exception("Reloading the Golurk-Agent cog failed; the old code is still running")
            await interaction.followup.send(
                "The reload failed, so the old code is still running. It's in the bot log.",
                ephemeral=True)
            return
        log.info("Golurk-Agent cog and ha_agent reloaded by /ha reload")
        await interaction.followup.send(reload_report(bot.get_cog(name)), ephemeral=True)


async def setup(bot):
    await bot.add_cog(GolurkAgent(bot))
