"""Astrabot cog: tasks from Discord, classified by the local model.

Load it by adding ``"cogs.tasks"`` to ``startup_extensions`` in
``bot.py`` on the host only. Do not push a ``bot.py`` change with it:
the host ``bot.py`` has a local change (the ``cogs.clip`` line is
commented out) and the self-update watcher never reloads ``bot.py``.

How it works:
- The cog watches one channel, mcdungeons #astra-feedback
  (``WATCH_CHANNEL_ID``). Every message there is sent to the
  AstraThought classifier service on loopback
  (``TASKS_CLASSIFIER_URL``, default ``http://127.0.0.1:13383``),
  which splits it into pieces and classifies each piece. The bot
  never loads the model itself: the bot Python has no PyTorch, and
  the model lives in the AstraThought venv as tasks-AstraThought-v2.
- The service returns one decision per piece: ``auto_task``,
  ``auto_drop`` or ``review``, with a score and a trigger. The cog
  trusts that decision and only routes it. It does not re-decide.
- ``auto_task`` pieces become wiki tasks (POST /api/tasks on the
  wiki backend, ``WIKI_API_URL`` default ``http://127.0.0.1:13382``,
  header ``X-Astra-Wiki-Key``) and are posted as embeds in
  #todo (``TODO_CHANNEL_ID``) with Claim / Unclaim (toggle), In Progress, Done,
  Not a Task. Several people can claim the same task; the Claim
  button adds you to the assignees, and clicking it again removes
  you (unclaims you).
- ``review`` pieces are posted as amber "Needs review" embeds in
  #human-intervention (``REVIEW_CHANNEL_ID``), with a public thread
  created on the message for discussion. They do NOT become wiki
  tasks until a human rules. "Is a task" creates the wiki task,
  posts the confirmed task embed in #todo, and deletes the review
  message and its thread. If #todo cannot be reached, the review
  message is edited in place instead, so the task is never lost.
  "Not a task" deletes the review message and the thread.
- ``auto_drop`` pieces are dropped. Silence is not a label.
- Finished tasks leave #todo. Done (button or /task_done) deletes
  the task's #todo message; the wiki keeps the record. The source
  message in #astra-feedback is never touched.
- A live markdown list of the open and in-progress wiki tasks is kept
  in ``TASKS_TODO_MARKDOWN_PATH`` (default ``data/TODO.md``). It is
  rewritten whenever a task changes through this cog and every
  ``SYNC_MINUTES`` minutes, which also catches changes made on the
  wiki site. The same pass deletes the #todo messages of tasks the
  wiki now shows as done. The file is only written when the list
  changed.

Buttons and restarts:
- Views are persistent (``timeout=None``, fixed ``custom_id``) and
  are registered in ``cog_load``, like the audiobooks cog. State is
  NOT kept on the view: after a restart the button handler recovers
  it from the embed (task id in a field, source message id in the
  footer) and from a small local JSON store
  (``TASKS_STATE_PATH``, default ``data/tasks_state.json``) keyed by
  feedback message id. That store also maps review message ids to
  thread ids, so a ruling after a restart can still delete the
  thread.
- The store, the labels file and the overrides file are local files
  on the bot host. The cog only talks HTTP to the wiki backend and
  to the classifier service. It never reads or writes the wiki
  backend ``tasks.json`` directly.

Labels and retraining:
- Every button ruling (review Is/Not, confirmed-task Not a Task)
  appends one JSON line to ``TASKS_LABELS_PATH`` (default
  ``data/task_labels.jsonl``) with text, label, source message id,
  model score, trigger, reviewer id and timestamp, and updates the
  overrides file (``TASK_OVERRIDES_PATH`` in the classifier service,
  default ``data/task_overrides.json`` here) mapping normalized text
  to label, so the next identical piece is decided by the human
  ruling immediately. Labels are NEVER appended to ``train.jsonl``
  automatically. Thea merges them in a batch.
- Retraining happens only when 50 new labels have been collected,
  no matter how long that takes. No time-based retrain, no
  per-ruling micro-training. ``/task_stats`` reports the count
  since the last retrain (marker file
  ``data/task_labels_last_retrain.json``).

Permissions: #todo and #human-intervention are team-wide. Anyone
who can see a channel can rule on a review item ("Is a task" /
"Not a task"), use Claim / Unclaim, In Progress and Done, and use
the slash commands /task_add, /task_edit, /task_delete,
/task_done, /task_start, /task_list and /task_stats. The buttons
enforce that by themselves: only people with access to the channel
can see and click them. A ruling still records who made it
(reviewer_id), and every ruling lands in the labels file for the
batch retrain, never straight into the training data.

Configuration (no secrets in code or in a committed file):
- Env vars override / fill in the same keys: WIKI_API_URL,
  WIKI_API_KEY, TASKS_CLASSIFIER_URL, TASKS_LABELS_PATH,
  TASKS_OVERRIDES_PATH, TASKS_STATE_PATH. Config may also come from
  the bot config ``tasks`` object, like the audiobooks cog.
- With no WIKI_API_KEY set, slash commands reply ephemerally that
  the tasks backend is not configured, and watched messages are
  logged and skipped (no orphan embed without a wiki task).
- If the classifier service is down, watched messages are logged
  and skipped. The cog never crashes the bot and never retries in
  a loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from discord.ext import tasks as ext_tasks

log = logging.getLogger("tasks")

WATCH_CHANNEL_ID = 1568142915631325355  # #astra-feedback
TODO_CHANNEL_ID = 1568332728573759621  # #todo
REVIEW_CHANNEL_ID = 1568329972928610546  # #human-intervention

DEFAULT_WIKI_API_URL = "http://127.0.0.1:13382"
DEFAULT_CLASSIFIER_URL = "http://127.0.0.1:13383"

RETRAIN_LABEL_COUNT = 50
SEEN_CAP = 1000
SYNC_MINUTES = 5
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

STATUS_COLORS = {
    "open": discord.Color.green(),
    "in_progress": discord.Color.blue(),
    "done": discord.Color.light_grey(),
}
REVIEW_COLOR = discord.Color.orange()


class WikiApiError(Exception):
    """A non-2xx response from the wiki backend, with its error text."""

    def __init__(self, status: int, error: str):
        super().__init__(f"wiki API {status}: {error}")
        self.status = status
        self.error = error


def normalize_text(text: str) -> str:
    """Collapse whitespace and lowercase, for dedupe and overrides."""
    return re.sub(r"\s+", " ", text or "").strip().lower()


def normalize_task_id(raw: str) -> str:
    """Accept "1", "task-1" or "task-0001" and return "task-0001"."""
    value = (raw or "").strip().lower()
    match = re.fullmatch(r"(?:task-)?0*(\d+)", value)
    if not match:
        return value
    return f"task-{int(match.group(1)):04d}"


def wiki_api_config() -> tuple[str, str | None]:
    """(wiki backend base URL, shared-secret API key or None)."""
    return (
        os.environ.get("WIKI_API_URL", DEFAULT_WIKI_API_URL).rstrip("/"),
        os.environ.get("WIKI_API_KEY") or None,
    )


def classifier_config() -> str:
    """Classifier service base URL."""
    return os.environ.get(
        "TASKS_CLASSIFIER_URL", DEFAULT_CLASSIFIER_URL
    ).rstrip("/")


def _tasks_config(bot) -> dict:
    """Config for this cog, from bot config, module config, or env."""
    cfg: dict = {}
    try:
        raw = getattr(bot, "config", None)
        if isinstance(raw, dict):
            cfg.update(raw.get("tasks") or {})
    except Exception:
        pass
    if not cfg:
        try:
            from config import config as module_config  # type: ignore

            if isinstance(module_config, dict):
                cfg.update(module_config.get("tasks") or {})
        except Exception:
            pass
    return cfg


def _default_paths() -> dict:
    root = Path(__file__).resolve().parent.parent
    return {
        "labels": Path(os.environ.get("TASKS_LABELS_PATH") or root / "data" / "task_labels.jsonl"),
        "overrides": Path(os.environ.get("TASKS_OVERRIDES_PATH") or root / "data" / "task_overrides.json"),
        "state": Path(os.environ.get("TASKS_STATE_PATH") or root / "data" / "tasks_state.json"),
        "todo_markdown": Path(
            os.environ.get("TASKS_TODO_MARKDOWN_PATH") or root / "data" / "TODO.md"
        ),
        "retrain_marker": Path(
            os.environ.get("TASKS_RETRAIN_MARKER_PATH")
            or root / "data" / "task_labels_last_retrain.json"
        ),
    }


def _load_json_file(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def _write_json_file(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_text_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _md_text(value) -> str:
    """One line of plain text: newlines and runs of spaces collapsed."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


