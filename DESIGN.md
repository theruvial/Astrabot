# Astrabot — Design & Context Document

**Purpose:** context for a fresh Claude session (or any new agent) to work on Astrabot without re-learning the project from scratch. Everything below is as of **2026-11-05**. Where a fact is unverified or host-side, it is marked as such.

**Keeping this current (Thea's rule, 2026-11-05):** this file is how changes are tracked between AI agents. Update it each time an agent finishes work for the day, or whenever asked: correct anything that changed in place, and add a dated entry to the change log (§12). Never record credential values here.

**Owner:** Thea (GitHub: `theruvial`, Discord admin user ID `151408413744693248`, email `theruvial237@gmail.com`)
**Repo:** `theruvial/Astrabot` (private, Python, discord.py 2.x), default branch `master`
**Bot application ID (public):** `622544371098845221`
**Assistant note:** Thea's standing workflow rule (set 2026-11-05) — Muse/Claude no longer needs her input for every request, just keep working. Only ask first for merges, pull-request creation, or anything that could destroy/damage the main branch. If a change is one she would want to know about, tell her; otherwise proceed.

---

## 1. What Astrabot is

A personal Discord bot (discord.py 2.x) that started as a small 2021 bot and grew into a small platform:

- **Audiobook request system** — the main feature: a Discord + web workflow for requesting audiobooks for Thea's Audiobookshelf server, with admin fulfilment.
- **Moderation cog** — kick, purge, streaming notices, speak, reload.
- **Wiki cog** — serves the Aboard the Astra game-mechanics wiki as a Discord attachment.
- **Self-update watcher** — polls git and hot-reloads changed cogs.
- **absLogin integration** — a separate repo/service (see §7) started from the same launcher.

The bot runs on Thea's **Windows host at 192.168.4.80**. Local checkout: `G:\My Drive\codingShit\Astrabot`. Python is run as `py -3.14 bot.py` (plain `python` is a different install without discord.py — do not tell her to `pip install discord` into the wrong Python).

---

## 2. Host & deployment

- **Status of this section:** the console, launcher fixes and command-scope changes below were built 2026-11-05 on branch `claude/astrabot-startbot-crash-logs-4b6db3`. Until that branch is merged to `master` and pulled, the host still runs the old launcher.
- **Launchers (repo root; use one, never both, each starts its own bot):**
  - `startConsole.bat` pulls Astrabot and absLogin, starts absLogin in its own window, then opens the **Astrabot Console** (`astra_console.py`, tkinter, dark theme).
  - `startBot.bat` is the plain console launcher: pulls, starts absLogin, runs the bot.
- **Astrabot Console:** owns the bot and the wiki backend (`C:\codingShit\Aboard-the-Astra-Wiki\Tools\wiki\backend\server.py`) as child processes, with Bot, Site and Wiki output panes. Buttons: Restart Bot (also restarts the site, which lives in the bot), Restart Site (reloads `cogs.audiobooks`), Restart Wiki, Pull + Restart (fast-forward pulls all three repos, restarts bot and wiki only if every pull worked). Closing the window stops the bot, site and wiki. absLogin is pulled but not restarted by the console. Do not run a hand-started `server.py` alongside it: it would share port 13382.
- **Restart Site side effect:** a reload runs `cog_load`, so it clears and re-posts the requests channel (§4). Thea chose cog reload over a separate web-only restart on 2026-11-05.
- **How Restart Site reaches the bot:** the console writes the cog name to `logs\reload_cog.flag`; `cogs/moderation.py` polls it every 2 s and calls `reload_extension`. A file, not an HTTP route, because the site binds `0.0.0.0`. The watcher is in moderation because a watcher inside the reloaded cog would cancel itself.
- **Crash tracking (both launchers, console also covers the wiki):** output is appended to `logs\bot.log` (`logs\wiki.log` for the wiki), UTF-8. Every start/stop is timestamped with its exit code in `logs\crashes.log`. Auto-restarts after 10 s on a crash; **stops after 5 crashes in a row**; a clean exit (code 0) stops normally. In the console a run of 60 s or more resets the streak.
- **Piped Python gotcha:** when output is piped, Python block-buffers stdout and encodes as cp1252. Always run with `-u` and `PYTHONIOENCODING=utf-8`, or `print()` output never appears and emoji in guild names raise `UnicodeEncodeError`. PowerShell 5.1's `Tee-Object` also writes UTF-16 and wraps stderr in `NativeCommandError` noise, so it is no longer used.
- **Logging:** `bot.py` calls `discord.utils.setup_logging(level=logging.INFO)`, because `bot.start()` (unlike `bot.run()`) configures none and INFO lines were dropped. The console sends `audiobooks*` and `aiohttp*` loggers to the Site pane; `aiohttp.access` lines are request logs.
- **No process killing on startup (2026-11-05):** the kill step was removed at Thea's request. Running a launcher while a bot is up gives two bots, so stop the old one first. `git pull -q` keeps startup quiet but still shows errors.
- **Logs are untracked:** `logs/` and `crash.log` are in `.gitignore` — never commit them.
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

### Command scope (replaces the `0a913b57` per-guild filter, 2026-11-05)

- **Cogs declare scope.** Commands are global unless a cog limits them with `app_commands.guilds(...)`. Every audiobooks command (just `/email_user` today) is limited to Thea's private guild **`454792201633792001`**, read from `audiobooks.guild_id` in `config.json`. If that key is missing, `setup()` in `cogs/audiobooks.py` falls back to `DEFAULT_GUILD_ID`, the same guild, never to global.
- **Behaviour change Thea accepted by proposing this design:** the private guild now also sees the global commands (`/kick`, `/purge`, `/wiki`, etc.); it used to get only the audiobooks commands.
- **Syncing:** the audiobooks cog syncs its guild in `setup()`, not `cog_load` (discord.py runs `cog_load` before adding commands), so reloads stay in sync. Global commands cannot sync per cog, because a global sync replaces the whole list, so `Astrabot._sync_commands()` does one global sync on first `on_ready`, then syncs each guild to clear the per-guild copies the old filter registered.
- Tests: `tests/test_guild_commands.py`.

---

## 4. Audiobook request system (the main feature)

**Design doc:** `~/workspace/your_files/audiobook-requests-design.md` (Thea's local copy; repo copy is `AUDIOBOOKS.md`).
**Staging (local, not the repo):** `~/workspace/astrabot-audiobooks/`; test venv `~/workspace/astrabot-venv/` (discord.py 2.7.1 — run cog tests with this venv's Python; system Python 3.12 has no `discord` module).

### Files

`cogs/audiobooks.py`, `audiobooks_db.py`, `audiobooks_providers.py`, `audiobooks_web.py`, `audiobooks_notify.py`, `bot.py` (adds `cogs.audiobooks`), `AUDIOBOOKS.md`, tests in `tests/test_audiobooks.py`.

### How it works

- **Database:** sqlite3, file `audiobooks.db`. Users are keyed by **email** (see email migration below).
- **Discord flow:** requests are posted as embeds in a **dedicated requests channel** (`request_channel_id` 1565347062965477386). **On every bot start the cog clears that channel completely and re-posts one embed per DB row, oldest first.** Treat that channel as dedicated — nothing else belongs there.
- **Embed buttons:** `Fulfill Request` and `Reject Request`. After Fulfill, a `Request Fulfilled` button appears; it sets status Fulfilled and emails the requester: *"The requested book {book} has been added to the server"*.
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

### Custom email feature (added 2026-11-01, commit `8d817116`)

- Discord: admin-only **`/email_user`** — inputs `requester` (autocomplete "FirstName (email)", value is the email), `message`, optional `subject` (default **"A message from Thea"**). Body is sent **exactly as typed**, not tied to a request. A first name shared by several users is **refused as ambiguous**; non-admins get no autocomplete and cannot use the command.
- Also an Email section at `/admin/email`. `config.json` was NOT pushed with this change. Tested: 25 tests passed.

### History

- Originally (2026-10-29) design-only on branch `aiTestBranch`; Thea reversed the no-build instruction the same day and asked for a full build.
- Built on branch `audiobook-requests`, merged to `master` via **PR #9** (2026-11-05, merge commit `2c065464`; 29 commits, 30 files). That merge also brought in the discord.py 2.x port, the self-update watcher, `/email_user`, and the `/wiki` cog.
- Project was marked "completed for now" by Thea on 2026-10-29, then re-raised repeatedly — do not assume it is closed.
- **Unconfirmed live-test set:** admin login landing, Audible link and Amazon example link live, Fulfill → Request Fulfilled email on a fresh request.

---

## 5. Wiki cog (`/wiki`) and the Astra wiki backend

### The cog

- `cogs/wiki.py` — slash command `/wiki` sends the wiki as a Discord attachment named `astra-wiki.html`; ephemeral error if the file is missing.
- **Current behaviour (commit `10ad72d0`):** when `WIKI_API_KEY` env is set, the cog fetches the fresh standalone from the backend (`WIKI_API_URL` env, default `http://127.0.0.1:13382`, path `/wiki/astra-wiki.html`) via aiohttp with the `X-Astra-Wiki-Key` header (15 s timeout) and sends those bytes. On any fetch failure, or with no key set, it falls back to the bundled `cogs/wiki.html`.
- Bundled file: `cogs/wiki.html` — the split standalone (18 pages, hash routing `#/story` etc.), updated 2026-11-05 (commit `af4ad791`).
- Enabled in `startup_extensions` 2026-11-05 (commit `9031deff`).
- Tests: `tests/test_wiki.py` (backend success, fallback on failure, api_config defaults) — full suite passes.
- **Note:** the self-update watcher does NOT reload non-`.py` files — a new `cogs/wiki.html` alone won't trigger a wiki cog reload.

### The backend (Aboard the Astra repo, NOT Astrabot)

- Lives in the **Aboard the Astra Remake repo** (`SteveGinGames/Aboard-the-Astra---Remake`, branch `feature/wiki-mechanics-site`), under `Tools/wiki/backend/` — `server.py` (stdlib-only `http.server`), `generator.py`, `README.md`, `data/mechanics.snapshot.json`.
- **Final model (2026-11-05, commit `d8f0ba0b`): NO login.** The backend regenerates the wiki HTML from the repo; the API is **Astrabot-only** via shared-secret header `X-Astra-Wiki-Key: <WIKI_API_KEY>` (same value on bot host and backend host, never committed). No key → 401; **no key configured → 503 fail-closed**. Default bind `127.0.0.1` (loopback; both run on 192.168.4.80), port 13382. `/health` is public. Webhook keeps its own GitHub HMAC check. **The public `api.theruvial.com` proxy must NOT expose the API** — the repo copy of its nginx config was removed and untracked 2026-11-05, and the corrected full server nginx config (with both api blocks removed) is `~/workspace/nginx-theruvial-full.conf`.
- **Superseded — do not revive:** the login build (was accidentally pushed in an earlier commit, then removed), the GitHub Action auto-update (workflow file exists only locally at `~/workspace/astra-wiki-staging/.github/workflows/wiki.yml`; org-level workflow permissions are greyed out), public GitHub Pages hosting, and the live-backend delivery route.
- **Host setup still pending (as of 2026-11-05):** generate the secret on the host (`python -c "import secrets; print(secrets.token_hex(32))"` — never paste it in chat, never commit it); set `WIKI_API_KEY` on both hosts + `GITHUB_TOKEN` for the backend (read-only token for the private Astra repo — classic `repo` scope only, or fine-grained Contents: Read-only); run `python server.py` from `Tools/wiki/backend/` in a checkout on `feature/wiki-mechanics-site`; verify `curl http://127.0.0.1:13382/health` → 200 and `curl -H "X-Astra-Wiki-Key: <key>" http://127.0.0.1:13382/api/status` → 200 with `live: true`; pull Astrabot `master` + restart the bot.
- **Wiki facts (for context, not Astrabot code):** the wiki lists game mechanics for the Aboard the Astra remake; boundary set by Thea — never change the game itself, only the wiki. 59 encounters ready / 26 not-ready / 85 rows (verified from `Encounter.csv` + `Encounter_Choice.csv`); 18 wiki pages; dark mode default. The wiki branch is **unmerged, no PR opened** (merging to `main` is Justin's call).

---

## 6. Self-update watcher (`cogs/selfupdate.py`)

- Added 2026-11-01 (commit `7cd0d7d63fcdc4f386163f55e1ee446deb6f7b1f`); **enabled 2026-11-05** (commit `be44fbec`, `"cogs.selfupdate"` added to `startup_extensions`). Host still needs pull + restart to run it.
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
- `startBot.bat` also starts absLogin; `ABS_API_KEY` must be set via `setx`, never in the committed bat file. The self-update watcher is Astrabot-only — it does **not** pull absLogin; `startBot.bat` pulls it separately.
- **Unconfirmed follow-through:** whether she applied the corrected nginx config / reloaded, backend restart to pick up latest `main`, end-to-end test, deleting the `testuser123` test account, and rotating the exposed API key.

---

## 8. Cogs inventory

| Cog | Status | Notes |
|---|---|---|
| `cogs.audiobooks` | Loaded | Main feature (§4). Only slash command: `/email_user` |
| `cogs.moderation` | Loaded | `/kick`, `/streamlive`, `/streamended`, `/speak`, `/reloadcog`, `/purge` |
| `cogs.wiki` | Loaded (2026-11-05) | `/wiki` (§5) |
| `cogs.selfupdate` | Loaded (2026-11-05) | `/check_updates` (§6) |
| `cogs.music` | Removed from `startup_extensions` | Streaming music/clip code exists in repo history; host `bot.py` has clip commented out |
| `cogs.clip` | Commented out on host | See host divergence (§2) |
| `cogs.roles`, `points`, `gambling`, `shop`, `pet`, `tasks` | Commented out in repo `bot.py` | Legacy 2021 cogs, not loaded |

---

## 9. Repo workflow & tooling notes (for the agent doing the work)

- **GitHub pushes from Claude use the snapshot-push flow:** staging dirs are not git repos; pushes go via `/opt/hatch/bin/github push-files` with a NUL-delimited manifest outside the source dir; each confirmed push is appended to the adjacent `.muse-github-snapshot-sync.json`. Reference: `/opt/hatch/connectors/github/references/snapshot-push.md`.
- `push-files` **can batch multiple files in one call/commit but cannot delete files**; `delete_file` deletes exactly one file per commit.
- The GitHub App **cannot create repositories** (403) and **cannot write `.github/workflows/*`** (403).
- `get_file_contents` returns file content in `result.content[1].resource.text`; directory listings come as a JSON string in `content[0].text`.
- `search_code` searches **all of GitHub**, not one repo — do not use bare terms for repo-scoped queries.
- **Tests:** run with the venv Python (`~/workspace/astrabot-venv/`), not system Python 3.12 (no `discord` module); on the Windows host use `py -3.14 -m unittest discover -s tests`. Suites: `tests/test_audiobooks.py`, `tests/test_selfupdate.py`, `tests/test_guild_commands.py`, `tests/test_wiki.py`, `tests/test_astra_console.py`. Modules importing `config` need a `config.json` in the working directory (copy `config.example.json` in a fresh worktree; it is gitignored, do not commit it).
- **Known failing on Windows, also on clean `10ad72d`:** `test_wiki_file_is_the_standalone_file` (reads `cogs/wiki.html` without `encoding="utf-8"`) and `test_cog_change_pulls_and_reloads_only_that_cog` (status `no_cog_changes`, cause not investigated).
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
8. **Console branch not live:** merge `claude/astrabot-startbot-crash-logs-4b6db3`, pull on the host, close the old bot and any hand-started wiki `server.py`, then run `startConsole.bat`. Not yet tested against the real bot, real site or real wiki backend (§12).
9. **Wiki env vars for the console:** `WIKI_API_KEY` and `GITHUB_TOKEN` must be persistent (`setx`), or the console's wiki backend fails closed with 503. Host state unconfirmed.

---

## 12. Change log

Newest first. One dated entry per working day, by the agent that did the work.

### 2026-11-05 — Claude Code (Opus 5.5), branch `claude/astrabot-startbot-crash-logs-4b6db3`

- **Fixed missing bot output:** piped Python buffered `print()` in memory. Also fixed the cp1252 emoji crash, UTF-16 `bot.log`, and `NativeCommandError` noise. `cd` now uses the bat's own folder (`C:\Coding\Astrabot` does not exist on the host).
- **Removed** the startup process kill; made startup `git pull`s quiet.
- **Added** the Astrabot Console and `startConsole.bat` (§2), including the wiki backend.
- **Added** INFO logging in `bot.py` and the reload-flag watcher in `cogs/moderation.py`.
- **Replaced** the per-guild command filter with cog-declared scope; `/email_user` is private-guild only (§3). `/reloadcog` no longer copies global commands into the guild.
- **Added this document to the repo** as `DESIGN.md`, with `CLAUDE.md` pointing agents to it.
- **Tested:** unit tests; the launcher and the real console window against fake bot and wiki processes. **Not tested:** live Discord sync, a real cog reload on the host, or the real wiki backend.
