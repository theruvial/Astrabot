# Astrabot — Design & Context Document

**Purpose:** context for a fresh Claude session (or any new agent) to work on Astrabot without re-learning the project from scratch. Everything below is as of **2026-11-05**. Where a fact is unverified or host-side, it is marked as such.

**Keeping this current (Thea's rule, 2026-11-05):** this file is how changes are tracked between AI agents. Update it each time an agent finishes work for the day, or whenever asked: correct anything that changed in place, and add a dated entry to the change log (§12). Never record credential values here.

**Owner:** Thea (GitHub: `theruvial`, Discord admin user ID `151408413744693248`, email `theruvial237@gmail.com`)
**Repo:** `theruvial/Astrabot` (public since 2026-11-11, Python, discord.py 2.x), default branch `master`. The old history is in the private `theruvial/Astrabot-old`.
**Bot application ID (public):** `622544371098845221`
**Assistant note:** Thea's standing workflow rule (set 2026-11-05) — Muse/Claude no longer needs her input for every request, just keep working. Only ask first for merges, pull-request creation, or anything that could destroy/damage the main branch. If a change is one she would want to know about, tell her; otherwise proceed.

---

## 1. What Astrabot is

A personal Discord bot (discord.py 2.x) that started as a small 2021 bot and grew into a small platform:

- **Audiobook request system** — the main feature: a Discord + web workflow for requesting audiobooks for Thea's Audiobookshelf server, with admin fulfilment.
- **Moderation cog** — kick, purge, streaming notices, speak, reload.
- **Wiki cog** — serves the Aboard the Astra game-mechanics wiki as a Discord attachment.
- **Self-update watcher** — polls git and hot-reloads changed cogs.
- **Tasks cog** — reads #astra-feedback, classifies messages with the local AstraThought model, sends unclear items to #human-intervention, and posts tasks to #todo (§5a).
- **absLogin integration** — a separate repo/service (see §7) started from the same launcher.

The bot runs on Thea's **Windows host at 192.168.4.80**. Local checkout: `G:\My Drive\codingShit\Astrabot`. Python is run as `py -3.14 bot.py` (plain `python` is a different install without discord.py — do not tell her to `pip install discord` into the wrong Python).

---

## 2. Host & deployment

- **Status of this section:** the console, launcher fixes and command-scope changes below were merged to `master` 2026-11-05. They take effect once the old bot is closed and a launcher is started again.
- **Launchers (repo root; use one, never both, each starts its own bot):**
  - `startConsole.bat` pulls Astrabot and absLogin, then opens the **Golurk Console** (`astra_console.py`, tkinter, dark theme; renamed from Astrabot Console). No separate absLogin window.
  - `startBot.bat` is the plain console launcher: pulls, starts absLogin, runs the bot.
- **Golurk Console:** the six windows below sit in one collapsible "Astrabot" group, each also collapsible (state kept in the untracked `console.json`; they can never be removed). **Settings** adds extra services (program, PowerShell or batch file; stop commands; start on open) with their own pane and Start/Restart/Stop/Remove; only those can be removed. Helpers are in `console_services.py`, the Settings window in `console_settings.py`, details in `docs/CONSOLE.md`. It owns the bot, the audiobook site (13380, hosted by the audiobooks cog), absLogin (13381), the wiki backend (13382, `C:\codingShit\Aboard-the-Astra-Wiki\Tools\wiki\backend\server.py`), the AstraThought classifier (13383) and the portfolio site (13384, hosted by the portfolio cog like the audiobook site, so its pane shows that cog's log lines and "Restart Portfolio" reloads just that cog), each with its own pane and crash handling. A Decisions panel shows the classifier's last 50 decisions and fetches on events rather than polling. "Pull + Restart" pulls Astrabot, absLogin and Wiki but not AstraThought, so use "Restart Classifier" for that. Every start of a service first stops anything still holding its port. A **Watchdog** button (on by default; `--no-watchdog` starts with it off) restarts the whole console when a service's watcher thread has died on two checks in a row, at most 3 times an hour (history in `logs/watchdog_restarts.json`), then gives up and says so, and the bot DMs its owner (the console writes `logs/watchdog_alert.flag`; the Moderation cog's flag watcher sends it, or sends it when the bot comes back). The Decisions box refreshes on classifier log lines (`#N [review ...]`), on "Starting classifier" and on "Classifier service listening"; it used to match only lines that began with `#N [`, which never occur, so it never filled. Closing the window stops everything. Do not run a hand-started `server.py` or `app.py` alongside it.
- **Restart Site side effect:** a reload runs `cog_load`, so it clears and re-posts the requests channel (§4). Thea chose cog reload over a separate web-only restart on 2026-11-05.
- **How Restart Site reaches the bot:** the console writes the cog name to `logs\reload_cog.flag`; `cogs/moderation.py` polls it every 2 s and calls `reload_extension`. A file, not an HTTP route, because the site binds `0.0.0.0`. The watcher is in moderation because a watcher inside the reloaded cog would cancel itself.
- **Crash tracking (both launchers, console also covers the wiki and absLogin):** output is appended to `logs\bot.log` (`logs\wiki.log`, `logs\absLogin.log` for those), UTF-8. Every start/stop is timestamped with its exit code in `logs\crashes.log`. Auto-restarts after 10 s on a crash; **stops after 5 crashes in a row**; a clean exit (code 0) stops normally. In the console a run of 60 s or more resets the streak.
- **Piped Python gotcha:** when output is piped, Python block-buffers stdout and encodes as cp1252. Always run with `-u` and `PYTHONIOENCODING=utf-8`, or `print()` output never appears and emoji in guild names raise `UnicodeEncodeError`. PowerShell 5.1's `Tee-Object` also writes UTF-16 and wraps stderr in `NativeCommandError` noise, so it is no longer used.
- **Logging:** `bot.py` calls `discord.utils.setup_logging(level=logging.INFO)`, because `bot.start()` (unlike `bot.run()`) configures none and INFO lines were dropped. The console sends `audiobooks*` and `aiohttp*` loggers to the Site pane; `aiohttp.access` lines are request logs.
- **No process killing on startup (2026-11-05):** the kill step was removed at Thea's request. Running a launcher while a bot is up gives two bots, so stop the old one first. `git pull -q` keeps startup quiet but still shows errors.
- **Logs are untracked:** `logs/` and `crash.log` are in `.gitignore` — never commit them.
- **Databases are untracked (2026-11-05):** `audiobooks.db` and `thi.db` are in `.gitignore`. While tracked, a stash and checkout at 18:20 on 2026-11-05 swapped the live `audiobooks.db` for the empty committed copy. The data was restored from stash `7c01756`; do not drop that stash until the restore is confirmed live. Stopping tracking through a branch merge deletes the working file, so do that kind of change in the host checkout.
- **Host bot.py divergence (important):** the host's `bot.py` has a local change — the `cogs.clip` line is commented out. Pulling `master` may conflict on `bot.py`; re-comment clip in the new file if git complains. **Never push the staging/host bot.py.**
- **Restart to go live:** every code change pushed to `master` needs a `git pull` + bot restart on the host to take effect. The self-update watcher (see §6) hot-reloads *cog* changes only — never `bot.py` or `config.json`.
- **Host pull quirk (seen 2026-11-05):** the host's local branch still tracks `audiobook-requests`, so a plain `git pull` fails with "Your configuration specifies to merge with the ref 'refs/heads/audiobook-requests' … but no such ref was fetched." Fix: `git checkout master` first, then `git pull`.

---

## 3. Configuration — read this first

### `config.json` is UNTRACKED (since 2026-11-05)

- The repo contains only a barebones template (`config.json`, all empty/zeroed) and a tracked **`config.example.json`** with the same shape.
- **Never push a real `config.json` again.** The real config lives on the bot host only.
- `config.py` does `config = load_json("config.json")`; `bot.py` needs `config["prefix"]`, `config["description"]`, `config["token"]`, optional `config.get("db", "thi.db")`.

### ⚠️ Token in git history

The old live bot token was merged into `master` in PR #9 (it was in the pre-untracking `config.json`). **Untracking does not erase history — the token must be rotated in the Discord Developer Portal.** A new token was visible in a Developer Portal screenshot Thea sent 2026-11-05, so rotation appears done, but it is **not confirmed in chat**. Never repeat any token value seen in screenshots or fetched files.

### Known config values (host)

| Key | Value |
|---|---|
| `prefix` | `.` |
| `guild_id` (audiobooks) | `454792201633792001` |
| `request_channel_id` (audiobooks) | `1565347062965477386` |
| `admin_user_ids` (audiobooks) | `[151408413744693248]` |
| `web_host` / `web_port` (audiobooks web UI) | `0.0.0.0` / `13380` |
| `db_path` | `audiobooks.db` |

### Environment variables (bot host, set via `setx`, never committed)

| Var | Purpose |
|---|---|
| `WIKI_API_KEY` | Shared secret for the Astra wiki backend (see §5). Same value on bot host and backend host. |
| `WIKI_API_URL` | Wiki backend base URL, default `http://127.0.0.1:13382` |
| `ABS_API_KEY` | Audiobookshelf API key for absLogin (see §7). **A key was pasted in chat/screenshots during debugging — rotation not confirmed. Never repeat it.** |

### Intents & invite

- `bot.py` uses `discord.Intents.all()` → **all three privileged intents must be enabled** in the Developer Portal (Server Members, Message Content, Presence), or discord.py refuses to connect with `PrivilegedIntentsRequired` (this exact crash happened 2026-11-05).
- Actually used: Members only (by the disabled roles cog); Message Content and Presence are not genuinely used by any loaded cog. Tightening to `Intents.default()` (+ `members=True` only if roles re-enable) is a known possible cleanup — not done.
- OAuth2 scopes needed: **`bot` + `applications.commands`** only; no user-OAuth scopes.
- **Invite permissions bitfield `93186`** for currently loaded cogs: View Channel 1024, Send Messages 2048, Embed Links 16384, Read Message History 65536, Manage Messages 8192 (`/purge` + audiobooks resync deletes), Kick Members 2 (`/kick`). **Add Attach Files 32768 for `/wiki` → `125954`.**
- Corrected invite URL (client_id `622544371098845221`):
  `https://discord.com/oauth2/authorize?client_id=622544371098845221&permissions=93186&integration_type=0&scope=bot%20applications.commands`
  (`/wiki` variant uses `permissions=125954`.)
- **Not needed:** Ban Members (no `.ban(` anywhere), Timeout, Manage Channels, role creation (only add/remove roles).

### Command scope (replaces the `97ece6c3` per-guild filter, 2026-11-05)

- **Cogs declare scope.** Commands are global unless a cog limits them with `app_commands.guilds(...)`. Every audiobooks command (just `/email_user` today) is limited to Thea's private guild **`454792201633792001`**, read from `audiobooks.guild_id` in `config.json`. If that key is missing, `setup()` in `cogs/audiobooks.py` falls back to `DEFAULT_GUILD_ID`, the same guild, never to global.
- **Behaviour change Thea accepted by proposing this design:** the private guild now also sees the global commands (`/kick`, `/purge`, `/wiki`, etc.); it used to get only the audiobooks commands.
- **Syncing:** the audiobooks cog syncs its guild in `setup()`, not `cog_load` (discord.py runs `cog_load` before adding commands), so reloads stay in sync. Global commands cannot sync per cog, because a global sync replaces the whole list, so `Astrabot._sync_commands()` does one global sync on first `on_ready`, then syncs each guild to clear the per-guild copies the old filter registered.
- Tests: `tests/test_guild_commands.py`.

---

## 4. Audiobook request system (the main feature)

**Design doc:** `~/workspace/your_files/audiobook-requests-design.md` (Thea's local copy; repo copy is `docs/AUDIOBOOKS.md`).
**Staging (local, not the repo):** `~/workspace/astrabot-audiobooks/`; test venv `~/workspace/astrabot-venv/` (discord.py 2.7.1 — run cog tests with this venv's Python; system Python 3.12 has no `discord` module).

### Files

`cogs/audiobooks.py`, `audiobooks_db.py`, `audiobooks_providers.py`, `audiobooks_web.py`, `audiobooks_notify.py`, `bot.py` (adds `cogs.audiobooks`), `docs/AUDIOBOOKS.md`, tests in `tests/test_audiobooks.py`.

### How it works

- **Database:** sqlite3, file `audiobooks.db`. Users are keyed by **email** (see email migration below).
- **Discord flow:** requests are posted as embeds in a **dedicated requests channel** (`request_channel_id` 1565347062965477386). **On every bot start the cog clears that channel completely and re-posts one embed per DB row, oldest first.** Treat that channel as dedicated — nothing else belongs there.
- **Embed buttons:** `Fulfill Request` and `Reject Request`. After Fulfill, a `Request Fulfilled` button appears; it sets status Fulfilled, emails the requester: *"The requested book {book} has been added to the server"*, and deletes the Discord message. Fulfilled requests are not re-posted on restart.
- **Admin check:** config admin IDs, **or** guild owner, **or** bot application owner via `Bot.is_owner()`.
- **Series requests are partial, not all-or-nothing** — create requests only for series books that have a valid audiobook.
- **Goodreads series enumeration:** caps at the page's "X primary works" count, drops range-headed collected editions ("Book 1-3"), keeps decimal entries (Book 1.5).

### Providers

- **Audible links are NOT scraped at all.** Accepted from the URL alone; title taken from the `/pd/` slug. (Audible has no regular books.)
- **Amazon is an accepted provider**, scraped server-side: path must contain `/dp/` or `/gp/product/` AND the page text must show an audiobook (Audible Audiobook format, narrator, or listening length). **Amazon blocks Muse with an explicit AI-agent refusal page — do not bypass it.** The bot's server-side Amazon fetch on her host may hit the same block live; not yet live-tested there.

### Email, not SMS (migrated 2026-10-29)

- Login is an **email address**; DB `users.email` is the primary key.
- Old phone tables are preserved as `*_phone_legacy` on first open; **old phone accounts must re-create with email**.
- Notifications are **SMTP emails**; Twilio/SMS was removed. Email sender needs SMTP host/user/password via env vars or `config audiobooks.email`. Thea confirmed 2026-10-29 her host config has SMTP info.
- **Google Voice was researched and recommended against** (no public API); she did not pursue it.

### Web UI

- **Material Design, dark theme** (requests site only): background `#141218`, cards `#1e1b24`, primary buttons `#d0bcff`. Future projects default to dark mode for their web interface unless she specifies otherwise.
- Runs on **`0.0.0.0:13380`** (changed from `127.0.0.1:8080`, which caused nginx 504s — nginx for `requests.theruvial.com` proxies to `192.168.4.80:13380`).
- **Website cancel = hard delete:** cancelling from the website deletes the DB row, deletes its Discord message, and emails the requester *"Your audiobook request for {book} has been cancelled."*
- **Admin Reject (different from cancel):** edits the embed to Rejected; the row is hard-deleted. Reject opens a **modal with an optional reason** (max 500 chars); the requester is emailed the **original wording verbatim first** (*"Request Rejected for {book} Speak to Thea for more information"*) plus `"\n\nReason: {reason}"` if given, also shown on the embed. Blank reason keeps the original wording exactly.
- **Fulfilled-email bug fix:** the fulfilled email is sent **before** the embed edit, and embed-edit failures are logged, not raised.
- **Admin web dashboard:** `/admin`, **only for logged-in `theruvial237@gmail.com`**; everyone else gets 403. Manages users (edit email cascading to requests/sessions, edit name, create, delete with their requests) and requests (edit book/link, Fulfill, Request Fulfilled, Reject with reason, Delete) with the same emails/embeds as Discord. Admins land on `/admin` → `/admin/users`; no admin button is shown on the regular page. Admin is split into `/admin/users` and `/admin/requests` with a fixed **72px icon sidebar**.
- **Topbar buttons:** "Create account" → `https://audio.theruvial.com/signup` and "Sign in to Audiobookshelf" → `https://audio.theruvial.com/login`, next to "New Request +". She has not said whether she wants only one.

### Custom email feature (added 2026-11-01, commit `b270df59`)

- Discord: admin-only **`/email_user`** — inputs `requester` (autocomplete "FirstName (email)", value is the email), `message`, optional `subject` (default **"A message from Thea"**). Body is sent **exactly as typed**, not tied to a request. A first name shared by several users is **refused as ambiguous**; non-admins get no autocomplete and cannot use the command.
- Also an Email section at `/admin/email`. `config.json` was NOT pushed with this change. Tested: 25 tests passed.

### History

- Originally (2026-10-29) design-only on branch `aiTestBranch`; Thea reversed the no-build instruction the same day and asked for a full build.
- Built on branch `audiobook-requests`, merged to `master` via **PR #9** (2026-11-05, merge commit `0b6744c0`; 29 commits, 30 files). That merge also brought in the discord.py 2.x port, the self-update watcher, `/email_user`, and the `/wiki` cog.
- Project was marked "completed for now" by Thea on 2026-10-29, then re-raised repeatedly — do not assume it is closed.
- **Unconfirmed live-test set:** admin login landing, Audible link and Amazon example link live, Fulfill → Request Fulfilled email on a fresh request.

---

## 5. Wiki cog (`/wiki`) and the Astra wiki backend

### The cog

- `cogs/wiki.py` — slash command `/wiki` sends the wiki as a Discord attachment named `astra-wiki.html`; ephemeral error if the file is missing.
- **Current behaviour (commit `0c69c5ff`):** when `WIKI_API_KEY` env is set, the cog fetches the fresh standalone from the backend (`WIKI_API_URL` env, default `http://127.0.0.1:13382`, path `/wiki/astra-wiki.html`) via aiohttp with the `X-Astra-Wiki-Key` header (15 s timeout) and sends those bytes. On any fetch failure, or with no key set, it falls back to the bundled `cogs/wiki.html`.
- Bundled file: `cogs/wiki.html` — the split standalone (18 pages, hash routing `#/story` etc.), updated 2026-11-05 (commit `09d19968`).
- Enabled in `startup_extensions` 2026-11-05 (commit `7d05519c`).
- Tests: `tests/test_wiki.py` (backend success, fallback on failure, api_config defaults) — full suite passes.
- **Note:** the self-update watcher does NOT reload non-`.py` files — a new `cogs/wiki.html` alone won't trigger a wiki cog reload.

### The backend (Aboard the Astra repo, NOT Astrabot)

- Lives in the **Aboard the Astra Remake repo** (`egamI-rorriM/Aboard-the-Astra---Remake`, branch `main`; it was developed on `feature/wiki-mechanics-site`, which was merged and deleted on GitHub), under `Tools/wiki/backend/` — `server.py` (stdlib-only `http.server`), `generator.py`, `README.md`, `data/mechanics.snapshot.json`.
- **Final model (2026-11-05, commit `d8f0ba0b`): NO login.** The backend regenerates the wiki HTML from the repo; the API is **Astrabot-only** via shared-secret header `X-Astra-Wiki-Key: <WIKI_API_KEY>` (same value on bot host and backend host, never committed). No key → 401; **no key configured → 503 fail-closed**. Default bind `127.0.0.1` (loopback; both run on 192.168.4.80), port 13382. `/health` is public. Webhook keeps its own GitHub HMAC check. **The public `api.theruvial.com` proxy must NOT expose the API** — the repo copy of its nginx config was removed and untracked 2026-11-05, and the corrected full server nginx config (with both api blocks removed) is `~/workspace/nginx-theruvial-full.conf`.
- **Superseded — do not revive:** the login build (was accidentally pushed in an earlier commit, then removed), the GitHub Action auto-update (workflow file exists only locally at `~/workspace/astra-wiki-staging/.github/workflows/wiki.yml`; org-level workflow permissions are greyed out), public GitHub Pages hosting, and the live-backend delivery route.
- **Host setup still pending (as of 2026-11-05):** generate the secret on the host (`python -c "import secrets; print(secrets.token_hex(32))"` — never paste it in chat, never commit it); set `WIKI_API_KEY` on both hosts + `GITHUB_TOKEN` for the backend (read-only token for the private Astra repo — classic `repo` scope only, or fine-grained Contents: Read-only); run `python server.py` from `Tools/wiki/backend/` in a checkout on `feature/wiki-mechanics-site`; verify `curl http://127.0.0.1:13382/health` → 200 and `curl -H "X-Astra-Wiki-Key: <key>" http://127.0.0.1:13382/api/status` → 200 with `live: true`; pull Astrabot `master` + restart the bot.
- **Wiki facts (for context, not Astrabot code):** the wiki lists game mechanics for the Aboard the Astra remake; boundary set by Thea — never change the game itself, only the wiki. 59 encounters ready / 26 not-ready / 85 rows (verified from `Encounter.csv` + `Encounter_Choice.csv`); 18 wiki pages; dark mode default. The wiki branch is **unmerged, no PR opened** (merging to `main` is Justin's call).

---

## 5a. Tasks cog (`cogs/tasks.py`)

Full behaviour is in `docs/README_tasks.md`, and the classifier is documented in `README_classifier.md` in the public `theruvial/AstraThought` repo (its data files and model are not in git and stay on Thea's machine).

| Role | Channel (mcdungeons) | ID |
|---|---|---|
| Reads messages from | #astra-feedback | `1568142915631325355` |
| Decisions needing a person | #human-intervention | `1568329972928610546` |
| Confirmed tasks | #todo | `1568332728573759621` |
| Tasks claimed by a person | #todo-steve, #todo-justin, #todo-thea, #todo-kathi | `1569493103033843822`, `1569493185946853517`, `1569493220151394517`, `1569493240896290816` |

- **Flow:** each human message in #astra-feedback goes to the classifier on `127.0.0.1:13383`, which splits it and returns `auto_task`, `review` or `auto_drop` per piece. Tasks become wiki tasks through `/api/tasks` and post to #todo with Claim / Unclaim, In Progress, Done and Not a Task. Unclear pieces post to #human-intervention with a thread. Dropped pieces post nothing.
- **Rulings:** "Is a task" creates the wiki task, posts it to #todo, and deletes the review message and thread. If #todo cannot be reached, it edits the review message in place instead. "Not a task" deletes the review message and thread. Every ruling is saved as a label and takes effect immediately through the overrides file.
- **Personal channels:** claiming a task re-posts its embed in the claimer's channel (`PERSONAL_CHANNELS`, Discord user id to channel id) and deletes the one in #todo; the first claimer decides with several claimers, and any unclaim sends it back to #todo (even if someone else is still assigned; the next claim sends it to the first claimer's channel). State records carry `claimers` (user ids, in order) and `channel_id`. Claimers with no channel leave the message in place.
- **Done and the live list:** Done (button or `/task_done`) deletes the task's #todo message; the wiki keeps the record and the #astra-feedback source message is untouched. The cog also writes `data/TODO.md` (`TASKS_TODO_MARKDOWN_PATH`), a markdown list of every non-done wiki task with assignees and message links, whenever a task changes and every 5 minutes. The same sync deletes #todo messages of tasks the wiki shows as done.
- **Host needs:** `"cogs.tasks",` uncommented in the host `bot.py` only, `WIKI_API_KEY` set, and Manage Threads for the bot in #human-intervention so it can delete threads.
- **Model:** SetFit `tasks-AstraThought-v2`, 77% precision and 93% recall on the held-out test set. Retrain only after 50 new labels. Thea trains it herself.

---

## 6. Self-update watcher (`cogs/selfupdate.py`)

- Added 2026-11-01 (commit `5e80a23e2e7a248ad0fabd5865612a0e39447dfc`); **enabled 2026-11-05** (commit `48b0a58c`, `"cogs.selfupdate"` added to `startup_extensions`). Host still needs pull + restart to run it.
- Every **15 minutes**: `git fetch` on the host checkout's current branch, diff, map changed paths to loaded extensions (an extension's own `.py` plus `cogs/<name>_*.py` helpers only); a dirty local edit to an upstream-changed file → skip (`conflict_skip`); otherwise `git pull --ff-only`, then `bot.reload_extension()` per affected extension.
- `bot.py` / `config.json` **never** trigger it. Also admin-only **`/check_updates`** runs the same check manually.
- Tested: 30 tests passed at build.
- **Webhook vs polling — still awaiting Thea's choice (reopened 2026-11-01):** options are (a) Cloudflare Tunnel, (b) Discord relay, (c) keep the 15-minute poll. **Do not build any webhook version until she picks.**

---

## 7. absLogin (separate repo, related service)

- **Repo:** `theruvial/absLogin` (private, `main`). Local repo `~/workspace/absLogin/`; host clone at `G:\My Drive\codingShit\absLogin`.
- **What it is:** signup backend for Thea's existing Audiobookshelf server, open to anyone; new users get access to all libraries. `app.py` (stdlib-only), `signup.html`, `change-password.html`, `README.md`, tests.
- **Behaviour:** creates users as `type: "user"`, download on, update/delete/upload off, explicit content off. New user's ABS token is **never returned to the browser**.
- **Deployment:** runs on the Discord-bot host `192.168.4.80:13381` (not the Audiobookshelf host); talks to ABS via `https://audio.theruvial.com`; nginx `/signup/` proxies to `http://192.168.4.80:13381/`. Live at `https://audio.theruvial.com/signup`.
- **Auth:** env `ABS_API_KEY`, must belong to an admin/root ABS account.
- **Details:** signup usernames forced lowercase (password-change lookups are NOT lowered); password-change page at `https://audio.theruvial.com/signup/change-password` verifies the current password via ABS login and blocks guest accounts. After signup, the page gives iOS/Android/Web instructions (Web via `audio.theruvial.com/login`, Android via the official app, iOS via AudioBooth — the official iOS app is closed-beta/TestFlight).
- `startBot.bat` starts absLogin in its own window and the console runs it as a child process; `ABS_API_KEY` must be set via `setx`, never in a committed file. The self-update watcher is Astrabot-only — it does **not** pull absLogin; `startBot.bat` pulls it separately.
- **Unconfirmed follow-through:** whether she applied the corrected nginx config / reloaded, backend restart to pick up latest `main`, end-to-end test, deleting the `testuser123` test account, and rotating the exposed API key.

---

## 8. Cogs inventory

| Cog | Status | Notes |
|---|---|---|
| `cogs.audiobooks` | Loaded | Main feature (§4). Only slash command: `/email_user` |
| `cogs.moderation` | Loaded | `/kick`, `/streamlive`, `/streamended`, `/speak`, `/reloadcog`, `/purge` |
| `cogs.wiki` | Loaded (2026-11-05) | `/wiki` (§5) |
| `cogs.selfupdate` | Loaded (2026-11-05) | `/check_updates` (§6) |
| `cogs.portfolio` | Loaded | Serves theruvial.com on port 13384 from the CV in the `theruvial/theruvial` repo (`cv/Thea_Arcadia_CV_full.md`). Polls every 10 minutes, checks the safety rules in `cogs/portfolio_cv.py`, DMs the owner (`cogs/owner.py`) an Approve/Reject preview, and rebuilds the page from the approved CV on every start. Layout (hero, project cards, skill chips, timeline) is `cogs/portfolio_render.py` plus the template `cogs/portfolio_page.html`; CV text is always escaped first. Live files are untracked: `data/portfolio_*`. Config keys are in the cog's docstring |
| `cogs.music` | Removed from `startup_extensions` | Streaming music/clip code exists in repo history; host `bot.py` has clip commented out |
| `cogs.clip` | Commented out on host | See host divergence (§2) |
| `cogs.tasks` | Loaded on the host only (local `bot.py` line) | Reads #astra-feedback, posts to #todo and #human-intervention (§5a) |
| `cogs.roles`, `points`, `gambling`, `shop`, `pet` | Commented out in repo `bot.py` | Legacy 2021 cogs, not loaded |

---

## 9. Repo workflow & tooling notes (for the agent doing the work)

- **GitHub pushes from Claude use the snapshot-push flow:** staging dirs are not git repos; pushes go via `/opt/hatch/bin/github push-files` with a NUL-delimited manifest outside the source dir; each confirmed push is appended to the adjacent `.muse-github-snapshot-sync.json`. Reference: `/opt/hatch/connectors/github/references/snapshot-push.md`.
- `push-files` **can batch multiple files in one call/commit but cannot delete files**; `delete_file` deletes exactly one file per commit.
- The GitHub App **cannot create repositories** (403) and **cannot write `.github/workflows/*`** (403).
- `get_file_contents` returns file content in `result.content[1].resource.text`; directory listings come as a JSON string in `content[0].text`.
- `search_code` searches **all of GitHub**, not one repo — do not use bare terms for repo-scoped queries.
- **Tests:** run with the venv Python (`~/workspace/astrabot-venv/`), not system Python 3.12 (no `discord` module); on the Windows host use `py -3.14 -m unittest discover -s tests`. Suites: `tests/test_audiobooks.py`, `tests/test_selfupdate.py`, `tests/test_guild_commands.py`, `tests/test_wiki.py`, `tests/test_astra_console.py`. Modules importing `config` need a `config.json` in the working directory (copy `config.example.json` in a fresh worktree; it is gitignored, do not commit it).
- **Known failing on Windows, also on clean `0c69c5f`:** `test_wiki_file_is_the_standalone_file` (reads `cogs/wiki.html` without `encoding="utf-8"`) and `test_cog_change_pulls_and_reloads_only_that_cog` (status `no_cog_changes`, cause not investigated).
- **Commit message trailer** used on the Astra repo (not necessarily Astrabot): `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- **Thea's preferences that apply here:** military time in all responses; implementation instructions presented as PowerShell commands in code blocks (with a copy button); future projects default to dark mode for their web interface.
- **Never store, echo, or commit credentials** she pastes (bot tokens, API keys). A bot token in a fetched Astrabot `config.json`, an ABS API key, and a new bot token visible in a Developer Portal screenshot have all been seen and **none of their values is recorded anywhere** — do not repeat them.
- **DM AI agent idea (NOT built, do not build until she defines scope):** a DM-only AI agent as an Astrabot cog, model Muse Wonder (`muse-wonder-1.0` via the Meta Model API, pay-per-token, separate from Muse app tokens — not Wonder Full, which has no API route), plus a local LLM option as an alternative provider, switchable in config. No task scope decided; API key status unknown.

---

## 10. Server / nginx topology (for context)

Nginx host: Ubuntu, `thea-ubuntu-server`, all sites in `/etc/nginx/sites-available/default` (single file). Sites: **audio.theruvial.com** → Audiobookshelf `192.168.4.74:13378`, `/signup/` → absLogin `192.168.4.80:13381`; **requests.theruvial.com** → Astrabot web UI `192.168.4.80:13380`; **media.theruvial.com** → Jellyfin `192.168.4.74:8096`; **vaultwarden.theruvial.com** → VaultWarden `192.168.4.74:1337`; **api.theruvial.com** → was the Astra wiki backend `192.168.4.80:13382` — **now to stay disabled for the API** per the loopback backend model (corrected config with both api blocks removed: `~/workspace/nginx-theruvial-full.conf`; her pre-change original: `~/workspace/user/files/default.bin`).

**Nginx + Certbot lesson (learned the hard way 2026-11-05):** NEVER write a `:443` server block referencing `/etc/letsencrypt/live/<domain>/fullchain.pem` before the cert exists — `nginx -t` fails ("cannot load certificate: No such file") and `certbot --nginx` fails with it (chicken-and-egg). Correct order every time: add ONLY the `:80` block first, `nginx -t` + reload, then `certbot --nginx -d <domain>` and let certbot create the `:443` block itself, then test + reload again.

---

## 11. Open items (as of 2026-11-05)

1. **Bot host catch-up — status unconfirmed:** host must `git checkout master` + pull, restore `config.json` from `~/workspace/your_files/astrabot-config.json` + paste new token, enable the three privileged intents in the Developer Portal, then restart. That one restart/pull brings in: merged `master`, per-guild command filtering, `cogs.wiki` + `cogs.selfupdate` enabled, startBot.bat crash tracking/Tee/taskkill changes.
2. **Wiki backend host setup — not done** (§5).
3. **Webhook vs polling — awaiting her choice** (§6).
4. **Audiobook live-test set unconfirmed** (§4); `admin_user_ids` in the barebones template is still `[]` (host's real config had `[151408413744693248]`); topbar button choice (both buttons added) not settled.
5. **DM AI agent cog — awaiting scope + API key; do not build.**
6. **absLogin follow-through unconfirmed** (§7).
7. Token rotation (bot token in git history; ABS API key exposed in debugging) — unconfirmed in chat.
8. **Console not live yet:** close the old bot and any hand-started wiki `server.py`, then run `startConsole.bat`. Not yet tested against the real bot, real site or real wiki backend (§12).
9. **Wiki env vars for the console:** `WIKI_API_KEY` and `GITHUB_TOKEN` must be persistent (`setx`), or the console's wiki backend fails closed with 503. Host state unconfirmed.

---

## 12. Change log

Newest first. One dated entry per working day, by the agent that did the work.

### 2026-11-11 — Claude Code (Sonnet 5.5), portfolio redesign

- **Redesigned theruvial.com** so it reads as a portfolio: a hero (monogram, role, location, summary, contact buttons), project cards in a grid with tech chips, dates and link buttons, skills as chips, and a timeline for experience and education. Projects come first on the page whatever order the CV uses. AI-assisted groups and chips are tinted differently from hand-written work so the honesty rules stay visible.
- **New module `cogs/portfolio_render.py`** reads the CV's shape (`##` sections, `###` groups, `**Title** — details` entries) and lays it out; all text still goes through `portfolio_cv.inline` (escape first). If the layout code ever fails, the page falls back to the old plain layout. The cog now calls `render_portfolio`; `render_cv` stays as the fallback.
- **Template rewritten** (`cogs/portfolio_page.html`): sticky blurred nav, animated gradient background, scroll-reveal, mobile and reduced-motion handling, new footer wording. The page works without JavaScript.
- **Tests:** `tests/test_portfolio_render.py` (new) plus the full suite, 252 tests passing. A local preview built from the real CV was checked in the browser pane. **Not tested:** the live site after deploy and phone screens on real devices. The change goes live when the host pulls it and the portfolio cog reloads.

### 2026-11-11 — Claude Code (Opus 5.5), history rewrite

- **Rewrote the whole git history** with `git filter-repo` before making the repo public. Removed from every commit: `config.json`, `console.json`, `trelloConfig.json`, `taskConfig.json`, all `.db`, `.csv`, `.pyc` and notebook files, and `nohup.out`. Scrubbed any leftover copies of the old tokens, webhook URLs and API keys, and set all of Thea's older commits to `Thea Arcadia <theruvial237@gmail.com>`.
- **Every commit ID changed.** Commit IDs in this file and the 2026-11-10 hand-off now point at the new commits. IDs from other repos or local stashes are unchanged. Any old clone must be deleted and cloned fresh; never push from an old clone.
- **The current files are unchanged.** The Discord bot token that was in history must be reset in the Discord Developer Portal; the rewrite hides it but does not revoke it.
- **Thea's local copy now follows the public history** (`master` at `b4a52a1`). Her databases, `config.json`, `console.json`, `logs/` and `data/` are untracked and were not touched. The 10 old worktrees, all old branches, the backup tag and 9 old stashes were deleted (the stashes were lost by mistake and Thea said they were not needed), and the old objects pruned.
- **AstraThought got the same treatment:** its history was also rewritten, the repo made public, and `*.jsonl` gitignored, so training and eval data stay local (with a local backup copy). The model folder `tasks-AstraThought-v2` is gitignored too. The local clone was repointed with a mixed reset so the data files survived; a hard reset would have deleted them. Old history is in the private `AstraThought-old`.
- **New held-out test set** `eval_stress.jsonl` in AstraThought: 100 AI-written rows, all reviewed by Thea. Final scores at the 0.5 line: real #astra-feedback set 98% precision and 92% recall (60 pieces), stress set 94% and 98%.

### 2026-11-10 — Claude Code (Sonnet 5.5), branches `console/watchdog` and `master`

- **Dragonwilds server (host only, not in the repo) and hand-off:** an anonymous SteamCMD copy of the RuneScape: Dragonwilds dedicated server was installed at `C:\Servers\Dragonwilds` with Thea's world and `.ini` copied from the Steam library install; a console service for it exists in the untracked `console.json`. Build choice (newer 26419371 vs old 26161880) is open. Full day summary, machine state and open items: `docs/HANDOFF-2026-11-10.md`.
- **Golurk Console (branch `console/golurk`):** renamed from Astrabot Console; the five built-in windows are grouped under one collapsible "Astrabot" header and each is collapsible; Settings adds removable services (adapted from the Service Console zip: run as program/PowerShell/batch, env, working folder, stop commands, start at login), used for the Dragonwilds server. A run-as-another-Windows-user option was built and then removed the same day (Thea wanted a Steam anonymous login instead, not a Windows guest account). `tests/test_golurk_console.py` added. **Not tested:** the Dragonwilds server itself.
- **Repo cleanup:** `AUDIOBOOKS.md` and `README_tasks.md` moved to `docs/` (links updated); removed the tracked root `__pycache__/`, the empty `nohup.out` and the unused `taskConfig.py` / `taskConfig.json`. Cogs, `bot.py`, `config.py` and `astra_console.py` stay where they are on purpose: moving them changes import paths, the launchers and the self-update watcher. `Saved/.keepfolder.txt` and `startBot.sh` were left alone.

- **Decisions box fixed, watchdog default on (later the same day):** see section 5a. `/todo` (mcdungeons only) serves the live `TODO.md` privately to the requester.

- **Console Watchdog and port clearing:** overnight the console's watcher threads for the classifier and wiki died (logs on the Drive stopped between 00:02 and 01:41; cause not proven), so "Restart Classifier" said "was not found at startup" while the services kept running with nothing watching them. `Supervisor.is_lost()` now reports a started service whose thread is dead, and a **Watchdog** button (off by default, or start with `--watchdog`) checks every 15 seconds after a 2 minute grace period; two bad checks in a row stop everything it can find, free the ports, close the console and open a new one with the watchdog on. Every service start (buttons, crash restarts, first start) first stops anything listening on its port: 13380 bot site, 13381 absLogin, 13382 wiki, 13383 classifier. A bot started with a custom command (the tests) does not clear 13380. The thread dying is still unfixed: a failed log write on the Drive is the leading guess.
- **`cogs.tasks` is now loaded in `bot.py` on master** (it had been an uncommitted line on the host).
- **AstraThought (separate repo, main):** the classifier now trains on `train.jsonl` plus Muse's 1,000 invented rows (`synthetic/synthetic_all.jsonl`): 46 of 50 real #astra-feedback tasks found, 1 false alarm. Requirement statements ("Modules should have 2 to 3 hp") now count as tasks for the regex check. The splitter splits "X and fix Y", also/then, statement pairs and one-line numbered lists. Press Restart Classifier to load the new model and rules. Details are in AstraThought's `README_classifier.md`.

### 2026-11-09 — Claude Code (Sonnet 5.5), branch `docs/wiki-branch-note`

- **Wiki checkout moved to `main`:** Pull + Restart was failing on the wiki step with "no such ref was fetched", because `feature/wiki-mechanics-site` had been merged and deleted on GitHub. `C:\codingShit\Aboard-the-Astra-Wiki` was switched to `main` and fast-forwarded (it had no commits `main` lacked, and `Tools/wiki` was unchanged). The old local branch was kept. If a console pull ever says "no such ref was fetched" for a repo, that repo's branch was deleted upstream: check `git ls-remote --heads origin` and switch to a branch that exists.
- Corrected the wiki repo owner and branch in the wiki backend notes above.

### 2026-11-09 — Claude Code (Opus 5.5), branch `feature/audiobook-titles`

- **Titles and authors:** Audible links are looked up on Audnexus, and requests store `author` next to `bookName`. The migration backs the database up first. `scripts/backfill_audiobook_titles.py` fixes old rows. Emails and embeds say "Title by Author"; the admin request row has an Author box.
- **Fulfil deletes the post:** Request Fulfilled (Discord or dashboard) deletes the Discord message, and restarts no longer re-post Fulfilled requests.
- Merged 2026-11-09. After the restart, run `py -3.14 scriptsackfill_audiobook_titles.py --apply` once to rename the four existing requests.

### 2026-11-09 — Claude Code (Sonnet 5.5), branch `feature/torrent-matching`

- **Transmission matching (off by default):** `cogs/audiobooks_torrents.py` reads torrents from Transmission's RPC (the desktop app on the Ubuntu server, port 9092, basic auth) and marks matching `Requested` audiobook requests In Progress via `admin_fulfill`. `audiobooks.torrents` in `config.json` holds `enabled`, `dry_run` (default true), `url`, `username`, `password`, `interval_seconds`; the credentials stay in the untracked `config.json`. `scripts/check_torrents.py` previews matches read-only.
- **Server notes:** Transmission is not in Docker. A `transmission-daemon` service was also running and held port 9091, so the desktop app's API was moved to 9092 and the daemon was stopped. The app's API only exists while the app is running.

### 2026-11-09 — Claude Code (Sonnet 5.5), branch `fix/review-thread-type`

- **Review threads work again:** `Message.create_thread` takes no `type` option (only `TextChannel.create_thread` does), so every "Needs review" post in #human-intervention was logging `Could not create review thread` and going up without its thread. The `type` argument was removed. The test fake now has the real signature, and a test binds the call to the installed discord.py's signature, so this cannot drift again.

### 2026-11-09 — Claude Code (Sonnet 5.5), branch `feature/personal-task-channels`

- **Personal task channels:** the four channels under Task Tracking were renamed #todo-steve, #todo-justin, #todo-thea and #todo-kathi (ids unchanged). Claiming a task moves its embed to the claimer's channel; Done deletes it there. Mapping: theruvial to thea, egami_rorrim to justin, _metronome to steve, kathi.exe to kathi. Other members (e.g. twocanpeacan, Peachblight) have no channel yet.
- Embeds rebuilt on Claim and In Progress now keep their Source field and footer (they used to lose them).

### 2026-11-09 — Claude Code (Sonnet 5.5), branch `feature/tasks-cleanup-and-markdown`

- **Done deletes the task message:** marking a task done in #todo (button or `/task_done`) deletes its message instead of greying it out. If the delete fails, it falls back to the old greyed embed.
- **Live `data/TODO.md`:** the tasks cog keeps a markdown list of open and in-progress tasks, rewritten on every task change and every 5 minutes, and only when the list changed. The 5-minute sync also deletes #todo messages for tasks done on the wiki.
- State records now remember the message's channel (`channel_id`), so links and deletes work for the rare task that stayed in #human-intervention.

### 2026-11-06 — Claude Code (Opus 5.5), task channel split

- **Changed task channels:** the cog now reads #astra-feedback, sends review items to #human-intervention, and posts tasks to #todo. It no longer reads #general.
- **"Is a task" rulings** now move the item to #todo and keep its source link, instead of editing the review post in place.
- **Docs:** rewrote `README.md`, added a channels section to `README_tasks.md`, and added §5a here.

### 2026-11-05 — Claude Code (Opus 5.5), branch `claude/astrabot-startbot-crash-logs-4b6db3`

- **Fixed missing bot output:** piped Python buffered `print()` in memory. Also fixed the cp1252 emoji crash, UTF-16 `bot.log`, and `NativeCommandError` noise. `cd` now uses the bat's own folder (`C:\Coding\Astrabot` does not exist on the host).
- **Removed** the startup process kill; made startup `git pull`s quiet.
- **Added** the Astrabot Console and `startConsole.bat` (§2), including the wiki backend.
- **Added** INFO logging in `bot.py` and the reload-flag watcher in `cogs/moderation.py`.
- **Replaced** the per-guild command filter with cog-declared scope; `/email_user` is private-guild only (§3). `/reloadcog` no longer copies global commands into the guild.
- **Added this document to the repo** as `DESIGN.md`, with `CLAUDE.md` pointing agents to it.
- **Restored `audiobooks.db`** (3 users, 3 sessions, 4 requests) from stash `7c01756` and stopped tracking both databases (§2).
- **Tested:** unit tests; the launcher and the real console window against fake bot and wiki processes. **Not tested:** live Discord sync, a real cog reload on the host, or the real wiki backend.