_LAST_CHANGE_RE = re.compile(r"^_Last change:.*_$", re.MULTILINE)


def build_todo_markdown(items: list, links: dict[str, str],
                        changed_at: datetime) -> str:
    """Markdown list of the tasks that are not done.

    ``links`` maps a task id to the jump URL of its #todo message.
    Tasks are grouped In progress, then Open, high priority first.
    """
    live = [t for t in items
            if isinstance(t, dict) and t.get("status") != "done"]

    def order(task: dict):
        priority = str(task.get("priority") or "").lower()
        return (PRIORITY_ORDER.get(priority, 1), str(task.get("id") or ""))

    in_progress = sorted(
        (t for t in live if t.get("status") == "in_progress"), key=order)
    opened = sorted(
        (t for t in live if t.get("status") != "in_progress"), key=order)

    lines = [
        "# To-do",
        "",
        "Live list of the open tasks from #todo. Astrabot rewrites this "
        "file whenever a task changes, so do not edit it by hand. "
        "Finished tasks are left out.",
        "",
        f"_Last change: {changed_at:%Y-%m-%d %H:%M}_",
        "",
        f"**{len(opened)} open, {len(in_progress)} in progress**",
    ]
    for heading, group in (("In progress", in_progress), ("Open", opened)):
        lines += ["", f"## {heading}", ""]
        if not group:
            lines.append("_Nothing here._")
            continue
        for task in group:
            task_id = _md_text(task.get("id"))
            details = [_md_text(task.get("priority"))]
            names = ", ".join(_task_assignees(task))
            if names:
                details.append(_md_text(names))
            line = f"- [ ] **{task_id}** {_md_text(task.get('title'))}"
            line += f" ({'; '.join(d for d in details if d)})"
            if links.get(task_id):
                line += f" - [message]({links[task_id]})"
            lines.append(line)
    return "\n".join(lines) + "\n"


