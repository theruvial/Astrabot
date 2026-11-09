# Astrabot tasks cog

Watches mcdungeons #astra-feedback, asks the local AstraThought classifier
whether each message is a task, and posts tasks to #todo
where the team can claim them. Borderline messages go to human
review in a thread in #human-intervention instead of being auto-posted.

## Channels

All three are in mcdungeons, under Task Tracking:

| Role | Channel | ID |
|---|---|---|
| Reads messages from | #astra-feedback | `1568142915631325355` |
| Decisions needing a person | #human-intervention | `1568329972928610546` |
| Confirmed tasks | #todo | `1568332728573759621` |
| Claimed by Steve | #todo-steve | `1569493103033843822` |
| Claimed by Justin | #todo-justin | `1569493185946853517` |
| Claimed by Thea | #todo-thea | `1569493220151394517` |
| Claimed by Kathi | #todo-kathi | `1569493240896290816` |

Channel IDs are constants at the top of `cogs/tasks.py`. Changing them
needs a bot restart. Embeds already posted elsewhere keep working.

## Files

- `cogs/tasks.py`: the cog.
- `tests/test_tasks.py`: 22 tests, no network, token, or bot needed.

## How it works

1. A message in #astra-feedback (1568142915631325355) is sent to the
   classifier service on loopback (`TASKS_CLASSIFIER_URL`, default
   `http://127.0.0.1:13383`), which splits it into pieces and returns
   one decision per piece: `auto_task`, `auto_drop`, or `review`,
   with a score and a trigger. The cog trusts that decision.
2. `auto_task` pieces become wiki tasks (POST /api/tasks on the wiki
   backend, `WIKI_API_URL` default `http://127.0.0.1:13382`, header
   `X-Astra-Wiki-Key`) and are posted to #todo (1568332728573759621)
   with Claim / Unclaim, In Progress, Done,
   Not a Task. Claim is a toggle: it adds the clicker to the task's
   assignees, and clicking again removes them. Several people can
   claim the same task; the embed lists them all under Assignees.
3. `review` pieces are posted as amber "Needs review" embeds in
   #human-intervention (1568329972928610546), with a public thread created on the message.
   "Is a task" creates the wiki task, posts the confirmed task embed
   in #todo, and deletes the review message and its thread. If #todo
   cannot be reached, the review message is edited in place instead.
   "Not a task" deletes the parent message and the thread.
4. `auto_drop` pieces are dropped. Silence is not a label.
5. **Claiming moves the task to your channel.** Each person has a
   personal channel (the table above; `PERSONAL_CHANNELS` in
   `cogs/tasks.py` maps Discord user ids to channel ids). When someone
   claims a task in #todo, the bot posts the same embed (status,
   assignees and source link kept) in their channel and deletes the
   one in #todo. Discord cannot move a message, so the new one is
   posted first. If two people claim it, it stays in the first
   claimer's channel. **Whenever anyone unclaims, the task goes back
   to #todo**, even if someone else is still assigned; the next claim
   sends it to the first claimer's channel again. In Progress changes the status in place, and Done
   deletes the message wherever it is. Someone with no personal
   channel can still claim, and the message stays where it is. To add
   a person, create their channel and add one line to
   `PERSONAL_CHANNELS`, then restart the bot.
6. **Done removes the task from #todo.** The Done button, or
   `/task_done`, deletes the task's #todo message. The wiki keeps the
   record, and the original message in #astra-feedback is not touched.
   If the bot cannot delete the message, Done greys out the embed
   instead. A task marked done on the wiki site is cleaned up by the
   next sync (see below).

## Live to-do list (`TODO.md`)

The cog keeps a markdown list of every task that is not done, grouped
In progress then Open, high priority first, with assignees and a link
to each task's #todo message. The default path is `data/TODO.md`
(`TASKS_TODO_MARKDOWN_PATH` to change it). It is rewritten whenever a
task changes through the bot (new task, Claim, In Progress, Done, and
the slash commands) and every 5 minutes, which also picks up changes
made on the wiki site. It is only written when the list actually
changed. Do not edit it by hand: the next change overwrites it. If
the wiki backend is down, the file and the messages are left alone.

The 5-minute sync also deletes the #todo messages of tasks the wiki
shows as done, so the first sync after this was deployed clears any
done tasks that still have a message.

#todo and #human-intervention are team-wide: anyone who can see
the channel can rule on a review item, use Claim / Unclaim,
In Progress and Done, and use every slash command. A ruling still
records who made it, and every ruling lands in the labels file
for the batch retrain, never straight into the training data.

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

- `/task_add`, `/task_edit`, `/task_delete`, `/task_done`,
  `/task_start`, `/task_list`, `/task_stats` (all team-wide)

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
| `TASKS_TODO_MARKDOWN_PATH` | Live markdown to-do list | `data/TODO.md` |

The bot also needs Manage Threads permission in #human-intervention
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
