# Astrabot tasks cog

Watches mcdungeons #general, asks the local AstraThought classifier
whether each message is a task, and posts tasks to #astra-feedback
where the team can claim them. Borderline messages go to human
review in a thread in #astra-feedback instead of being auto-posted.

## Files

- `cogs/tasks.py`: the cog.
- `tests/test_tasks.py`: 22 tests, no network, token, or bot needed.

## How it works

1. A message in #general (1407601803536760905) is sent to the
   classifier service on loopback (`TASKS_CLASSIFIER_URL`, default
   `http://127.0.0.1:13383`), which splits it into pieces and returns
   one decision per piece: `auto_task`, `auto_drop`, or `review`,
   with a score and a trigger. The cog trusts that decision.
2. `auto_task` pieces become wiki tasks (POST /api/tasks on the wiki
   backend, `WIKI_API_URL` default `http://127.0.0.1:13382`, header
   `X-Astra-Wiki-Key`) and are posted to #astra-feedback
   (1568142915631325355) with Claim / Unclaim, In Progress, Done,
   Not a Task. Claim is a toggle: it adds the clicker to the task's
   assignees, and clicking again removes them. Several people can
   claim the same task; the embed lists them all under Assignees.
3. `review` pieces are posted as amber "Needs review" embeds in
   #astra-feedback, with a public thread created on the message.
   "Is a task" creates the wiki task, edits the parent message in
   place into the confirmed task embed, and deletes the thread.
   "Not a task" deletes the parent message and the thread.
4. `auto_drop` pieces are dropped. Silence is not a label.

Review rulings are admin-only (config admin IDs, guild owner, or
bot owner, same check as the audiobooks cog). Thea is the label
authority and the reviewer list is still open, so the narrow
default is deliberate. Claim / In Progress / Done are team-wide.

## Labels and retraining

Every button ruling appends one JSON line to
`data/task_labels.jsonl` (env `TASKS_LABELS_PATH`) and updates the
overrides file `data/task_overrides.json` (env
`TASKS_OVERRIDES_PATH`), so the next identical piece is decided by
the human ruling immediately, without retraining. Labels are never
appended to `train.jsonl` automatically. Thea merges them in a
batch. Retraining happens only when 50 new labels have been
collected, no matter how long that takes. No time-based retrain,
no per-ruling micro-training. `/task_stats` reports the count.

Button state survives restarts: persistent views (timeout=None,
fixed custom_id) are registered in `cog_load`, and per-message
state lives in `data/tasks_state.json` (env `TASKS_STATE_PATH`),
keyed by feedback message id, plus the task id in the embed.

## Slash commands

- `/task_add` (admin), `/task_edit` (admin), `/task_delete` (admin)
- `/task_done`, `/task_start`, `/task_list`, `/task_stats` (team-wide)

Task ids accept `1`, `task-1`, or `task-0001`.

## Environment variables (bot host, set via setx, never committed)

| Var | Purpose | Default |
|---|---|---|
| `WIKI_API_URL` | Wiki backend base URL | `http://127.0.0.1:13382` |
| `WIKI_API_KEY` | Shared secret for the wiki backend | (none, cog reports not configured) |
| `TASKS_CLASSIFIER_URL` | Classifier service base URL | `http://127.0.0.1:13383` |
| `TASKS_LABELS_PATH` | Labels JSONL file | `data/task_labels.jsonl` |
| `TASKS_OVERRIDES_PATH` | Overrides JSON file | `data/task_overrides.json` |
| `TASKS_STATE_PATH` | Button state JSON file | `data/tasks_state.json` |
| `TASKS_ADMIN_IDS` | Comma-separated admin Discord user IDs | (config, guild owner, bot owner) |

The bot also needs Manage Threads permission in #astra-feedback
so it can create and delete review threads. If the classifier
service is down, watched messages are logged and skipped. If the
wiki backend is down or has no key, slash commands reply
ephemerally and watched messages are skipped. The cog never
crashes the bot and never retries in a loop.

## Enable on the host

The classifier service must be running first (see the
AstraThought repo, `classifier_service.py`). Then, in PowerShell
on the bot host:

This branch is `feature/tasks-local-model`. It is not merged.
On the bot host, check it out to review or test it:

```powershell
cd 'G:\My Drive\codingShit\Astrabot'
git fetch
git checkout feature/tasks-local-model
git pull
```

Add `"cogs.tasks",` to `startup_extensions` in the **host**
`bot.py` only. Do not push a `bot.py` change: the host `bot.py`
has a local change (the `cogs.clip` line is commented out) and
the self-update watcher never reloads `bot.py`. Re-comment clip
if a pull complains about it.

Set the env vars if they are not set yet (example values, use
the real key, never paste it in chat):

```powershell
setx WIKI_API_KEY "<the shared wiki key>"
setx TASKS_CLASSIFIER_URL "http://127.0.0.1:13383"
```

Then restart the bot:

```powershell
py -3.14 bot.py
```

Do not change `commands_for_guild` in `bot.py` silently. The
tasks slash commands are global by default, like `/wiki`; the
audiobooks-only guild (454792201633792001) gets only audiobooks
commands today, and whether tasks commands should be excluded
there is still an open decision for Thea.

## Tests

Run from the repo root with a Python that has discord.py
installed:

```powershell
py -3.14 -m unittest discover -s tests -v
```

22 tests, all passing at build time (run with the test venv
Python in the build workspace).