def build_task_embed(
    piece: str,
    status: str = "open",
    wiki_task_id: str | None = None,
    source_message: object | None = None,
    assignees: list[str] | None = None,
    score: float | None = None,
) -> discord.Embed:
    """Embed for a confirmed task in #todo."""
    embed = discord.Embed(
        title=piece[:256],
        color=STATUS_COLORS.get(status, discord.Color.green()),
    )
    embed.add_field(name="Status", value=status, inline=True)
    if wiki_task_id:
        embed.add_field(name="Task ID", value=wiki_task_id, inline=True)
    if assignees:
        embed.add_field(name="Assignees", value=", ".join(assignees), inline=True)
    if score is not None:
        embed.add_field(name="Model score", value=f"{score:.0%}", inline=True)
    if source_message is not None:
        jump = getattr(source_message, "jump_url", None)
        author = getattr(source_message, "author", None)
        author_name = getattr(author, "display_name", None) or str(author or "unknown")
        if jump:
            embed.add_field(name="Source", value=f"[Original message]({jump}) by {author_name}", inline=False)
        elif author is not None:
            embed.add_field(name="Source", value=f"by {author_name}", inline=False)
    footer_parts = []
    if wiki_task_id:
        footer_parts.append(wiki_task_id)
    source_id = getattr(source_message, "id", None)
    if source_id:
        footer_parts.append(f"source:{source_id}")
    if footer_parts:
        embed.set_footer(text=" | ".join(footer_parts))
    return embed


def build_review_embed(
    piece: str,
    score: float | None,
    trigger: str,
    source_message: object | None = None,
) -> discord.Embed:
    """Amber "Needs review" embed. No Claim button until reviewed."""
    embed = discord.Embed(
        title="Needs review",
        description=piece[:2000],
        color=REVIEW_COLOR,
    )
    if score is not None:
        embed.add_field(name="Model score", value=f"{score:.0%}", inline=True)
    embed.add_field(name="Trigger", value=trigger or "score_band", inline=True)
    if source_message is not None:
        jump = getattr(source_message, "jump_url", None)
        author = getattr(source_message, "author", None)
        author_name = getattr(author, "display_name", None) or str(author or "unknown")
        text = getattr(source_message, "content", "") or ""
        if jump:
            embed.add_field(
                name="Source",
                value=f"[Original message]({jump}) by {author_name}",
                inline=False,
            )
        if text and text.strip() != piece.strip():
            embed.add_field(name="Full message", value=text[:1000], inline=False)
    source_id = getattr(source_message, "id", None)
    if source_id:
        embed.set_footer(text=f"source:{source_id}")
    return embed


def _embed_field(embed, name: str) -> str | None:
    for field in getattr(embed, "fields", []) or []:
        if field.name == name:
            return field.value
    return None


def _embed_source_id(embed) -> int | None:
    footer = getattr(embed, "footer", None)
    text = getattr(footer, "text", "") or ""
    match = re.search(r"source:(\d+)", text)
    return int(match.group(1)) if match else None


def _task_assignees(task: dict) -> list[str]:
    """Names assigned to a wiki task.

    Prefers the "assignees" list; falls back to the legacy single
    "assignee" string so tasks written before the list existed still
    show who claimed them.
    """
    raw = task.get("assignees")
    if isinstance(raw, list):
        names = [str(n).strip() for n in raw if str(n).strip()]
        if names:
            return names
    legacy = str(task.get("assignee", "") or "").strip()
    return [legacy] if legacy else []


class _TaskView(discord.ui.View):
    """Buttons on a confirmed task. Persistent: timeout=None."""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Claim / Unclaim", style=discord.ButtonStyle.primary,
                       custom_id="tasks:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_task_button(interaction, "claim")

    @discord.ui.button(label="In Progress", style=discord.ButtonStyle.secondary,
                       custom_id="tasks:in_progress")
    async def in_progress(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_task_button(interaction, "in_progress")

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success,
                       custom_id="tasks:done")
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_task_button(interaction, "done")

    @discord.ui.button(label="Not a Task", style=discord.ButtonStyle.danger,
                       custom_id="tasks:not_task")
    async def not_task(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_task_button(interaction, "not_task")


class _ReviewView(discord.ui.View):
    """Buttons on a review embed. Persistent: timeout=None."""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Is a task", style=discord.ButtonStyle.success,
                       custom_id="tasks:review_task")
    async def is_task(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_review_button(interaction, "is_task")

    @discord.ui.button(label="Not a task", style=discord.ButtonStyle.danger,
                       custom_id="tasks:review_not_task")
    async def not_task(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_review_button(interaction, "not_task")


class Tasks(commands.Cog):
    """Watch #astra-feedback, classify with the local model, post tasks."""

    def __init__(self, bot, *, config_override=None, labels_path=None,
                 overrides_path=None, state_path=None, retrain_marker_path=None,
                 todo_markdown_path=None):
        self.bot = bot
        self.config = config_override if config_override is not None else _tasks_config(bot)
        paths = _default_paths()
        self.labels_path = Path(labels_path) if labels_path else paths["labels"]
        self.overrides_path = Path(overrides_path) if overrides_path else paths["overrides"]
        self.state_path = Path(state_path) if state_path else paths["state"]
        self.retrain_marker_path = Path(retrain_marker_path) if retrain_marker_path else paths["retrain_marker"]
        self.todo_markdown_path = (Path(todo_markdown_path) if todo_markdown_path
                                   else paths["todo_markdown"])
        self._todo_key: str | None = None
        self._sync_lock = asyncio.Lock()
        self._http_session: aiohttp.ClientSession | None = None
        self._seen: set[str] = set()
        self.state: dict = _load_json_file(self.state_path, {})
        if not isinstance(self.state, dict):
            self.state = {}

    # -- HTTP helpers ---------------------------------------------------

    async def _session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self._http_session = aiohttp.ClientSession(timeout=timeout)
        return self._http_session

    async def cog_unload(self) -> None:
        self._sync_loop.cancel()
        if self._http_session is not None and not self._http_session.closed:
            await self._http_session.close()

    async def _wiki_api(self, method: str, path: str, **kwargs):
        """Call the wiki backend. Raises WikiApiError on non-2xx."""
        api_url, api_key = wiki_api_config()
        if not api_key:
            raise WikiApiError(0, "tasks backend is not configured (no WIKI_API_KEY)")
        session = await self._session()
        try:
            async with session.request(
                method,
                f"{api_url}{path}",
                headers={"X-Astra-Wiki-Key": api_key},
                **kwargs,
            ) as response:
                try:
                    payload = await response.json()
                except Exception:
                    payload = {}
                if response.status >= 400:
                    raise WikiApiError(
                        response.status,
                        str(payload.get("error") or f"HTTP {response.status}"),
                    )
                return payload
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise WikiApiError(0, f"wiki backend unreachable: {exc}") from exc

    async def _classify(self, text: str) -> dict | None:
        """Ask the classifier service. None if it is down or bad."""
        session = await self._session()
        try:
            async with session.post(
                f"{classifier_config()}/classify",
                json={"text": text},
            ) as response:
                if response.status != 200:
                    log.warning("Classifier service returned %s", response.status)
                    return None
                payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("Classifier service unreachable: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001 - one bad reply must not kill the cog
            log.warning("Classifier reply unreadable: %s", exc)
            return None
        if not isinstance(payload, dict) or not payload.get("ok"):
            log.warning("Classifier reply not ok: %s", payload)
            return None
        return payload

    async def _classifier_health(self) -> dict | None:
        session = await self._session()
        try:
            async with session.get(f"{classifier_config()}/health") as response:
                if response.status != 200:
                    return None
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, Exception):
            return None

    # -- finished tasks and the live markdown list ----------------------

    async def _delete_message(self, channel_id: int, message_id: int) -> bool:
        """Delete one message by id. True if it is gone afterwards."""
        channel = self._channel(channel_id)
        if channel is None:
            return False
        try:
            await channel.get_partial_message(message_id).delete()
            return True
        except discord.NotFound:
            return True
        except Exception as exc:  # noqa: BLE001 - retried by the next sync
            log.warning("Could not delete task message %s: %s", message_id, exc)
            return False

    async def _remove_task_messages(self, wiki_task_id: str) -> int:
        """Delete every bot message for a finished task. Returns the count.

        Messages that could not be deleted stay in the state file, so
        the next sync tries again.
        """
        removed = 0
        for message_id, record in list(self.state.items()):
            if record.get("kind") != "task" or record.get("wiki_task_id") != wiki_task_id:
                continue
            channel_id = record.get("channel_id") or TODO_CHANNEL_ID
            if await self._delete_message(channel_id, int(message_id)):
                self.state.pop(message_id, None)
                removed += 1
        if removed:
            self._save_state()
        return removed

    def _task_message_links(self) -> dict[str, str]:
        links: dict[str, str] = {}
        for message_id, record in self.state.items():
            task_id = record.get("wiki_task_id")
            if record.get("kind") != "task" or not task_id:
                continue
            channel_id = record.get("channel_id") or TODO_CHANNEL_ID
            guild = getattr(self._channel(channel_id), "guild", None)
            guild_id = getattr(guild, "id", None)
            if guild_id:
                links[task_id] = (
                    f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}")
        return links

    async def refresh_todo(self) -> None:
        """Rewrite the markdown list, and clear messages of done tasks.

        Never raises: a wiki outage or a locked file is logged and the
        next sync tries again.
        """
        async with self._sync_lock:
            try:
                payload = await self._wiki_api("GET", "/api/tasks")
                items = (payload.get("tasks") or {}).get("items")
                if not isinstance(items, list):
                    return
                for item in items:
                    if (isinstance(item, dict) and item.get("status") == "done"
                            and item.get("id")):
                        await self._remove_task_messages(str(item["id"]))
                text = build_todo_markdown(
                    items, self._task_message_links(), datetime.now())
                key = _LAST_CHANGE_RE.sub("", text)
                if key == self._todo_key:
                    return
                _write_text_file(self.todo_markdown_path, text)
                self._todo_key = key
            except WikiApiError as exc:
                log.warning("Could not refresh the todo list: %s", exc)
            except Exception as exc:  # noqa: BLE001 - the bot must keep running
                log.warning("Could not refresh the todo list: %s", exc)

    @ext_tasks.loop(minutes=SYNC_MINUTES)
    async def _sync_loop(self) -> None:
        await self.refresh_todo()

    @_sync_loop.before_loop
    async def _before_sync_loop(self) -> None:
        await self.bot.wait_until_ready()

    # -- state, labels, overrides ---------------------------------------

    def _save_state(self) -> None:
        try:
            _write_json_file(self.state_path, self.state)
        except OSError as exc:
            log.warning("Could not save tasks state: %s", exc)

    def _remember_seen(self, piece: str) -> None:
        if len(self._seen) >= SEEN_CAP:
            self._seen.clear()
        self._seen.add(normalize_text(piece))

    def _append_label(self, *, text: str, label: int, source_message_id: int | None,
                      score: float | None, trigger: str, reviewer_id: int) -> None:
        """Save one human ruling as a training label and an override.

        Labels go to a JSONL file, never straight into train.jsonl.
        The override makes the ruling take effect immediately for an
        identical piece, without retraining.
        """
        record = {
            "text": text,
            "label": int(label),
            "source_message_id": source_message_id,
            "model_score": score,
            "trigger": trigger,
            "reviewer_id": int(reviewer_id),
            "timestamp": _now_iso(),
        }
        try:
            self.labels_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.labels_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("Could not save task label: %s", exc)
            return
        try:
            overrides = _load_json_file(self.overrides_path, {})
            if not isinstance(overrides, dict):
                overrides = {}
            overrides[normalize_text(text)] = int(label)
            _write_json_file(self.overrides_path, overrides)
        except OSError as exc:
            log.warning("Could not save task override: %s", exc)

    def labels_since_retrain(self) -> tuple[int, int]:
        """(labels collected since the last retrain, total labels)."""
        try:
            with open(self.labels_path, encoding="utf-8") as handle:
                total = sum(1 for _ in handle)
        except OSError:
            total = 0
        marker = _load_json_file(self.retrain_marker_path, {})
        base = int(marker.get("count", 0)) if isinstance(marker, dict) else 0
        return max(0, total - base), total

    # -- wiki task helpers ----------------------------------------------

    async def _open_task_titles(self) -> set[str]:
        """Normalized titles of open wiki tasks, for dedupe."""
        try:
            payload = await self._wiki_api("GET", "/api/tasks?status=open")
        except WikiApiError as exc:
            log.warning("Could not list open tasks for dedupe: %s", exc)
            return set()
        items = (payload.get("tasks") or {}).get("items") or []
        return {normalize_text(item.get("title") or "") for item in items}

    async def _create_wiki_task(self, *, title: str, description: str,
                                created_by: int | None) -> dict:
        payload = await self._wiki_api("POST", "/api/tasks", json={
            "title": title[:200],
            "description": description[:2000],
            "source": "astrabot",
            "created_by": str(created_by) if created_by else "",
        })
        return payload.get("task") or {}

    def _channel(self, channel_id: int):
        if self.bot is None:
            return None
        return self.bot.get_channel(channel_id)

    async def _post_task_embed(self, *, piece: str, task: dict,
                               source_message, score: float | None,
                               trigger: str = "none"):
        channel = self._channel(TODO_CHANNEL_ID)
        if channel is None:
            log.warning("Todo channel %s not found", TODO_CHANNEL_ID)
            return None
        embed = build_task_embed(
            piece,
            status=task.get("status") or "open",
            wiki_task_id=task.get("id"),
            source_message=source_message,
            assignees=_task_assignees(task),
            score=score,
        )
        message = await channel.send(embed=embed, view=_TaskView(self))
        self.state[str(message.id)] = {
            "kind": "task",
            "wiki_task_id": task.get("id"),
            "source_message_id": getattr(source_message, "id", None),
            "piece": piece,
            "score": score,
            "trigger": trigger,
            "thread_id": None,
            "status": task.get("status") or "open",
            "channel_id": channel.id,
        }
        self._save_state()
        await self.refresh_todo()
        return message

    async def _post_review_embed(self, *, piece: str, score: float | None,
                                 trigger: str, source_message):
        channel = self._channel(REVIEW_CHANNEL_ID)
        if channel is None:
            log.warning("Review channel %s not found", REVIEW_CHANNEL_ID)
            return None
        embed = build_review_embed(piece, score, trigger, source_message)
        message = await channel.send(embed=embed, view=_ReviewView(self))
        thread = None
        try:
            thread = await message.create_thread(
                name=f"Review: {piece[:80]}",
                auto_archive_duration=1440,
                type=discord.ChannelType.public_thread,
            )
        except Exception as exc:  # noqa: BLE001 - a missing thread must not lose the review
            log.warning("Could not create review thread: %s", exc)
        self.state[str(message.id)] = {
            "kind": "review",
            "wiki_task_id": None,
            "source_message_id": getattr(source_message, "id", None),
            "source_url": getattr(source_message, "jump_url", None),
            "source_author": getattr(getattr(source_message, "author", None), "display_name", None),
            "piece": piece,
            "score": score,
            "trigger": trigger,
            "thread_id": getattr(thread, "id", None),
            "status": "review",
        }
        self._save_state()
        return message

    async def _delete_thread(self, thread_id: int | None) -> None:
        if not thread_id or self.bot is None:
            return
        thread = None
        try:
            get_thread = getattr(self.bot, "get_channel", None)
            thread = get_thread(thread_id) if get_thread else None
            if thread is None:
                thread = await self.bot.fetch_channel(thread_id)
            await thread.delete()
        except Exception as exc:  # noqa: BLE001 - ruling stands even if thread delete fails
            log.warning("Could not delete review thread %s: %s", thread_id, exc)

    # -- watcher ----------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.channel.id != WATCH_CHANNEL_ID:
            return
        if getattr(message.author, "bot", False):
            return
        text = (message.content or "").strip()
        if not text:
            return
        result = await self._classify(text)
        if result is None:
            return
        pieces = result.get("pieces") or []
        if not pieces:
            return
        _, api_key = wiki_api_config()
        if not api_key:
            log.warning("Wiki backend not configured, skipping watched message %s", message.id)
            return
        open_titles = await self._open_task_titles()
        for piece in pieces:
            piece_text = (piece or {}).get("text") or ""
            if not piece_text:
                continue
            normalized = normalize_text(piece_text)
            if normalized in self._seen or normalized in open_titles:
                continue
            decision = (piece or {}).get("decision")
            score = (piece or {}).get("score")
            trigger = (piece or {}).get("trigger") or "none"
            if decision == "auto_task":
                try:
                    task = await self._create_wiki_task(
                        title=piece_text,
                        description=f"From {message.author.display_name} in #{message.channel.name}: {message.jump_url}",
                        created_by=message.author.id,
                    )
                except WikiApiError as exc:
                    log.warning("Could not create wiki task: %s", exc)
                    continue
                self._remember_seen(piece_text)
                await self._post_task_embed(
                    piece=piece_text, task=task, source_message=message, score=score,
                )
            elif decision == "review":
                self._remember_seen(piece_text)
                await self._post_review_embed(
                    piece=piece_text, score=score, trigger=trigger, source_message=message,
                )
            # auto_drop: nothing is posted. Silence is not a label.

    # -- button handlers ---------------------------------------------------

    async def handle_task_button(self, interaction: discord.Interaction, action: str):
        message = interaction.message
        embed = (message.embeds or [None])[0]
        record = self.state.get(str(message.id), {})
        wiki_task_id = record.get("wiki_task_id") or _embed_field(embed, "Task ID")
        piece = record.get("piece") or (embed.title if embed else "") or ""
        score = record.get("score")
        trigger = record.get("trigger") or "none"
        source_id = record.get("source_message_id") or _embed_source_id(embed)
        await interaction.response.defer()
        if action == "not_task":
            if wiki_task_id:
                try:
                    await self._wiki_api("DELETE", f"/api/tasks/{wiki_task_id}")
                except WikiApiError as exc:
                    log.warning("Could not delete wiki task %s: %s", wiki_task_id, exc)
            self._append_label(
                text=piece, label=0, source_message_id=source_id,
                score=score, trigger=trigger, reviewer_id=interaction.user.id,
            )
            self.state.pop(str(message.id), None)
            self._save_state()
            done_embed = build_task_embed(piece, status="done", wiki_task_id=wiki_task_id)
            done_embed.title = f"Not a task: {piece[:200]}"
            done_embed.color = discord.Color.light_grey()
            view = _TaskView(self)
            for item in view.children:
                item.disabled = True
            await message.edit(embed=done_embed, view=view)
            await interaction.followup.send("Marked as not a task.", ephemeral=True)
            await self.refresh_todo()
            return
        if action == "claim":
            if not wiki_task_id:
                await interaction.followup.send("No wiki task linked to this message.", ephemeral=True)
                return
            try:
                current = await self._wiki_api("GET", f"/api/tasks/{wiki_task_id}")
                current_task = current.get("task") or {}
            except WikiApiError as exc:
                await interaction.followup.send(f"Wiki backend error: {exc.error}", ephemeral=True)
                return
            # Claim is a toggle, and several people can claim one task.
            # Discord shows one button label to everyone, so the button
            # says "Claim / Unclaim" and does whichever applies to the
            # person who clicked it.
            name = interaction.user.display_name
            names = _task_assignees(current_task)
            if name in names:
                names = [n for n in names if n != name]
                verb = "Unclaimed."
            else:
                names = names + [name]
                verb = "Claimed."
            try:
                task = await self._wiki_api(
                    "PATCH", f"/api/tasks/{wiki_task_id}",
                    json={"assignees": names},
                )
                task = task.get("task") or {}
            except WikiApiError as exc:
                await interaction.followup.send(f"Wiki backend error: {exc.error}", ephemeral=True)
                return
            record["status"] = task.get("status") or record.get("status") or "open"
            self.state[str(message.id)] = record
            self._save_state()
            await message.edit(
                embed=build_task_embed(
                    piece, status=record["status"], wiki_task_id=wiki_task_id,
                    assignees=_task_assignees(task),
                    score=score,
                ),
                view=_TaskView(self),
            )
            await interaction.followup.send(verb, ephemeral=True)
            await self.refresh_todo()
            return
        status = "in_progress" if action == "in_progress" else "done"
        if not wiki_task_id:
            await interaction.followup.send("No wiki task linked to this message.", ephemeral=True)
            return
        try:
            task = await self._wiki_api(
                "PATCH", f"/api/tasks/{wiki_task_id}", json={"status": status},
            )
            task = task.get("task") or {}
        except WikiApiError as exc:
            await interaction.followup.send(f"Wiki backend error: {exc.error}", ephemeral=True)
            return
        if status == "done":
            # A finished task leaves #todo. The wiki keeps the record, and
            # the message in #astra-feedback it came from is not touched.
            try:
                await message.delete()
            except Exception as exc:  # noqa: BLE001 - fall back to a greyed embed below
                log.warning("Could not delete finished task message %s: %s", message.id, exc)
            else:
                self.state.pop(str(message.id), None)
                self._save_state()
                await interaction.followup.send(
                    "Marked done and removed from #todo.", ephemeral=True)
                await self.refresh_todo()
                return
        record["status"] = status
        self.state[str(message.id)] = record
        self._save_state()
        await message.edit(
            embed=build_task_embed(
                piece, status=status, wiki_task_id=wiki_task_id,
                assignees=_task_assignees(task), score=score,
            ),
            view=_TaskView(self),
        )
        await interaction.followup.send(f"Marked {status.replace('_', ' ')}.", ephemeral=True)
        await self.refresh_todo()

    async def handle_review_button(self, interaction: discord.Interaction, action: str):
        message = interaction.message
        embed = (message.embeds or [None])[0]
        record = self.state.get(str(message.id), {})
        piece = record.get("piece") or (embed.description if embed else "") or ""
        score = record.get("score")
        trigger = record.get("trigger") or "score_band"
        source_id = record.get("source_message_id") or _embed_source_id(embed)
        thread_id = record.get("thread_id")
        await interaction.response.defer()
        if action == "is_task":
            try:
                task = await self._create_wiki_task(
                    title=piece,
                    description=f"Reviewed in #human-intervention, source message {source_id}",
                    created_by=interaction.user.id,
                )
            except WikiApiError as exc:
                await interaction.followup.send(f"Wiki backend error: {exc.error}", ephemeral=True)
                return
            self._append_label(
                text=piece, label=1, source_message_id=source_id,
                score=score, trigger=trigger, reviewer_id=interaction.user.id,
            )
            source = SimpleNamespace(
                id=source_id,
                jump_url=record.get("source_url"),
                author=(SimpleNamespace(display_name=record["source_author"])
                        if record.get("source_author") else None),
            )
            posted = None
            try:
                posted = await self._post_task_embed(
                    piece=piece, task=task, source_message=source,
                    score=score, trigger=trigger,
                )
            except Exception as exc:  # noqa: BLE001 - fall back below so the task is not lost
                log.warning("Could not post confirmed task to #todo: %s", exc)
            await self._delete_thread(thread_id)
            if posted is not None:
                self.state.pop(str(message.id), None)
                self._save_state()
                try:
                    await message.delete()
                except Exception as exc:  # noqa: BLE001 - the task is safe in #todo
                    log.warning("Could not delete review message: %s", exc)
                await interaction.followup.send("Created as a task in #todo.", ephemeral=True)
                return
            self.state[str(message.id)] = {
                "kind": "task",
                "wiki_task_id": task.get("id"),
                "source_message_id": source_id,
                "piece": piece,
                "score": score,
                "trigger": trigger,
                "thread_id": None,
                "status": task.get("status") or "open",
                "channel_id": getattr(message.channel, "id", None),
            }
            self._save_state()
            await message.edit(
                embed=build_task_embed(
                    piece, status=task.get("status") or "open",
                    wiki_task_id=task.get("id"),
                    assignees=_task_assignees(task), score=score,
                ),
                view=_TaskView(self),
            )
            await interaction.followup.send(
                "Created as a task, but #todo could not be reached, so it stayed here.",
                ephemeral=True,
            )
            return
        self._append_label(
            text=piece, label=0, source_message_id=source_id,
            score=score, trigger=trigger, reviewer_id=interaction.user.id,
        )
        await self._delete_thread(thread_id)
        self.state.pop(str(message.id), None)
        self._save_state()
        try:
            await message.delete()
        except Exception as exc:  # noqa: BLE001 - label is saved either way
            log.warning("Could not delete review message: %s", exc)
        await interaction.followup.send("Marked as not a task.", ephemeral=True)

    async def cog_load(self) -> None:
        # Persistent views: register before any interaction is dispatched.
        if self.bot is None:
            return
        try:
            self.bot.add_view(_TaskView(self))
            self.bot.add_view(_ReviewView(self))
        except Exception as exc:  # noqa: BLE001 - view registration must not kill the cog
            log.warning("Could not register persistent task views: %s", exc)
        if not self._sync_loop.is_running():
            self._sync_loop.start()

    # -- slash commands -----------------------------------------------------

    @app_commands.command(name="task_add", description="Add a task to the wiki to-do list")
    @app_commands.describe(
        title="Task title (1-200 chars)",
        description="Optional detail",
        priority="Priority",
        assignee="Who should do it",
    )
    @app_commands.choices(priority=[
        app_commands.Choice(name="low", value="low"),
        app_commands.Choice(name="medium", value="medium"),
        app_commands.Choice(name="high", value="high"),
    ])
    async def task_add(self, interaction: discord.Interaction, title: str,
                       description: str | None = None,
                       priority: str | None = None,
                       assignee: str | None = None):
        if not title or not title.strip():
            await interaction.response.send_message("A title is required.", ephemeral=True)
            return
        body: dict = {"title": title.strip()[:200], "source": "astrabot",
                      "created_by": str(interaction.user.id)}
        if description:
            body["description"] = description[:2000]
        if priority:
            body["priority"] = priority
        if assignee:
            body["assignee"] = assignee[:100]
        try:
            payload = await self._wiki_api("POST", "/api/tasks", json=body)
        except WikiApiError as exc:
            await interaction.response.send_message(
                f"Wiki backend error: {exc.error}", ephemeral=True)
            return
        task = payload.get("task") or {}
        await interaction.response.send_message(
            f"Task created: {task.get('id')} - {task.get('title')}", ephemeral=True)
        await self.refresh_todo()

    @app_commands.command(name="task_list", description="List wiki to-do tasks")
    @app_commands.describe(status="Filter by status", priority="Filter by priority")
    @app_commands.choices(
        status=[
            app_commands.Choice(name="open", value="open"),
            app_commands.Choice(name="in_progress", value="in_progress"),
            app_commands.Choice(name="done", value="done"),
        ],
        priority=[
            app_commands.Choice(name="low", value="low"),
            app_commands.Choice(name="medium", value="medium"),
            app_commands.Choice(name="high", value="high"),
        ],
    )
    async def task_list(self, interaction: discord.Interaction,
                        status: str | None = None, priority: str | None = None):
        query = []
        if status:
            query.append(f"status={status}")
        if priority:
            query.append(f"priority={priority}")
        path = "/api/tasks" + ("?" + "&".join(query) if query else "")
        try:
            payload = await self._wiki_api("GET", path)
        except WikiApiError as exc:
            await interaction.response.send_message(
                f"Wiki backend error: {exc.error}", ephemeral=True)
            return
        tasks = (payload.get("tasks") or {}).get("items") or []
        counts = (payload.get("tasks") or {}).get("counts") or {}
        embed = discord.Embed(title="To-do tasks", color=discord.Color.green())
        if counts:
            embed.add_field(
                name="Counts",
                value=f"open {counts.get('open', 0)}, in progress {counts.get('in_progress', 0)}, "
                      f"done {counts.get('done', 0)}, total {counts.get('total', 0)}",
                inline=False,
            )
        for task in tasks[:25]:
            assignee = ", ".join(_task_assignees(task)) or "-"
            embed.add_field(
                name=f"{task.get('id')} - {task.get('title')}",
                value=f"{task.get('status')} | {task.get('priority')} | {assignee}",
                inline=False,
            )
        if not tasks:
            embed.description = "No tasks match."
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="task_done", description="Mark a wiki task done")
    @app_commands.describe(task_id="Task id, e.g. task-0001 or 1")
    async def task_done(self, interaction: discord.Interaction, task_id: str):
        await self._set_status(interaction, task_id, "done")

    @app_commands.command(name="task_start", description="Mark a wiki task in progress")
    @app_commands.describe(task_id="Task id, e.g. task-0001 or 1")
    async def task_start(self, interaction: discord.Interaction, task_id: str):
        await self._set_status(interaction, task_id, "in_progress")

    async def _set_status(self, interaction: discord.Interaction, task_id: str, status: str):
        task_id = normalize_task_id(task_id)
        try:
            payload = await self._wiki_api(
                "PATCH", f"/api/tasks/{task_id}", json={"status": status},
            )
        except WikiApiError as exc:
            await interaction.response.send_message(
                f"Wiki backend error: {exc.error}", ephemeral=True)
            return
        task = payload.get("task") or {}
        await interaction.response.send_message(
            f"{task.get('id')} is now {task.get('status')}.", ephemeral=True)
        if status == "done":
            await self._remove_task_messages(str(task.get("id") or task_id))
        await self.refresh_todo()

    @app_commands.command(name="task_edit", description="Edit a wiki task")
    @app_commands.describe(
        task_id="Task id, e.g. task-0001 or 1",
        title="New title",
        description="New description",
        priority="New priority",
        assignee="New assignee (replaces the assignee list)",
    )
    @app_commands.choices(priority=[
        app_commands.Choice(name="low", value="low"),
        app_commands.Choice(name="medium", value="medium"),
        app_commands.Choice(name="high", value="high"),
    ])
    async def task_edit(self, interaction: discord.Interaction, task_id: str,
                        title: str | None = None, description: str | None = None,
                        priority: str | None = None, assignee: str | None = None):
        body: dict = {}
        if title:
            body["title"] = title.strip()[:200]
        if description is not None:
            body["description"] = description[:2000]
        if priority:
            body["priority"] = priority
        if assignee is not None:
            body["assignee"] = assignee[:100]
        if not body:
            await interaction.response.send_message("Nothing to change.", ephemeral=True)
            return
        try:
            payload = await self._wiki_api(
                "PATCH", f"/api/tasks/{normalize_task_id(task_id)}", json=body,
            )
        except WikiApiError as exc:
            await interaction.response.send_message(
                f"Wiki backend error: {exc.error}", ephemeral=True)
            return
        task = payload.get("task") or {}
        await interaction.response.send_message(
            f"{task.get('id')} updated.", ephemeral=True)
        await self.refresh_todo()

    @app_commands.command(name="task_delete", description="Delete a wiki task (permanent)")
    @app_commands.describe(task_id="Task id, e.g. task-0001 or 1")
    async def task_delete(self, interaction: discord.Interaction, task_id: str):
        try:
            await self._wiki_api("DELETE", f"/api/tasks/{normalize_task_id(task_id)}")
        except WikiApiError as exc:
            await interaction.response.send_message(
                f"Wiki backend error: {exc.error}", ephemeral=True)
            return
        await interaction.response.send_message(
            f"{normalize_task_id(task_id)} deleted.", ephemeral=True)
        await self.refresh_todo()

    @app_commands.command(name="task_stats", description="Show task classifier and label counts")
    async def task_stats(self, interaction: discord.Interaction):
        since, total = self.labels_since_retrain()
        health = await self._classifier_health()
        _, api_key = wiki_api_config()
        lines = [
            f"Labels collected: {total}",
            f"Labels since last retrain: {since} (retrain at {RETRAIN_LABEL_COUNT})",
            f"Retrain ready: {'yes' if since >= RETRAIN_LABEL_COUNT else 'no'}",
            f"Classifier service: {'up, model loaded' if health and health.get('model_loaded') else 'down or model not loaded'}",
            f"Wiki backend key configured: {'yes' if api_key else 'no'}",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Tasks(bot))
