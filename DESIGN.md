# Astrabot — Design Document

**Read this whole file before touching the code.** It is the single shared memory of the project between Thea and every AI agent that works on it. If you are a model starting a session here, this file is where you pick up exactly where the last session left off.

Last full review of the code against this document: **2026-11-17** (Claude Code, Opus 5.5). Every file in the repo was read for that review; sections 1–13 describe the code as it is on `master` at that point. The change log (§14) is history and is not rewritten.

---

## 0. For agents: read me first

### You wrote most of this

Since 2026-10-29, nearly all new code in this repo was written by AI agents (first Muse, then Claude Code, Sonnet and Opus) at Thea's direction. That includes the audiobook system, the tasks cog rewrite, the wiki cog, the self-updater, the portfolio site, the Golurk Console, the launchers' crash handling, the startup-cog loader and every test in `tests/`. Treat that code as **your own earlier work**: you know why it is shaped the way it is because the reasons are written down here and in each module's docstring. Do not describe it to Thea as someone else's code, and do not "rediscover" decisions that are recorded below. The 2019–2021 code (legacy cogs in §7.9, the first `bot.py`, `config.py`) is Thea's hand-written work; `music.py` and `clip.py` started as hers and were later ported and extended by agents.

See §1.3 for a file-by-file authorship table.

### Rules that always apply

- **Workflow (Thea, 2026-11-05):** keep working without asking for every step. **Ask first only for merges, pushes, pull requests, or anything that could damage `master`**, the live databases, or host config. Mention any change Thea would want to know about.
- **Branches:** do work in a worktree on a feature branch (`git worktree add -b feature/x .claude/worktrees/x master`), fast-forward merge into `master` only after Thea says so, push only after Thea says so, then delete the branch and worktree.
- **Never record or repeat credentials** (bot token, API keys, SMTP password, invite code, cookies). Their *names* and *where they live* are fine; values never. The invite code and every secret are read from config or env, never hard-coded.
- **Never commit** `config.json`, `console.json`, `cookies.txt`, `*.db`, `logs/`, `data/`. All are git-ignored; keep it that way.
- **Keep this file current **after every change, finished or still in progress**, not at the end of the day. A change that is half done, on a branch, unmerged or abandoned still gets recorded, with its state (Thea, 2026-11-17).** Each time: fix anything that changed in place in §1–13, and add a dated entry at the top of §14 (date, agent and model, what changed, what was tested and what was not). The same rule is in `CLAUDE.md` and `AGENTS.md`.
- **Thea's preferences:** military time in replies; shell instructions as copyable code blocks (PowerShell on the host); web UIs dark by default; plain, direct wording.
- **Python on the host is `py -3.14`.** Plain `python` is a different install without discord.py. Never suggest `pip install` into the wrong one.
- **Repo files use CRLF line endings.** Preserve them when editing.

---

## 1. What Astrabot is

### 1.1 Overview

A personal Discord bot (discord.py 2.7, slash commands, SQLite) running since 2019 on Thea's Windows machine. It began as a points/roles bot for a friends' server and is now a small platform:

| Area | What it does | Section |
|---|---|---|
| Audiobook requests | Public website + Discord embeds for requesting books for Thea's Audiobookshelf server; watches Transmission and Audiobookshelf to move requests along automatically | §7.1 |
| Team tasks | Reads #astra-feedback in the mcdungeons guild, classifies messages with the local AstraThought model, manages the team to-do list on the wiki backend | §7.2 |
| Wiki | `/wiki` sends the Aboard the Astra mechanics wiki as one HTML file | §7.3 |
| Portfolio | Serves theruvial.com from Thea's CV, with owner approval of every CV change | §7.4 |
| Music and clips | `/play` queue from YouTube/YT Music/SoundCloud/Spotify; `/clip`, `/sclip`, `/cplay` for sound clips | §7.5, §7.6 |
| Moderation | `/kick`, `/purge`, `/speak`, `/reloadcog`, stream presence; also the console's reload/alert flag watcher | §7.7 |
| Self-update | Polls git every 15 min, pulls and hot-reloads only changed cogs | §7.8 |
| Home Assistant | Allowlisted users talk to the house in Discord through the local `ha_agent` (Qwen on Ollama); risky actions need an Allow button | §7.10 |
| Golurk Console | Tkinter control panel that runs and supervises the bot and its sibling services | §8 |
| Legacy cogs | 2019–2021 economy, roles, shop, gambling, pets, houses; in the repo, off by default | §7.9 |

### 1.2 Architecture at a glance

```
startConsole.bat ──> astra_console.py (Golurk Console, tkinter)
                       ├─ bot.py  (py -3.14 -u)            Discord gateway
                       │    ├─ cogs.audiobooks ── aiohttp web UI :13380 (requests.theruvial.com)
                       │    │     ├─ TorrentWatcher  → Transmission RPC 192.168.4.74:9092
                       │    │     ├─ LibraryWatcher  → Audiobookshelf audio.theruvial.com
                       │    │     └─ intake :13385   ← Automated Librarian on 192.168.4.74 (private, secret)
                       │    ├─ cogs.portfolio  ── aiohttp :13384 (theruvial.com), polls GitHub CV
                       │    ├─ cogs.tasks      → classifier :13383, wiki backend :13382
                       │    ├─ cogs.wiki       → wiki backend :13382
                       │    ├─ cogs.music / cogs.clip → yt-dlp + FFmpeg → voice
                       │    ├─ cogs.moderation (polls logs/*.flag every 2 s)
                       │    ├─ cogs.selfupdate (git fetch/pull every 15 min)
                       │    └─ cogs.homeassistant → ha_agent → Ollama 127.0.0.1:11434 + Home Assistant 192.168.4.229 (port 80)
                       ├─ absLogin app.py                 :13381 (audio.theruvial.com/signup)
                       ├─ wiki backend server.py          :13382 (Aboard the Astra repo, loopback)
                       ├─ AstraThought classifier         :13383 (separate repo + venv)
                       └─ extra services from console.json (e.g. Dragonwilds server)
nginx on 192.168.4.74 terminates TLS and proxies the public hostnames to 192.168.4.80.
```

Bot host: Windows 11, **192.168.4.80**, checkout `G:\My Drive\codingShit\Astrabot` (on Google Drive). Nginx/Audiobookshelf/Transmission host: Ubuntu `thea-ubuntu-server`, **192.168.4.74**.

### 1.3 Who wrote what

| Files | Origin |
|---|---|
| `config.py`, `cogs/roles.py`, `points.py`, `gambling.py`, `shop.py`, `pet.py`, `house.py`, `test.py`, `cogs/__init__.py` | Thea, 2019–2021, by hand (ported to slash commands by an agent in 2026) |
| `bot.py` | Thea 2019; agents since 2026 (discord.py 2 port, logging, command sync, config-driven cogs) |
| `cogs/music.py`, `cogs/clip.py` | Thea 2020; ported and largely rewritten by agents in 2026 (multi-provider search, playlists, progress logging, clip cutting, cookies) |
| `cogs/tasks.py` | Started as Thea's 2020 Trello tasks cog; fully rewritten by agents 2026-11 |
| Everything else in `cogs/` (audiobooks*, wiki, selfupdate, portfolio*, owner), `astra_console.py`, `console_*.py`, `startup_cogs.py`, `startConsole.bat`, `scripts/`, `tests/`, `docs/` | AI agents (Muse, Claude Code) at Thea's direction, 2026-10-29 onward |
| `startBot.bat` | Thea 2021; crash handling and logging by agents 2026-11-05 |

---

## 2. Repository map

```
bot.py                  Astrabot(commands.Bot): creates thi.db tables, loads cogs, syncs commands
config.py               config = json.load("config.json") at import (the only config loader)
startup_cogs.py         extensions_from_config(): "cogs" list in config.json → extension names
config.example.json     tracked template of config.json (no secrets)
console.example.json    tracked template of console.json
astra_console.py        Golurk Console (tkinter), supervisors, watchdog, decisions panel
console_services.py     pure helpers for console extra services and console.json
console_settings.py     the console's Settings window
console_terminal.py     embedded terminal for the console (ConPTY via pywinpty + pyte screen)
console_strata.py       Strata runtime start/stop/restart for the console (plan-guarded); SSH tunnel for a remote server
console_remote.py       watch-only remote feed of the console's panes (HTTP + WebSocket, docs/CONSOLE_REMOTE.md)
startConsole.bat        pulls repos, opens the console (the normal way to run everything)
startBot.bat            plain launcher: pulls, starts absLogin, runs bot with crash restarts
startBot.sh             empty (unused)
cogs/
  audiobooks.py         cog: Discord embeds/buttons, /email_user, starts web UI + watchers
  audiobooks_db.py      SQLite layer for audiobooks.db (users, sessions, requests, migrations)
  audiobooks_web.py     aiohttp site: user pages, admin dashboard, JSON API
  audiobooks_access.py  pure rules: home-network admin check, proxy trust, invite code
  audiobooks_providers.py  link validation for Audible/Amazon/Spotify/Apple/Google/Goodreads, series
  audiobooks_notify.py  SMTP email sending
  audiobooks_torrents.py  Transmission RPC client + strict request↔torrent matcher + watcher
  audiobooks_library.py Audiobookshelf client + matcher + watcher (closes In Progress requests)
  tasks.py              cog: classifier pipeline, review flow, #todo board, /task_* commands
  wiki.py / wiki.html   cog: /wiki, plus the bundled fallback wiki file
  portfolio.py          cog: theruvial.com server, CV watcher, owner approval DMs
  portfolio_cv.py       pure: CV Markdown → safe HTML, publish rules, change summary
  portfolio_render.py   pure: CV → hero/cards/chips/timeline layout
  portfolio_page.html   template for the rendered page; portfolio.html = bundled fallback page
  owner.py              who the bot DMs (team-owned app workaround)
  music.py              cog: /play and queue; shared yt-dlp/FFmpeg helpers used by clip.py
  clip.py               cog: /clip, /sclip, /cplay
  moderation.py         cog: moderation commands + console flag watcher
  selfupdate.py         cog: git poll + hot reload
  homeassistant.py      cog: Discord <-> the local Home Assistant agent (ha_agent package, separate repo)
  roles.py points.py gambling.py shop.py pet.py house.py test.py   legacy cogs (§7.9)
scripts/                one-off tools: backfill_audiobook_titles, check_torrents, check_library,
                        export_youtube_cookies
automated-librarian/      server-side companion that runs on the Ubuntu server, not the PC: files torrents into the
                        Audiobookshelf library (see §9); has its own tests/ and README.md
tests/                  unittest suites (353 tests on 2026-11-24; one known console-test failure, §11)
docs/                   AUDIOBOOKS.md, README_tasks.md, CONSOLE.md, YOUTUBE_COOKIES.md,
                        HANDOFF-2026-11-10.md
Saved/                  saved clips for /sclip and /cplay (only .keepfolder.txt is tracked)
```

Untracked on the host only: `config.json` (+ `.bak`), `console.json`, `cookies.txt`, `audiobooks.db` (+ dated `.bak` copies made by migrations), `thi.db`, `logs/`, `data/`, `backups/`.

---

## 3. Runtime: how the bot starts

1. `bot.py` imports `config` (reads `config.json` from the **current directory**; the launchers `cd` to the repo first).
2. `Astrabot.__init__`: prefix from `config["prefix"]`, `Intents.all()`, `heartbeat_timeout=150`.
3. `setup_hook`: opens `thi.db` (name from `config["db"]`, `.db` added if missing) as `bot.db` with `sqlite3.Row`, creates the legacy tables (`exp`, `bucks`, `jackpot`, `tasks`, `roles`, `fct_pet_hp`, `fct_pet_xp`, `fct_pet_stats`, `dim_pet`), then loads each cog from `extensions_from_config(config)`. A cog that fails to load is printed and skipped; the bot keeps starting.
4. First `on_ready`: `_sync_commands()` does one global `tree.sync()` and then a per-guild sync of every guild (clears stale per-guild copies from an old design). Presence: listening to "Metronome rant about girls".
5. Logging: `discord.utils.setup_logging(level=INFO)` in `__main__`, because `bot.start()` sets up none. Cogs use named loggers (`audiobooks`, `portfolio`, `selfupdate`, `tasks`, ...); `music.py` has its own `log()` that prints with timestamps.

**Which cogs load** is the `"cogs"` list in `config.json` (since 2026-11-16): short (`tasks`) or full (`cogs.tasks`) names, order kept, duplicates dropped, invalid names skipped with a warning. No key → `DEFAULT_COGS` in `startup_cogs.py` (moderation, tasks, clip, audiobooks, wiki, portfolio, selfupdate). Empty list → nothing. A list change needs a restart, or `/reloadcog` for a cog already loaded.

**Host's list on 2026-11-17:** moderation, tasks, clip, music, audiobooks, wiki, portfolio, selfupdate.

**Command scope:** commands are global unless a cog uses `app_commands.guilds(...)`. Audiobooks commands (`/email_user`) are limited to Thea's guild `454792201633792001` (`audiobooks.guild_id`, falling back to `DEFAULT_GUILD_ID`, never global); the cog syncs that guild in `setup()`. `/todo` answers only in mcdungeons (`TEAM_GUILD_ID 1407601802827792525`). Tests: `tests/test_guild_commands.py`.

**Intents:** `Intents.all()` means all three privileged intents must be on in the Developer Portal, or the bot exits with `PrivilegedIntentsRequired`. Message Content is genuinely needed by `cogs.tasks` (reads #astra-feedback) and the legacy points/shop listeners; Members by legacy roles. Tightening intents is a possible cleanup, not done.

**Invite:** scopes `bot applications.commands`; app id `622544371098845221`. Permissions needed by the loaded cogs: View Channel, Send Messages, Embed Links, Attach Files (`/wiki`, `/todo`, `/sclip`), Read Message History, Manage Messages (`/purge`, audiobooks channel resync, task message deletes), Manage Threads (review threads), Kick Members (`/kick`), Connect + Speak (music/clip).

---

## 4. Configuration reference

All settings are in the host's `config.json` (untracked; `config.example.json` shows the shape) or environment variables set with `setx`. Secrets never go in the repo, in docs, or in this file.

### 4.1 `config.json` keys

| Key | Used by | Notes |
|---|---|---|
| `prefix`, `description`, `token` | bot.py | token is the bot's secret |
| `db` | bot.py | legacy SQLite file, default `thi.db` |
| `cogs` | startup_cogs.py | list of cogs to load (§3) |
| `owner_id` | owner.py via moderation | who gets watchdog DMs; default Thea's id |
| `cookiesFile` | music/clip, export script | default `cookies.txt` (§7.5) |
| `spotifyClientId`, `spotifyClientSecret` | music | optional; Spotify links need them and the `spotipy` package |
| `maxClipSeconds` | clip | default 300 |
| `savedClipsChannel` | clip | channel id; falls back to a channel named `saved-clips` |
| `audiobooks` | audiobooks* | object: `guild_id`, `request_channel_id`, `web_host`, `web_port` (13380), `db_path`, `admin_user_ids`, `admin_email`, `invite_code`, `trusted_proxies`, `admin_networks`, `email` (SMTP), `torrents` {enabled, dry_run, url, username, password, interval_seconds}, `library` {enabled, dry_run, url, api_key, library_id, interval_seconds}, `intake` {enabled, dry_run, host, port (13385), secret, allowed_ips} |
| `portfolio` | portfolio | object: `web_host` (0.0.0.0), `web_port` (13384), `cv_watch`, `cv_repo`, `cv_path`, `cv_branch`, `cv_poll_minutes` (10), `cv_auto_publish` (false), `data_dir`, `owner_id` |
| `tasks` | tasks | optional object overriding classifier/wiki URLs and paths |
| `selfupdate` | selfupdate | object: `enabled`, `interval_minutes` |
| `homeassistant` | homeassistant | object: `allowed_user_ids` (default Thea), `channel_id`, `ha_token` (blank = env `HA_TOKEN`), `ha_url`, `ollama_url`, `llm_url` (Strata/OpenAI-style URL ending in `/v1`; passed only when set), `model`, `timeout_seconds` (180), `fake_ha` (dev mode), `fake_ha_path` (§7.10) |
| `roles`, `crewmate`, `updates`, `rolesMessageID`, `updatesEmojiID`, `welcomeChannel` | legacy roles | ids |
| `botShop`, `workChannel` | legacy shop | ids |
| `be_webhook`, `bl_webhook`, `gd_webhook` | legacy house | webhook URLs (secrets) |
| `savedClipsChannel`, `roles`... | | the zeroed keys in the example are legacy placeholders |

Known non-secret host values: guild `454792201633792001`, requests channel `1565347062965477386`, admin user `151408413744693248`, trusted proxy `192.168.4.74`, admin network `192.168.4.0/24`.

### 4.2 Environment variables

| Var | Used by |
|---|---|
| `WIKI_API_KEY`, `WIKI_API_URL` | wiki, tasks (shared secret header `X-Astra-Wiki-Key`; default URL `http://127.0.0.1:13382`) |
| `TASKS_CLASSIFIER_URL`, `TASKS_LABELS_PATH`, `TASKS_OVERRIDES_PATH`, `TASKS_STATE_PATH`, `TASKS_TODO_MARKDOWN_PATH`, `TASKS_RETRAIN_MARKER_PATH` | tasks (defaults under `data/`) |
| `ABS_API_KEY` | absLogin; audiobooks library watcher when `library.api_key` is blank |
| `AUDIOBOOKS_INTAKE_SECRET` | audiobooks librarian intake when `intake.secret` is blank (shared with the Automated Librarian on the Ubuntu server) |
| `EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT`, `EMAIL_SMTP_USER`, `EMAIL_SMTP_PASSWORD`, `EMAIL_FROM_ADDRESS` | audiobooks_notify (override `audiobooks.email`) |
| `AUDIOBOOKS_CHANNEL_ID`, `AUDIOBOOKS_WEB_HOST`, `AUDIOBOOKS_WEB_PORT`, `AUDIOBOOKS_DB_PATH`, `AUDIOBOOKS_ADMIN_IDS`, `AUDIOBOOKS_ADMIN_EMAIL` | audiobooks overrides |
| `PORTFOLIO_WEB_HOST`, `PORTFOLIO_WEB_PORT` | portfolio overrides |
| `SELFUPDATE_ENABLED`, `SELFUPDATE_INTERVAL_MINUTES` | selfupdate overrides |
| `HA_TOKEN` | homeassistant (Home Assistant long-lived token, made by Thea) when `homeassistant.ha_token` is blank; `HA_URL`, `OLLAMA_URL`, `HA_AGENT_MODEL` are read by the agent itself when the config keys are blank |
| `GITHUB_TOKEN` | wiki backend (not the bot) |
| `SIGNUP_CONFIG` | absLogin, path to its config.json |

---

## 5. Host, launchers and deployment

- **Run everything with `startConsole.bat`** (pulls Astrabot and absLogin, opens the Golurk Console, §8). `startBot.bat` is the older plain launcher (pulls, starts absLogin in its own window, runs the bot with crash restarts). Use one, never both: each starts its own bot.
- **Crash handling (both):** output appended to `logs\bot.log` (UTF-8); each start/stop with exit code in `logs\crashes.log`; restart after 10 s; stop after 5 crashes in a row; exit code 0 stops normally. The console also logs `logs\wiki.log`, `logs\absLogin.log`, `logs\classifier.log`.
- **Piped Python:** always `-u` and `PYTHONIOENCODING=utf-8`, or prints are buffered and emoji crash cp1252.
- **Going live:** a push to `master` reaches the host when the self-updater pulls it (cogs only, §7.8) or on "Pull + Restart" / a relaunch. `bot.py`, `startup_cogs.py`, `config.py` and launcher changes need a full restart. `config.json` changes need a restart (or a cog reload for cog-specific keys).
- The **Restart** buttons on the Site and Portfolio panes in the console write the cog name to `logs\reload_cog.flag`; `cogs/moderation.py` polls it every 2 s and calls `reload_extension`. Reloading audiobooks re-runs `cog_load`, which clears and re-posts the requests channel.
- **Watchdog alerts:** the console writes `logs\watchdog_alert.flag`; moderation DMs the owner its text.
- **Databases:** `audiobooks.db` (requests site) and `thi.db` (legacy) are untracked. Never switch branches in a way that would replace them; schema migrations back the file up first (`audiobooks.db.pre-*.bak`).
- **No process killing at launcher start** (Thea's choice). The console frees each service's port before starting it.
- **Host checkout is on `master`** and follows `origin/master`. The old `bot.py` divergence is gone: cog choice lives in `config.json`.

---

## 6. Data stores

| Store | Owner | Contents |
|---|---|---|
| `audiobooks.db` | audiobooks_db | `users(email PK, firstName)`, `sessions`, `requests(id, requester, bookName, author, linkToBook, status, discord message id, created...)`; legacy `*_phone_legacy` tables from the 2026-10-29 email migration |
| `thi.db` | bot.py, legacy cogs | append-only ledgers `exp`, `bucks`, `jackpot`; `roles` (emoji→role), legacy `tasks`, pet tables |
| `data/tasks_state.json`, `task_labels.jsonl`, `task_overrides.json`, `task_labels_last_retrain.json`, `TODO.md` | tasks | message↔task state, human rulings (training labels), live overrides, retrain marker, live to-do list |
| `data/portfolio_*` | portfolio | approved CV, pending CV, ETag, rendered page |
| `Saved/*.mp3` | clip | saved clips |
| `console.json` | console | window state, extra services |
| `logs/` | launchers, console | logs, flags, `watchdog_restarts.json` |

---

## 7. Cogs

### 7.1 Audiobooks (`cogs/audiobooks*.py`) — the main feature

Full user-facing spec: `docs/AUDIOBOOKS.md`.

**Request lifecycle:** `Requested` → (Fulfill, torrent match, or librarian intake) → `In Progress` → (Request Fulfilled, or library match) → `Fulfilled`. The library match also closes a row still `Requested` straight to `Fulfilled` (one email). Reject and cancel hard-delete the row. Every step emails the requester, except the watcher intake's move to In Progress (no email, by Thea's choice) (SMTP, `audiobooks_notify.py`; Gmail needs an app password). If email fails, the admin is told; it never pretends it was sent.

**Discord side (`audiobooks.py`):**
- Requests post as embeds in the dedicated channel `request_channel_id`. **On every cog load the channel is reconciled in place (since 2026-11-20; it used to be wiped and re-posted):** posts of open requests stay and are edited only if they differ from the row, missing ones are posted, everything else is deleted (`plan_resync`, `resync_channel`). Buttons survive restarts as persistent views routed by message ID. Nothing else belongs in that channel.
- Persistent views: `Fulfill Request` + `Reject Request`; after Fulfill, `Request Fulfilled` + `Reject`. Fulfilled deletes the post. Reject opens a modal with an optional reason (≤500 chars); the email uses the original wording verbatim ("Request Rejected for {book} Speak to Thea for more information") plus "\n\nReason: ..." if given.
- Admin = `admin_user_ids`, or guild owner, or `Bot.is_owner()`.
- `/email_user` (admin, private guild only): requester autocomplete "FirstName (email)", message sent exactly as typed, subject default "A message from Thea"; an ambiguous first name is refused.
- Emails and embeds say "Title by Author".

**Website (`audiobooks_web.py`, aiohttp on `0.0.0.0:13380`, public as requests.theruvial.com):**
- Pages `/` (login), `/create-account`, `/requests`, `/new-request`; admin `/admin` → `/admin/users`, `/admin/requests`, `/admin/email` with a 72px icon sidebar. Material dark theme (`#141218` background, `#1e1b24` cards, `#d0bcff` primary).
- JSON API: `/api/login`, `/api/logout`, `/api/me`, `/api/users` (create account), `/api/requests` (list/create), `/api/requests/{id}/cancel`, and `/api/admin/...` for users (create/edit/delete), requests (fulfill/fulfilled/reject/edit/delete) and email. Authorisation is server-side on every route; `isMine` is computed by the server.
- Website cancel = hard delete + delete Discord post + "Your audiobook request for {book} has been cancelled." email.
- Topbar also links to absLogin signup and the Audiobookshelf login.
- **Login is email-only with no password** for ordinary users: anyone who knows a user's email can sign in as them. Known weakness, not yet addressed.

**Public-site security (`audiobooks_access.py`, 2026-11-14):**
- Admin (the `admin_email` account) only works from `admin_networks` (empty = this machine only). Behind nginx the visitor IP is the last `X-Forwarded-For` entry (or `X-Real-IP`), trusted only when the peer is in `trusted_proxies`; proxy headers from anyone else, or a trusted proxy naming no visitor, count as "not home" (fails closed). Outside the home network the admin cannot sign in or be created, and an admin cookie is ignored.
- Creating an account needs `invite_code` from config (constant-time compare; empty/missing = sign-ups closed). Guess limits: 5/min per visitor, 40/10 min overall.
- nginx must send `proxy_set_header X-Forwarded-For $remote_addr;` for requests.theruvial.com. **Not yet verified behind the real nginx.**

**Link validation (`audiobooks_providers.py`):** accepts Audible, Amazon, Spotify, Apple Books, Google Books, Goodreads. Each book needs positive audiobook evidence (format label, narrator, or listening length). Audible links are not scraped: the ASIN is looked up on Audnexus for title/author, falling back to the URL slug. Amazon is scraped (must be `/dp/` or `/gp/product/` and show audiobook evidence; Amazon may block it). Goodreads series: capped at the "X primary works" count, collected editions ("Book 1-3") dropped, decimals kept; series are partial, never all-or-nothing.

**Automation (all off by default, `dry_run` true by default; on 2026-11-17 the host log showed the torrent and library watchers running with dry run off):**
- `audiobooks_torrents.py`: polls Transmission RPC (desktop app on the Ubuntu server, port 9092, basic auth) and marks matching `Requested` rows In Progress. Strict matcher: all title words, author surname, torrent added after the request; ambiguous matches are left for Thea. Words are split on camelCase (2026-11-20) so the watcher's author folders (`lynsaySands`, `dennisETaylor`), which Audiobookshelf shows as the author until its metadata match, still count; a title that is a bare Audible ID (`B0...` or ISBN-10-shaped like `0062977776`) never matches. Preview with `scripts/check_torrents.py`.
- `audiobooks_library.py`: polls Audiobookshelf's recent items and marks matching `Requested` or `In Progress` rows Fulfilled (via `admin_fulfilled(..., allow_requested=True)`; the dashboard button still requires In Progress), using the stricter `match_title_exact` (since 2026-11-20): the request's and the library's title must have the same words (brackets like "(Series, #1)" and "Book 3" ignored), and when both name an author the surname must agree. So "Twice" no longer closes on "Twice Bitten". A download finishing never closes a request; only ABS reporting the book does. Preview with `scripts/check_library.py`.
- `audiobooks_intake.py` (2026-11-20): private aiohttp endpoint on its own port (default 13385) for the Automated Librarian on the Ubuntu server, which files torrents into the library and knows each book's real title/author, including every book of a multi-book pack. `POST /intake/books` {torrent {hash, name}, books [{title, author, series}]}: each book matching exactly one `Requested` row (`match_title_exact`, no date check) moves it to In Progress **without an email** and stores `requests.torrentHash`; books nobody requested are ignored. Security: refuses any proxy header (nginx is on the watcher's server, so the address alone proves nothing), peer must be in `allowed_ips`, Bearer secret compared in constant time (`secret` or env `AUDIOBOOKS_INTAKE_SECRET`; no secret = does not start). Contract for the watcher side: `docs/LIBRARIAN_INTAKE.md`.

**DB (`audiobooks_db.py`):** emails stored lowercase; sessions last `SESSION_DAYS`; migrations: phone→email (2026-10-29, old tables kept as `*_phone_legacy`), `author` column (2026-11-09, backs up first), `torrentHash` column (2026-11-20, backs up first as `*.pre-torrenthash-*.bak`). `scripts/backfill_audiobook_titles.py` fixed old Audible-ID titles.

### 7.2 Tasks (`cogs/tasks.py`)

Full behaviour: `docs/README_tasks.md`. Classifier: `README_classifier.md` in the public `theruvial/AstraThought` repo (data and model stay local).

| Role | Channel (mcdungeons guild 1407601802827792525) | ID |
|---|---|---|
| Watched | #astra-feedback | `1568142915631325355` |
| Needs a person | #human-intervention | `1568329972928610546` |
| Task board | #todo | `1568332728573759621` |
| Personal boards | #todo-steve / -justin / -thea / -kathi | `1569493103033843822`, `1569493185946853517`, `1569493220151394517`, `1569493240896290816` |

- Each human message in #astra-feedback → classifier `127.0.0.1:13383` → split into pieces → `auto_task` / `review` / `auto_drop` each. The bot never loads the model (no PyTorch in the bot's Python).
- `auto_task` → wiki task via `/api/tasks` on the wiki backend, posted to #todo with Claim/Unclaim, In Progress, Done, Not a Task. `review` → #human-intervention with a thread and Is a task / Not a task. Every ruling is saved to `task_labels.jsonl` and applies immediately through the overrides file. Retrain after `RETRAIN_LABEL_COUNT` = 50 new labels (Thea trains it herself).
- Claiming moves the embed to the first claimer's personal channel (`PERSONAL_CHANNELS`: theruvial→thea, egami_rorrim→justin, _metronome→steve, kathi.exe→kathi); unclaim sends it back. Done deletes the message (fallback: grey it out).
- Every 5 minutes (`SYNC_MINUTES`) and on each change: rewrite `data/TODO.md` if changed, and delete #todo messages for tasks done on the wiki.
- Commands: `/task_add`, `/task_list`, `/task_start`, `/task_done`, `/task_edit`, `/task_delete`, `/todo` (mcdungeons only, ephemeral markdown file), `/task_stats`.
- Needs `WIKI_API_KEY`, the classifier running, and Manage Threads in #human-intervention.
- Model: SetFit `tasks-AstraThought-v2`; latest scores in the 2026-11-11 change-log entry. The service runs it on the CPU (since 2026-11-25, AstraThought branch `feat/classifier-on-cpu`; `ASTRATHOUGHT_DEVICE=cuda` puts it back on the GPU), leaving the GTX 1070 to the local LLMs. Training still uses the GPU.

### 7.3 Wiki (`cogs/wiki.py`)

`/wiki` sends `astra-wiki.html`. With `WIKI_API_KEY` set it fetches `/wiki/astra-wiki.html` from the backend (15 s timeout, header `X-Astra-Wiki-Key`); on any failure, or with no key, it sends the bundled `cogs/wiki.html`. The self-updater ignores non-`.py` files, so a new `wiki.html` alone does not reload the cog.

Backend (not in this repo): `Tools/wiki/backend/server.py` in `egamI-rorriM/Aboard-the-Astra---Remake` (`main`), host checkout `C:\codingShit\Aboard-the-Astra-Wiki`. Stdlib HTTP server on `127.0.0.1:13382`, no login; API needs the shared key (401 without, 503 if no key is configured); `/health` is public. It must never be exposed publicly (the old api.theruvial.com proxy stays disabled). Boundary from Thea: never change the game itself, only the wiki. Merging to the game repo's `main` is Justin's call.

### 7.4 Portfolio (`cogs/portfolio*.py`)

Serves theruvial.com from `0.0.0.0:13384`. Read-only page, no commands, no auth.
- Source of truth: `cv/Thea_Arcadia_CV_full.md` in the public `theruvial/theruvial` repo. Polled every 10 minutes with an unauthenticated GET + ETag.
- A changed CV must pass the publish rules in `portfolio_cv.py`; if not, the page stays as it was and the owner is DMed why. If it passes, the owner gets a DM summary of changed lines with Approve/Reject (`cv_auto_publish` skips this). Owner = `cogs/owner.py` (the app is team-owned, so `application_info().owner` is a placeholder that cannot receive DMs).
- Rendering: `portfolio_render.render_portfolio` builds nav, hero (monogram, role, contact buttons), project cards, skill chips, a timeline, and a closing contact panel inside `portfolio_page.html` (royal purple `#5b21b6`/lavender `#b48cff` and gold `#f4c85a`, works without JS, reduced-motion and print CSS). Projects come first. AI-assisted groups are tinted gold, hand-written ones purple; the footer says AI-built projects were "designed and orchestrated by Thea". All CV text is escaped first (`portfolio_cv.inline`); links limited to http/https/mailto. If rendering throws, it falls back to `render_cv`'s plain layout; before any approval it serves `portfolio.html`.
- The page is rebuilt from the approved CV on every start; live files are `data/portfolio_*`.

### 7.5 Music (`cogs/music.py`)

- `/play query [provider]`: URL or search. Providers: YouTube (default), YouTube Music, SoundCloud, Spotify. Search falls back across YouTube → YT Music → SoundCloud, trying up to 5 results each until one has a playable stream. Spotify track/album/playlist links are read with `spotipy` (needs `spotifyClientId/Secret`) and each track is searched on YouTube. Playlist links ask the requester (buttons) whether to queue the first video or the whole list.
- Per-guild queue of `Track`s; `/skip`, `/queue`, `/nowplaying`, `/volume` (0–200%), `/pause`, `/resume`, `/stop`, `/leave`. Playback is `FFmpegPCMAudio` with reconnect options.
- `TaskProgress` logs each command's stages with timings (`#N /play ...`).
- yt-dlp helpers shared with clip: `ytdl_extract_sync` tries without cookies first, then with `cookies.txt` if present. `get_js_runtimes()` uses Deno if installed, else Node (the host has Node only). FFmpeg from PATH or `imageio_ffmpeg`.
- **YouTube cookies:** `scripts/export_youtube_cookies.py` copies only youtube.com/google.com cookies from a signed-in browser (Firefox by default) to `cookiesFile`, refusing if no login cookie is present, then does a test lookup. The file is read fresh on each lookup, so refreshing it needs no restart. Guide: `docs/YOUTUBE_COOKIES.md`. On 2026-11-17 ordinary videos resolved without cookies from the host IP; cookies matter for age-restricted videos and bot checks.
- Not yet tested end to end in a voice channel after the 2026-11-17 changes.

### 7.6 Clip (`cogs/clip.py`)

- `/clip url start end`: streams that section into your voice channel. `/sclip url start end name`: cuts it with FFmpeg to `Saved/<name>.mp3` and posts it to `savedClipsChannel` (or a channel named `saved-clips`). `/cplay name` (autocomplete) plays a saved clip.
- Timestamps accept `83`, `83.5`, `1:23`, `1:02:03`; max length `maxClipSeconds` (300). Names `[A-Za-z0-9_-]{1,64}`.
- Uses music.py's resolver, so the same cookies and JS runtime apply. Works with or without the Music cog loaded (`bot.get_cog("Music") or Music(bot)`).

### 7.7 Moderation (`cogs/moderation.py`)

`/kick` (Kick Members), `/purge n` (Manage Messages), `/speak`, `/reloadcog name`, `/streamlive`, `/streamended`. Also the 2 s flag watcher for `logs/reload_cog.flag` (console reloads) and `logs/watchdog_alert.flag` (owner DM). The watcher lives here, not in the reloaded cog, because a cog cannot safely reload itself.

### 7.8 Self-update (`cogs/selfupdate.py`)

Every 15 minutes (and `/check_updates`, admin): `git fetch origin <current branch>`, `git diff --name-only HEAD FETCH_HEAD`, map changed paths to loaded extensions (a cog's own `.py` plus `cogs/<name>_*.py` helpers). If a changed file has a local edit → `conflict_skip`. Otherwise `git pull --ff-only` and `reload_extension` per affected cog (atomic: a failed reload keeps the old code). It never reloads `bot.py`, `config.json`, non-`.py` files, or other repos. Webhook-triggered updates were discussed (Cloudflare Tunnel / Discord relay); **do not build one until Thea chooses**.

### 7.9 Legacy cogs (Thea, 2019–2021; off unless listed in `"cogs"`)

| Cog | What it does | Needs |
|---|---|---|
| `roles` | Auto-roles on join (crewmate, updates), reaction roles from the `roles` table, `/maprole`, `/roles`, `/roleall` | `roles`, `crewmate`, `updates`, `rolesMessageID`, `updatesEmojiID`, `welcomeChannel`; "Team Member" role |
| `points` | 1 XP per message per minute, `/exp`, `/award` (role "Admin") | `exp`, `bucks` ledgers |
| `gambling` | `/slots` (feeds a jackpot), `/jackpot`, `/pool`, `/giftbucks` | `bucks`, `jackpot` ledgers |
| `shop` | Premium-role perks on messages (e.g. Forest Nymph flower reactions), `/addpaidrole`, buy by reaction | `botShop`, `workChannel` |
| `pet` | Pokémon-style pet tables only; never finished | pet tables |
| `house` | Fire Emblem house roles: `/assign` (Officer), `/apply`, `/broadcast` via webhooks | `be/bl/gd_webhook` |
| `test` | Helper only, no commands | |

Balances are never stored: they are sums over append-only ledgers.

### 7.10 Home Assistant (`cogs/homeassistant.py`, 2026-11-24)

Carries messages between Discord and Thea's local Home Assistant agent. The agent is a separate package, `ha_agent` in `C:\codingShit\ha-agent` (not in this repo; the brief this cog follows is its `COG_GUIDE.md`, copied here as `docs/AGENT_COG_DESIGN.md` by Thea on 2026-11-25). It picks the tools, asks Qwen3.5-4B on Ollama and calls Home Assistant's REST API. Wrong replies are fixed in the agent, never here.
- **Must run on Golurk:** Ollama listens only on 127.0.0.1, on purpose. Moving the bot needs an HTTP wrapper and a network-exposure decision first; ask Thea.
- **Install:** `py -3.14 -m pip install -e C:\codingShit\ha-agent` (editable, so agent fixes apply at the next cog reload, which `/ha reload` does since 2026-11-25: `cog_unload` drops `ha_agent`, `ha_agent.*` and the dev-mode `ha_agent_fake_ha` from `sys.modules`, so reloading `cogs.homeassistant` re-imports the agent; since 2026-11-24, before that it needed a bot restart). Done on the host 2026-11-24. Off until `"homeassistant"` is in `"cogs"`; the token goes in the `HA_TOKEN` user environment variable (Thea sets it; never in config.json on the Drive if avoidable, never in docs).
- **Who and where:** `allowed_user_ids` (default Thea) in `channel_id` and the threads under it; DMs only from `dm_user_ids` (default: the bot's `owner_id`, Thea; a separate list on purpose). ID lists, never roles. Everything else is ignored silently, including @mentions in other channels; slash commands answer "I only answer in #channel" (allowed users) or "Not allowed." ephemerally. With `channel_id` unset only the DMs work. Changed 2026-11-24 at Thea's request (before: DMs and mentions anywhere for allowed users).
- **What reaches the agent:** messages from the people above in the places above (an @mention is stripped, not needed); bots never. Memory key `discord:<thread id>`, `discord:<channel id>` (only when a thread couldn't be opened) or `discord:dm:<user id>`; the agent serializes each conversation.
- **Threads (2026-11-24, branch `feature/ha-cog-threads`):** a new message in `channel_id` itself opens a thread on that message, named after its first line (max 100 chars, archives after 24 h idle), and the answer, confirm buttons and later replies go there. The thread is the conversation, so `/think` and `/ha reset` inside it act on that thread only. The agent keeps `history_turns` exchanges (config, default 6) in memory. After a bot restart (or once the agent drops a least-recently-used conversation past its 500), the first message or slash command in a thread finds `agent.has_history()` false, re-reads up to `4 × history_turns + 10` thread messages plus the starter message from the parent channel, and hands them back with `agent.remember()`. `thread_turns` writes user turns as "Name: text" (as `chat()` stores them), drops `-#` footer lines, joins split replies, and leaves out confirm prompts, reasoning spoilers, error notices and other people. If the thread can't be read it answers without the history. If the bot lacks Create Public Threads, it logs a warning and answers in the channel. `/ha ask` doesn't open a thread.
- **Answering:** typing indicator, `speaker` = display name, `asyncio.wait_for` 180 s. Reply text plus a small-print footer per action (`✅ fan.set_percentage → Air Purifier {percentage: 40}`, `❌ … (declined)`) and seconds; split under 2000 chars; mentions never ping.
- **Confirm buttons:** the agent awaits `confirm(summary)` before risky actions. The cog posts "Allow **…**?" with Allow / Deny; only the asker can press (others get an ephemeral refusal); Deny, 60 s without an answer, or any error = no. The 60 s deadline is enforced by `asyncio.wait_for` in `make_confirm`, not by the View's own timeout (discord.py only starts that once a real send registers the view). Never auto-approves.
- **Errors:** Home Assistant down or token rejected → "Home Assistant isn't responding"; Ollama down → "Start Ollama on Golurk"; timeout → "try again". Tracebacks go to the log only. If HA is down at load, the cog stays loaded and retries `start()` on the next message. No token or no package → it stays loaded and says what is missing to allowed users.
- **Thinking (required by the guide since 2026-11-24):** Qwen reasons first, about 3x slower (3.7 s vs 13.0 s measured), off by default. Per conversation, kept in the agent (resets to off on restart). `/think` flips it, `/think on|off` sets it (`agent.set_thinking`), `/think <message>` thinks for that one message only (`chat(..., think=True)`, toggle untouched). A thought-about reply has 🧠 in its footer and its reasoning (`reply.thinking`, cut to 1800 chars, `||` defused) as a spoiler in a last message.
- **Timers (2026-11-25, branch `feature/ha-cog-timers`):** the agent's `set_timer` only works when the host gives it `on_timer(cid, text)`. The cog sets `agent.on_timer = cog.deliver_timer` after building the agent (not as a constructor argument, so older agents still build). `timer_target(cid)` reverses `conversation_id`: `discord:<id>` → channel or thread (`get_channel`, else `fetch_channel` for archived threads; sending unarchives), `discord:dm:<id>` → that user. Messages never ping. Timers are saved by the agent and re-armed by `start()`, which runs in `cog_load` before Discord is connected, so delivery first awaits `bot.wait_until_ready()` (safe: each timer is its own task). If the place is gone (thread deleted, no access), the message goes to the owner's DMs (lowest `dm_user_ids` entry, else Thea) instead of vanishing; Claude chose this on 2026-11-25. Never raises; failures are logged. Thread restore (`thread_turns`) skips timer messages, so they never come back as agent answers (2026-11-25, branch `fix/ha-timer-restore`).
- **Modes (required by the guide, 2026-11-24, branch `feature/ha-cog-threads`; updated 2026-11-25):** the agent now has `general` (default: the house, questions, web search, math, notes, documents, timers; hands coding to `code` per message, by keywords or the model) and `code` (answers only). `home` was folded into general; the name is still accepted as an alias but `MODES` no longer lists it, so `/mode` offers only general and code. Per conversation (so per thread), kept in the agent, back to general on restart. `/mode` shows it, `/mode <name>` sets it (choices from `ha_agent.MODES`), `/code <message>` uses code mode once (`chat(..., mode="code")`; the cog only passes `mode` when set, so an older agent still works). Footer's last line: mode that answered (`🏠 home (auto)` when general handed it over), 🧠 if it thought, `🔎 N tools` for tool calls that aren't house actions (web lookups, device reads), seconds. `split_message` closes an open code block at a cut with ``` and reopens it (same language) in the next part, and keeps indentation.
- **Commands:** `/think [message]`, `/mode [name]`, `/code message`, `/ha ask text`, `/ha reset` (forget this conversation), `/ha reload` (reloads the `cogs.homeassistant` extension, so the cog and `ha_agent` code are re-imported, `config.json` is read again from disk (`read_config`, not `config.py`'s copy cached at bot start, so a changed `llm_url` or other setting applies), a fresh agent starts and HA's actions are re-read; reports the action count and model server, or that the old code still runs if the load failed; agent memory starts fresh, threads re-read themselves; until 2026-11-25 it only re-read the actions, so agent changes needed a bot restart). Global, but only work in the agent channel, its threads and allowed DMs.
- **Dev mode:** `fake_ha: true` loads the agent repo's `tests/fake_ha.py`, which records actions instead of running them; no token needed. Restarting HA through it is the way to try the buttons.
- **Not yet tried live:** no HA token on the host yet and the cog is not in the host's `"cogs"`; only the unit tests (`tests/test_homeassistant.py`, fake agent) have run.

---

## 8. Golurk Console (`astra_console.py`, `console_services.py`, `console_settings.py`, `console_terminal.py`, `console_strata.py`)

Details: `docs/CONSOLE.md`.
- Each pane's **Restart** button sits on its own header (since 2026-11-25); the top bar keeps only Watchdog, Pull + Restart and Settings.
- The pane area scrolls (a Canvas holding the grid, dark arrowless ttk scrollbars). Rows have minimum heights (`PANE_MIN_HEIGHT` 230, `SHORT_MIN_HEIGHT` 170, `STRATA_MIN_HEIGHT` 640 in `console_services.py`), so a short window scrolls instead of squashing panes. The wheel scrolls a log pane until it hits its end, then the page. `fit_page` runs `update_idletasks()` before reading the grid's requested height: Tk's own idle pass can come after it, and the stale height cut off the bottom of the Astrabot group when a pane was expanded (fixed 2026-11-25).
- **Strata pane** (full width under the Astrabot group, built 2026-11-25): runs the real interactive `strata.py monitor` (a Textual TUI) in a pseudo-console. `console_terminal.TerminalSession` spawns it with pywinpty (ConPTY) and feeds its output to a pyte screen on a reader thread; `TerminalView` draws only changed rows into a tk Text every 66 ms and sends keys and (when the program asks for it) SGR mouse reports back. Needs `pywinpty` and `pyte` in `py -3.14` (installed on the host); without them, or without Strata, the pane says what is missing. Font: Cascadia Mono when installed, because Consolas has no one-cell block glyphs and the monitor's sparklines broke its boxes. **Pop out** moves the same monitor process into its own window (the session moves between views, the PID stays); **Reattach** or closing that window brings it back. Closing the console stops the monitor only: Strata keeps running in its own host process.
- Strata **Start / Stop / Restart** (`console_strata.StrataControl`, one action at a time, in the background): plain `runtime start`; Stop and Restart first run `--plan --json`, then act with `--if-owner-id` / `--if-instance-id` from that plan; Restart adds `--startup-wait 600`, refuses a `source_switch` or nothing running, and allows a launch-file migration only with `--if-source-sha256` of the planned candidate. Stop asks for confirmation. Only state, readiness and API URL from the replies are shown (never IDs or tokens); messages go to the header note and `logs/strata.log`. `console.json` `"strata": {"dir", "profile"}` (defaults: `%LOCALAPPDATA%\Strata\terminal\current`, `launch-iq1_m-32k`; profile checked against a strict pattern).
- **Strata on another PC** (2026-11-25): `"strata": {"ssh": "laptop", "port": "8080", "tunnel_port": "18080"}`. Since 2026-11-25 the model runs on the laptop **Amethyst** (NixOS, 192.168.4.29, SSH alias `laptop`, key `id_ed25519_laptop_claude`; Strata from `~/strata-check` via `run-coder-iq1_m.sh`, not `strata.py runtime`). Strata refuses to monitor a remote server over plain HTTP, and the laptop's venv lacks the monitor's libraries (`rich`, Textual), so `console_strata.Tunnel` keeps `ssh -N -L 127.0.0.1:18080:127.0.0.1:8080 -- laptop` running (BatchMode, ExitOnForwardFailure, ServerAlive 15 s x 3) and this PC's Strata runs `monitor --url http://127.0.0.1:18080`. The tunnel opens in the background before the monitor (`open_monitor` → `__monitor_tunnel__`); a failure shows ssh's last stderr line (`logs/strata_tunnel.log`). A port that already answers (a tunnel left by a crashed console) is reused, not killed. Closed in `on_close`, before a Watchdog relaunch and at exit (`atexit`). Start / Stop / Restart are left off the pane in this mode (they act on this PC's Strata only); the title is `Strata (<host>)`. `ssh` must be a plain alias or address (no leading `-`); bad ports fall back to the defaults.
- Seven built-in panes in a collapsible "Astrabot" group: bot, audiobook site (log lines from `audiobooks*`/`aiohttp*` loggers), absLogin, wiki backend, classifier, portfolio (from `portfolio` logger), and (since 2026-11-22, full width at the bottom) the **Automated Librarian**. Each has Start/Restart/Stop and crash handling (10 s restart, give up after 5, a 60 s run resets the streak). Built-ins cannot be removed.
- The Automated Librarian pane does not run the librarian (it lives on the Ubuntu server): `LibrarianSupervisor` runs `ssh -o BatchMode=yes thea-server` with `systemctl --user is-active automated-librarian` then `journalctl --user -u automated-librarian -f -n 30` (`librarian_command` in `console_services.py`). It needs key login from this PC to the server (`~/.ssh/config` alias `thea-server`; works as of 2026-11-22). `console.json` `"librarian": {"host", "unit"}` changes it; a blank host turns it off; host and unit are checked against a strict pattern before going into the remote command. Its Restart button reconnects. Its reconnect policy is its own `_retry_delay`: 10, 20, 40 s ... capped at 5 min, never giving up (a lost connection is usually a reboot or a network blip; the base `Supervisor._retry_delay` hook was added for it). Not in the watchdog.
- Settings adds extra services (program, PowerShell, batch; env, working folder, stop commands, start at login), saved in `console.json`; those can be removed. The Dragonwilds dedicated server (`C:\Servers\Dragonwilds`) is one.
- "Pull + Restart" pulls Astrabot, absLogin and the wiki repo (not AstraThought; use the Classifier pane's Restart). Every start frees the service's port first.
- Decisions panel: last 50 classifier decisions, refreshed on classifier log events, not polled.
- Watchdog (on by default; `--no-watchdog`): if a service's watcher thread is dead on two checks 15 s apart (after a 2 min grace), it restarts the whole console, at most 3 times an hour (`logs/watchdog_restarts.json`), then gives up and alerts the owner via the flag file.
- **Remote feed** (`console_remote.py`, protocol v1 in `docs/CONSOLE_REMOTE.md`, built 2026-11-25): watch-only HTTP `GET /status` and WebSocket `GET /ws?boot=&since=` for the phone app; pane states plus every pane's lines (500 per pane replayed, one `seq` per console run). Off unless `console.json` `"remote": {"bind", "port", "token"}` has one literal IP in `bind` (never `0.0.0.0`/`::`); the token comes from `GOLURK_REMOTE_TOKEN` (preferred, keeps it off the Drive) or `remote.token`, at least 32 characters, `Authorization: Bearer` only, constant-time compare, bare 401 otherwise. Why it is off (bad bind, short or missing token, address not on this PC) goes to the Bot pane. The console's line queue is a `TapQueue`, so every `(stream, line)`, also direct `lines.put` calls, reaches the hub from any thread; the server runs on its own thread and asyncio loop and never touches Tk; `__` streams and `strata` notes are never sent. Stopped in `on_close` and before a Watchdog relaunch. Port 13386; the firewall rule is Thea's to add.
  - **Screens** (2026-11-25, still protocol 1): `screen` frames mirror the Strata pane's monitor grid to the phone (watch-only, never resized for it). The console hands the hub the live `TerminalSession` from the Tk thread (`Hub.set_screen` on open/end with the header note when there is none, `Hub.screen_moved` on pop out/reattach). A server-loop task samples once a second, only while a client is connected: `sample_screen` copies every row with `screen_runs` under `session.lock` and never reads or clears `screen.dirty` (the Tk view's). Full frame after `replay_done` (the last-sent grid, so diffs line up), changed rows only after that, full again on a new size/session/view, `running: false` once when it stops. Not buffered, no seq.
- Closing the window stops everything, including a service caught mid-start: `Supervisor._run_once` checks `_closing` under `_lock` right after the spawn and stops the new process itself if `shutdown()` already ran (fixed 2026-11-24; it used to orphan that process). Do not run hand-started copies of the services alongside it.

---

## 9. Related repos and services

| Repo | What | Local path |
|---|---|---|
| `theruvial/absLogin` (private, `main`) | Audiobookshelf signup at audio.theruvial.com/signup (`:13381`, stdlib). Creates `user` accounts (download on; upload/update/delete off). Needs `ABS_API_KEY` (admin). Invite code from its own `config.json` (`invite_code`, read every request; empty = closed; 20 bad codes / 10 min cap). Password change page verifies the current password. Usernames forced lowercase. | `G:\My Drive\codingShit\absLogin` |
| `theruvial/AstraThought` (public) | SetFit task classifier service on `:13383`; data and model git-ignored | `C:\codingShit\AstraThought` (own venv) |
| `egamI-rorriM/Aboard-the-Astra---Remake` | the game; wiki backend under `Tools/wiki/backend` | `C:\codingShit\Aboard-the-Astra-Wiki` |
| `automated-librarian/` (in this repo) | Runs on `thea-ubuntu-server` as the user service `automated-librarian`. Files each `.torrent` as `Author/Series/Title`, adds it to Transmission, checks the downloaded file's tags, links multi-book packs into the library over SSH on the TrueNAS, asks Audiobookshelf to scan and match, and calls the bot's intake (`docs/LIBRARIAN_INTAKE.md`). Python 3.12, standard library only; secrets live in `~/.config/automated-librarian/env` on the server, never in the repo | `~/repos/Astrabot/automated-librarian` on the server (a clone of this repo; the service runs straight from it; update with `automated-librarian/update.sh`) |
| `ha-agent` (local only, package `ha_agent`) | Home Assistant agent for Qwen via Ollama; the bot imports it as a library (§7.10). Python ≥3.11, aiohttp only | `C:\codingShit\ha-agent` (installed editable into `py -3.14`) |
| `theruvial/theruvial` (public) | GitHub profile README and the CV the portfolio serves | edited via `gh api` |
| `theruvial/Astrabot-old`, `AstraThought-old` (private) | pre-2026-11-11 histories (contain old secrets) | — |

---

## 10. Network topology

Nginx on `thea-ubuntu-server` (192.168.4.74), all sites in `/etc/nginx/sites-available/default`:
- **requests.theruvial.com** → `192.168.4.80:13380` (needs the `X-Forwarded-For` line, §7.1)
- **theruvial.com / www** → `192.168.4.80:13384` (portfolio)
- **audio.theruvial.com** → Audiobookshelf `192.168.4.74:13378`; `/signup/` → absLogin `192.168.4.80:13381`
- **media.theruvial.com** → Jellyfin `:8096`; **vaultwarden.theruvial.com** → `:1337`
- **api.theruvial.com** must not expose the wiki backend.

Certbot rule learned the hard way: add only the `:80` block, `nginx -t`, reload, then `certbot --nginx -d <domain>` and let it write the `:443` block. Never reference a cert that does not exist yet.

The sites are public and get constant scanner traffic (visible in `logs/bot.log` as `aiohttp.access` 404s).

---

## 11. Testing

- Run from the repo root: `py -3.14 -m unittest discover -s tests` (469 tests, all passing on master after merging `feature/console-remote-screen` 2026-11-25). Modules importing `config` need a `config.json` in the working directory: in a fresh worktree, copy `config.example.json` to `config.json` (git-ignored).
- Suites: `test_audiobooks`, `test_audiobooks_security`, `test_audiobook_torrents`, `test_audiobook_library`, `test_tasks`, `test_wiki`, `test_selfupdate`, `test_guild_commands`, `test_portfolio_cv`, `test_portfolio_render`, `test_portfolio_cog`, `test_astra_console`, `test_golurk_console`, `test_startup_cogs`, `test_export_youtube_cookies`, `test_homeassistant`, `test_console_terminal`, `test_console_remote`.
- The Automated Librarian has its own suites: `cd automated-librarian && python3 -m unittest discover -s tests` (46 tests, all passing on the Ubuntu server with Python 3.12 on 2026-11-21). The root run above does not collect them, and they have not been run on the Windows host.
- No tests yet for `music.py`, `clip.py` (beyond imports), `moderation.py` or the legacy cogs.
- `test_manual_restart_is_not_a_crash` used to fail with a 10 s join timeout and a PermissionError on `bot.log`: tearDown's `shutdown()` landed between the bot's spawn and `_proc` being set, leaving an orphan bot. Fixed in `astra_console.py`, not the test; `test_shutdown_during_a_start_leaves_no_orphan` forces that window every run.
- Windows quirk: SQLite keeps files open, so temp dirs in tests use `ignore_cleanup_errors=True`; a rare temp-cleanup error on a full run passes on rerun.
- Test values only: tests use made-up codes and keys (`test-invite-code`, fake ABS/Transmission servers). Never put real ones in tests.

---

## 12. Security notes and known risks

- **Console remote feed** (§8): no TLS. Over Tailscale it is WireGuard-encrypted; on the plain LAN the bearer token can be sniffed (accepted risk). Watch-only by design: no commands from the client in v1.
- **Old secrets in pre-rewrite history:** the bot token and other keys in `Astrabot-old` history must be considered exposed. Bot token reset and ABS API key rotation are **not confirmed**.
- Requests site ordinary-user login is email-only (§7.1).
- `cookies.txt` is a Google login; a throwaway account is recommended.
- `config.json` lives on Google Drive (synced), with SMTP, Transmission and invite-code values in it.
- This file contains Thea's Discord id and channel ids (not secret) and must never contain credentials.

---

## 13. Open items (as of 2026-11-21)

1. Verify the requests-site security behind the real nginx (X-Forwarded-For line; admin refused on mobile data, allowed at home).
2. Test `/play` and `/clip` in a voice channel with the new cookies, including an age-restricted video.
3. Confirm bot token reset and ABS API key rotation.
4. Decide whether ordinary requests-site accounts get a password or email link login.
5. Webhook vs polling for self-update: awaiting Thea's choice; do not build.
6. Home Assistant agent cog: built 2026-11-24 (§7.10, merged into master). To go live, Thea sets `HA_TOKEN`, adds `"homeassistant"` to `"cogs"` (and an optional `channel_id`), and restarts; try `fake_ha: true` first to see the buttons.
7. Possible cleanups: tighten intents; add tests for music/clip/moderation; a `requirements.txt`; the old fallback `cogs/portfolio.html` still says "directed".
8. Two bot "web UI listening" lines 16 s apart were seen in `logs/bot.log` on 2026-11-17 09:58; not investigated (possible double start or reload).
9. Dragonwilds server build choice (newer 26419371 vs old 26161880) is open (see `docs/HANDOFF-2026-11-10.md`).
10. **Librarian intake (branch `feature/watcher-intake`, 2026-11-20):** bot side built, tested and merged. **Host setup 2026-11-20:** `AUDIOBOOKS_INTAKE_SECRET` set as a Windows user environment variable (random 64 hex chars); the same value is in `codingShit/intakeSecret.txt` on the Drive (next to `absAPIKey.txt`, outside the repo) for the watcher to read; host `config.json` has `audiobooks.intake` enabled with `dry_run: false` (Thea, 2026-11-20). Windows Firewall rule "Astrabot watcher intake" added by Thea (inbound TCP 13385, remote 192.168.4.74 only; verified). **Live since 2026-11-20 09:37** (intake listening, dry run off; checked in `logs/bot.log` on 2026-11-21): the Ubuntu watcher (`automated-librarian/0.1`) made 23 `POST /intake/books` calls that day, none refused; 12 requests linked to torrents (In Progress, no email), and the library watcher then closed 30 requests, so all 42 rows were Fulfilled with none open. Nothing left to do here; keep an eye on `ambiguous` results in the log.
11. **Request-time matching (decided 2026-11-20, not built):** when a request is made, look the book up in canonical metadata to fix title/author/series, and warn if it is already in Audiobookshelf or already requested. Series requests stay website-only: Thea said the Discord side is for her, so no Discord request command.
12. **Automated Librarian code (merged into master 2026-11-21):** in the repo. Renamed from "audiobook watcher" on 2026-11-22 (folder, unit, config/state paths) and migrated on the server the same day: the clone is on `master`, the user service is `automated-librarian`, the old unit is gone. Leftover on the server, safe to delete: `~/.local/state/automated-librarian.empty-run` (already merged) and `plans/hash.tsv` in the state folder (test output, see the change log). The GitHub branch `feature/audiobook-watcher` can be deleted. Also open: author-spelling variants (`ccPenaranda` vs "Chloe C. Peñaranda") can create a duplicate author folder for staged books and many-author packs; the model's author for a whole pack is not cross-checked against the catalogue; Gemini's free tier returns 503 now and then (the librarian retries three times).

---

## 14. Change log

Newest first. One dated entry per change (finished or in progress; say which, and which branch), by the agent that did the work. Entries before 2026-11-17 were written one per working day.

### 2026-11-25 — Claude Code (Opus 5.5), Golurk Console remote feed: Strata monitor screens for the phone (branch `feature/console-remote-screen`, merged into master and pushed 2026-11-25)

- **Why:** hand-over from the Golurk Console Mobile session: the app (branch `feature/strata-screen` of the mobile repo) shows the Strata monitor; its PROTOCOL.md "Screens" section is the contract.
- **What:** `console_remote`: `sample_screen`, `screen_message`, `Hub.set_screen` / `screen_moved` / `screen_initial` / `screen_frames` / `push_screens`, a once-a-second sampling task in `build_app` (`screen_every`), the full frame queued right after `replay_done`. `astra_console`: hands the session to the hub when the monitor opens, ends, pops out or reattaches, and the "not opened yet" / missing note at startup. `docs/CONSOLE_REMOTE.md` has the Screens section and the server-side notes (the session comes through `set_screen`, not `monitor_view()` read from the server thread). 10 new tests (fake session on a real `pyte.Screen`, no Tk or pty); full suite 469 passing. Smoke run against the real console and the laptop's monitor: one full 35x119 frame, every row's runs add up to `cols`, then partial frames with only the changed rows.
- **State:** merged into master (82d9ac3) and pushed.

### 2026-11-25 — Claude Code (Opus 5.5), Golurk Console: Strata pane monitors the laptop's server (branch `feature/strata-remote-monitor`, merged into master and pushed 2026-11-25)

- **Why:** Thea moved the model server to the laptop (192.168.4.29) and asked for the console's Strata monitor to show that one. `strata.py monitor --url http://192.168.4.29:8080` refuses: "remote monitoring requires HTTPS or a local SSH tunnel".
- **What:** `console_strata`: `remote_of`, `tunnel_argv`, `port_open`, `Tunnel`, and `monitor_argv(folder, url=None)`. `console_services`: `strata` gains `ssh`, `port`, `tunnel_port`. `astra_console`: the tunnel opens before the monitor, the title says the host, Start / Stop / Restart are hidden in remote mode, and the tunnel closes on close, relaunch and exit. CONSOLE.md has the setup. 8 new tests; full suite 459 passing. Checked live: the tunnel opens in about 1 s, the embedded pane shows the laptop's monitor (RTX 3060 Laptop GPU, model READY), the port is free after exit. Thea's `console.json` now has `"strata": {"ssh": "laptop"}` (ignored by master's code until this merges).
- **State:** merged into master and pushed. The running console picks it up on its next start.

### 2026-11-25 — Claude Code (Opus 5.5), Home Assistant cog: `/ha reload` re-reads config.json (branch `fix/ha-reload-config`, merged into master and pushed 2026-11-25)

- **Why:** Thea moved the model server to another PC (`homeassistant.llm_url` is now `http://192.168.4.29:8080/v1`). `/ha reload` re-imported the cog and agent but took its settings from `from config import config`, the dict `config.py` loaded once at bot start, so the new URL needed a full restart.
- **What:** `read_config(path="config.json")` reads the file from disk; `HomeAssistant.__init__` uses it when no settings are passed. A file that doesn't parse raises, so the reload fails and the old cog keeps running. `reload_report` now names the model server (`llm_url`, or "the agent's default"). Other cogs still use the cached `config`. 2 new tests and a wider report test (76 in `test_homeassistant`); full suite 451 passing.
- **State:** merged into master and pushed. The running bot needs one restart to get this code; after that `/ha reload` picks up config changes.

### 2026-11-25 — Claude Code (Opus 5.5), Golurk Console remote feed, protocol v1 (branch `feature/console-remote`, merged into master and pushed 2026-11-25)

- **Why:** Thea wrote the v1 contract for a watch-only phone app (pane states, live lines, replay, heartbeat for a dead-man alert).
- **What:** new `console_remote.py` (settings check, bearer auth, `Hub` ring buffers and seq, `TapQueue`, aiohttp app, `RemoteFeed` thread); `console.json` `remote` block (`console_services.py`, blank bind = off); `astra_console.py` taps the line queue, builds `remote_status()`, starts the feed after startup and stops it in `on_close` and `restart_console`. The protocol is in `docs/CONSOLE_REMOTE.md` (§8). 25 tests in `test_console_remote` (aiohttp test server, no Tk); full suite 449 passing.
- **Checked:** the real console window with services stubbed and the feed on 127.0.0.1 with a throwaway token: 401 without the token, `/status` lists the seven built-ins, the WebSocket sent hello, status, replay, replay_done, a live line emitted from a worker thread (ANSI stripped), then a heartbeat status.
- **Not done:** not merged; off on the host until Thea sets `GOLURK_REMOTE_TOKEN`, the `remote.bind` address and the firewall rule. The 1001 close on console exit is wired but only exercised through `runner.cleanup`, not watched from a client.

### 2026-11-25 — Claude Code (Opus 5.5), Home Assistant cog: `/ha reload` reloads the agent's code (branch `fix/ha-reload-code`, merged into master and pushed)

- **Why:** Thea found that only a full bot restart picked up agent changes. `/ha reload` only called `agent.reload_catalog()` (re-read HA's actions); the 2026-11-24 module-dropping in `cog_unload` was in master, but nothing in normal use ever reloaded `cogs.homeassistant` (the console's reload flag only covers the site and portfolio cogs, self-update only reacts to Astrabot pulls, and `/reloadcog` needs the guild Admin role).
- **What:** `/ha reload` now calls `bot.reload_extension("cogs.homeassistant")` and reports from the new cog (`reload_report`: action count, agent off with the reason, or HA not reachable yet). A failed load keeps the old code (discord.py rollback) and says so. 4 new tests (74 in `test_homeassistant`); full suite 424 passing. Checked with a real, unconnected `commands.Bot`: after the command the cog is a new object, `ha_agent` is a new module, the cog's `HAAgent` is the new class, `/ha` is still registered; it read 293 actions from the real HA (read-only).
- **Not done:** not tried on the running bot. Config changes (`config.json`) still need a restart: `config` is imported once.

### 2026-11-25 — Claude Code (Opus 5.5), `docs/AGENT_COG_DESIGN.md` committed (master)

- **What:** Thea's brief for the Home Assistant agent cog (how the Discord cog talks to the `ha_agent` package in `C:\codingShit\ha-agent`), which the cog in §7.10 was checked against, is now tracked. Environment variable names only, no values.

### 2026-11-25 — Claude Code (Opus 5.5), Golurk Console: expanded Librarian pane cut off (branch `fix/console-librarian-height`, merged into master and pushed)

- **Why:** Thea saw that expanding the Automated Librarian with the Strata pane open left it a sliver.
- **What:** the scrolling page read the grid's requested height before Tk had recomputed it, so the page kept the old, smaller height and clipped the bottom of the Astrabot group. `fit_page` now calls `update_idletasks()` first. Smoke run (collapse, then expand the Librarian): group height 800 of 800 needed, against 664 before the fix. Full suite 420 passing; no unit test, since it is Tk layout timing.

### 2026-11-25 — Claude Code (Opus 5.5), Golurk Console: Strata monitor pane, per-pane Restart, scrolling, pop-out (branch `feature/console-strata`, merged into master and pushed)

- **Why:** Thea asked for the restart buttons next to their panes, the whole interactive Strata monitor inside the console with start/stop/restart, a scrollable window that looks good, and a pop-out with a reattach button.
- **What:** new `console_terminal.py` (ConPTY + pyte terminal widget) and `console_strata.py` (plan-guarded Strata runtime calls); Strata pane, header Restart buttons, scrollable page with row minimum heights and dark scrollbars, Pop out / Reattach in `astra_console.py`; layout helpers in `console_services.py` (§8). 25 new tests in `test_console_terminal`, layout tests updated; full suite 420 passing.
- **Checked:** smoke runs of the real window with service starts stubbed: the monitor renders, follows resizes, quits on `q`; pop-out and reattach keep the same monitor PID; nothing left running after close. Strata start/stop/restart were only exercised with `--plan` and scripted replies, never live.
- **Not done:** first live use of Stop/Restart should be watched. Seen once in a smoke run: the page opened scrolled ~20% down (probably a stray wheel event while the test window sat over Thea's screen); not reproduced.

### 2026-11-25 — Claude Code (Opus 5.5), Home Assistant cog: timer messages left out of thread restore (branch `fix/ha-timer-restore`, merged into master and pushed)

- **Why:** found while re-checking the cog against `docs/AGENT_COG_DESIGN.md`: after a restart, `thread_turns` would have turned a "⏰ Timer done" message into an assistant turn, glued onto the previous answer. The brief says to hand back only the conversation.
- **What:** `_bot_noise` also matches the agent's "⏰ Timer done" prefix. 1 new test (70 in `test_homeassistant`); full suite 392 passing.
- **Not done:** not tried live.

### 2026-11-25 — Claude Code (Opus 5.5), AstraThought classifier on the CPU (AstraThought branch `feat/classifier-on-cpu`, commit ba50926, merged into main and pushed)

- **Why:** Thea asked to move AstraThought off the GPU.
- **What (in `C:\codingShit\AstraThought`, not this repo):** `classifier_service.py` takes `--device` / `ASTRATHOUGHT_DEVICE` (default `cpu`) and passes it to `SetFitModel.from_pretrained`; `/health` and the startup log show the device. 3 new tests (51 passing). Checked with the real model: loads on the CPU in about 18 s, scores two messages in 0.28 s, 0 MB of GPU memory. No console change needed. Training is unchanged.
- **Not done:** takes effect after a Restart Classifier in the Golurk Console.

### 2026-11-25 — Claude Code (Opus 5.5), Home Assistant cog: timers, llm_url, two modes (branch `feature/ha-cog-timers`, merged into master and pushed)

- **Why:** Thea added the agent's updated brief as `docs/AGENT_COG_DESIGN.md`. Compared with master, the cog was missing timers (`on_timer`), had no `llm_url` setting, and still expected a `home` mode the agent folded into general.
- **What:** `timer_target` + `HomeAssistant.deliver_timer` (waits for Discord, fetches archived threads, falls back to the owner's DMs when the place is gone, never pings or raises); `agent.on_timer` set in `_build_agent`; `llm_url` config key; `MODE_NAMES` fallback without `home`; the mode-names test follows `MODE_NAMES`. 12 new tests (69 in `test_homeassistant`); full suite 391 passing. See §7.10. The brief itself (`docs/AGENT_COG_DESIGN.md`) is in the main checkout, untracked; not part of this branch.
- **Not done:** not tried live.

### 2026-11-24 — Claude Code (Opus 5.5), Home Assistant cog: reloading the cog reloads ha_agent (branch `feature/ha-agent-reload`, merged into master)

- **Why:** Thea asked whether agent fixes need a bot restart. They did: `import ha_agent` in a reloaded cog reused the cached package from `sys.modules`, so §7.10's "applies at the next cog reload" was wrong.
- **What:** `_forget_agent_modules()` in `cogs/homeassistant.py`, called at the end of `cog_unload` (also when `agent.close()` fails). If the new load fails, discord.py's rollback re-runs the old `setup`, which still holds the old classes. 2 new tests and the existing unload test no longer purges the real package (57 in `test_homeassistant`); full suite 379 passing. Also checked once with a real `commands.Bot` (not connected): after `reload_extension` the cog's `HAAgent` is the newly imported class.
- **Not done:** not tried on the running bot.

### 2026-11-24 — Claude Code (Opus 5.5), Home Assistant cog: modes (branch `feature/ha-cog-threads`, merged into master and pushed)

- **Why:** `COG_GUIDE.md` §9 makes modes required; Thea asked for them.
- **What:** `/mode [name]` (show/set, choices from `ha_agent.MODES`), `/code <message>` (code mode once), `answer(..., mode=)`, `footer_bits` (mode icon and "(auto)", 🧠, `🔎 N tools`, seconds), code-block-aware `split_message`. 8 more tests (55); full suite 377 passing. See §7.10.
- **Not done:** not tried live (no HA token on the host, cog not in `"cogs"`).

### 2026-11-24 — Claude Code (Opus 5.5), Home Assistant cog: channel-only, dm_user_ids, thread context restored (branch `feature/ha-cog-threads`, merged into master and pushed)

- **Why:** Thea wants the agent to read and answer only in its channel (and threads), plus DMs from a configurable `dm_user_ids`. The ha-agent review session (commit 006ddb2 in `C:\codingShit\ha-agent`, now a git repo) added `has_history` / `remember`, so a thread's context can be rebuilt after a restart.
- **What:** `usable_here` decides for messages and slash commands. Mentions outside the channel no longer reach the agent. New `dm_user_ids` setting (default `owner_id`). `thread_turns` + `_restore_thread` rebuild a forgotten thread from Discord. 7 more tests (47); full suite 369 passing. See §7.10.
- **Agent changes the cog now relies on (from 006ddb2):** confirm must return exactly True (ours returns a bool); more actions ask (cover toggle/position, `homeassistant.turn_on/toggle` on locks, covers, alarms); HA timeouts are `HAError`, so they show "Home Assistant isn't responding". No cog change needed for those.
- **Not done:** modes (COG_GUIDE §9). Not tried live.

### 2026-11-24 — Claude Code (Opus 5.5), Home Assistant cog: one thread per request (branch `feature/ha-cog-threads`, merged into master and pushed)

- **Why:** Thea wants each request in the agent channel to get its own thread, with the thread as the agent's context.
- **What:** `agent_place` / `thread_name` helpers; `on_message` opens a thread for top-level messages in `channel_id` and answers there (falling back to the channel if thread creation fails); threads under the channel route without a mention; new `history_turns` setting passed to `HAAgent`. 8 new tests (40 in `test_homeassistant`); full suite 362 passing. See §7.10.
- **Not done:** restoring a thread's context after a bot restart (needs the `ha_agent` API from the parallel ha-agent review session); modes (COG_GUIDE §9, required) aren't in the cog yet. Not tried live.

### 2026-11-24 — Claude Code (Opus 5.5), Golurk Console shutdown race fixed (branch `fix/console-restart-race`, merged into master and pushed)

- **Why:** `test_manual_restart_is_not_a_crash` was failing on master and on feature branches. Thea asked for the real cause, fixed in the console.
- **What:** `Supervisor._run_once` sets `_proc` only after the spawn. A `shutdown()` in that window saw no process, so the new bot ran on unwatched. The window close could leave an orphan bot that way, and the test's tearDown hit it every run. Now `_run_once` takes `_lock` after the spawn: if `_closing` is set it stops and reaps the process and returns, otherwise it publishes `_proc`. One of the two sides always sees the other. New regression test `test_shutdown_during_a_start_leaves_no_orphan` (fails on the old code after 60 s, passes in 0.3 s). Full suite 322/322, three runs.
- **Still open:** `restart()` has a similar window (a restart request landing mid-spawn may be lost; harmless, the bot just isn't restarted). Not changed.

### 2026-11-24 — Claude Code (Opus 5.5), thinking toggle for the Home Assistant cog (branch `feature/ha-agent-cog`, merged into master and pushed)
- The agent's `COG_GUIDE.md` gained a required thinking section. Added `/think [message]` (toggle, on/off, or think once), `think=` passed through to `agent.chat`, 🧠 in the footer and the reasoning as a truncated spoiler message (§7.10). The guide also moved HA's default URL to port 80; the cog leaves that to the agent, only §1.2 changed.
- Tests: 8 more in `tests/test_homeassistant.py` (32). Full suite 353, all passing except `test_manual_restart_is_not_a_crash`, which failed the same way on untouched `master`; fixed the same day by the shutdown race entry above.
- Not tested live (no HA token on the host, cog not in `"cogs"`).

### 2026-11-24 — Claude Code (Opus 5.5), Home Assistant agent cog (branch `feature/ha-agent-cog`, merged into master and pushed)
- New `cogs/homeassistant.py` from the ha-agent repo's `COG_GUIDE.md`: allowlisted DMs / channel / mentions go to `ha_agent`, replies with an action footer, Allow/Deny confirm buttons only the asker can press (60 s, never auto-approve), friendly errors, `/ha ask|reset|reload`, `fake_ha` dev mode. Details in §7.10.
- `ha_agent` installed editable into `py -3.14` on the host. `config.example.json` gained a `homeassistant` block. Not in `DEFAULT_COGS`.
- Tests: `tests/test_homeassistant.py` (24, fake agent, no Discord/HA/Ollama); full suite 345 passing. Found while testing: a View's own timeout never fires unless discord.py registered the view through a real send, so the confirm deadline uses `asyncio.wait_for`.
- Not tested live: no HA token on the host yet, cog not in the host's `"cogs"`.

### 2026-11-22 — Claude Code (Opus 5.5), audiobook watcher renamed to Automated Librarian (branch `feature/automated-librarian`, merged into master, pushed and migrated)

- **Why:** Thea asked to rename the watcher feature to Automated Librarian, and chose the full rename (folder, unit and paths, not just the names people see).
- **What:** `audiobook-watcher/` → `automated-librarian/`, `audiobook_watcher.py` → `automated_librarian.py`, unit `automated-librarian.service` (Description "Automated Librarian"), env `~/.config/automated-librarian/env`, state `~/.local/state/automated-librarian/`, loggers and User-Agent `automated-librarian`. `docs/WATCHER_INTAKE.md` → `docs/LIBRARIAN_INTAKE.md`; "watcher intake" → "librarian intake" in prose and the bot's two log lines (config keys `audiobooks.intake` and env `AUDIOBOOKS_INTAKE_SECRET` unchanged). Console: pane key, `console.json` key and button are now `librarian` / "Restart Librarian"; `LibrarianSupervisor`, `librarian_command`. The bot's own torrent watcher and library watcher, and the console's thread watcher, keep their names. Change-log entries and `docs/HANDOFF-2026-11-10.md` were left as written.
- **Server move:** new `automated-librarian/migrate.sh` (tests first, idempotent). Checked read-only beforehand: nothing else on the server names the old folder (env file paths, cron, shell rc). The old `update.sh` on the server cannot half-apply this: it tests `audiobook-watcher/` in the incoming code, which no longer exists, so it stops with nothing changed.
- **Migration (2026-11-22, run over SSH at Thea's request):** clone switched to `master`, `migrate.sh` ran: tests passed, old unit removed, env folder moved, new unit enabled and active. **The state folder was not moved**: the new folder already existed, so `move` skipped it and the new service started with an empty state and filed one torrent (He Who Fights with Monsters 7, intake `no_request`). Fixed by hand: stopped the service, merged the new `recent.json` / `verify.json` entries (dicts keyed by infohash) into the old files, checked `plans/hash.tsv` identical and both intake queues empty, moved the merged folder into place, restarted. `--recent` shows all 13 books.
- **Root cause, fixed:** `tests/test_mixed_pack.py` set `packs.STATE_DIR` to a temp folder but not `packs.PLANS` (computed at import), so every test run, including `update.sh`'s and `migrate.sh`'s, wrote `plans/hash.tsv` (fake infohash "hash") into the real `~/.local/state/...`. No real plan was overwritten. The test now patches both with `mock.patch.object` and restores them. Removed the same junk from the Windows home.
- **State:** rename merged and pushed (a973772); test fix (7df4535 before this note) pushed and pulled onto the server with `update.sh` the same day. Bot suite 321 and librarian suite 46 passing.

### 2026-11-22 — Claude Code (Opus 5.5), watcher pane in the Golurk Console (branch `feature/console-watcher-pane`, merged into master and pushed)

- **Why:** Thea asked to see the audiobook watcher's status in the Golurk Console.
- **What:** seventh built-in pane "Audiobook watcher (Ubuntu server)" (full width under classifier/portfolio) following the watcher's journal over SSH, plus a "Restart Watcher" button and the watcher in the status bar. `Supervisor` gained `_retry_delay` / `_retry_message` hooks (behaviour of the existing services unchanged). `console.json` accepts an optional `watcher` section. Checked by hand: the pane's exact ssh command streams the state line and journal from `thea-server` through a pipe.
- **Reconnect policy:** doubles from 10 s to a 5-minute cap and never gives up (Thea asked Claude to choose).
- **State:** merged into master and pushed (2026-11-22); no server-side change needed (the pane only reads the journal). Bot suite 321 passing (5 new watcher tests; layout tests updated for the new row). Not yet opened in the real console.

### 2026-11-21 — Claude Code (Opus 5.5), audiobook watcher merged into master

- Thea asked to merge `feature/audiobook-watcher` (the server session's two commits). Fast-forward, no bot code changed. Before merging, on the Windows host: bot suite 316 passing, the watcher's own suite (`audiobook-watcher/tests`) 46 passing with Python 3.14. Scanned for committed secrets: none.
- That branch also holds the fix for the 2026-11-20 Gena Showalter misfiling: *The Immortal Who Loved Me* (Lynsay Sands, no author in the torrent name) was filed under `genaShowalter/theImmortal` because the title starts with Showalter's folder name; `series_scan` no longer counts that (`tests/test_series_scan.py`). The book is now in `lynsaySands/Argeneau`.
- Seen while checking, not fixed (Audiobookshelf metadata, Thea's call): `lynsaySands/Argeneau/The Gift` carries *The Reluctant Vampire*'s ASIN B0051WU9O4. *The Gift* (Argeneau 15.5) has no ASIN of its own; it is Sands' half of the anthology *The Bite Before Christmas* (B005V2HFL4).
- §13: item 12 updated (merged; the server clone still needs `git checkout master`) and moved after item 11.
- State: merged into master and pushed (2026-11-21).

### 2026-11-21 — Claude Code (Sonnet 5.5), watcher service now runs from a clone (branch `feature/audiobook-watcher`)

- **What:** the user service on the Ubuntu server runs `~/repos/Astrabot/audiobook-watcher/audiobook_watcher.py` instead of a loose copy, so changes are made by commit and push and brought to the server with `audiobook-watcher/update.sh` (fetch, test the incoming code in a scratch worktree, then pull and restart; nothing changes if the tests fail). The unit file's `ExecStart` and the README were updated to match. The code run is byte-identical to before; only the path changed.
- **State:** on this branch, not merged. The old copy was renamed `~/audiobook-watcher.old` and can be deleted.
- **Tested:** the service restarted and logged all features on; `update.sh` was not run against a real incoming change yet.

### 2026-11-21 — Claude Code (Sonnet 5.5), audiobook watcher added to the repo (branch `feature/audiobook-watcher`)

- **What:** new folder `audiobook-watcher/` (nine modules, the systemd unit, a README and five test files). It was written on the Ubuntu server on 2026-11-20 and 2026-11-21 and has run there from a plain folder; this change only adds the code to the repo. **No bot code changed.** The bot's side of the contract is `docs/WATCHER_INTAKE.md`.
- **Behaviour in short:** places each torrent as `Author/Series/Title` (name heuristics, Open Library, then a Gemini fallback whose answer is cross-checked against the Audible catalogue through Audiobookshelf); keeps multi-book torrents whole in staging and symlinks each book into the library over SSH; reads the downloaded file's tags and, only for torrents added from now on, moves a misfiled book through Transmission's `torrent-set-location`; asks Audiobookshelf to scan and match after downloads go quiet; tells the bot's intake which books a torrent holds; keeps a log of the last 30 books (`--recent`). Every limit and guard is documented in the code and the folder's README.
- **State:** committed on this branch, not merged to `master`. The service on the server now runs from a clone of the repo (see the next entry).
- **Tested:** 46 tests pass on the server (Python 3.12). Not run on the Windows host. No real Transmission move or many-author pack has run live yet; the tag check was only run against fakes, and read-only over the whole library (422 books, nothing moved).
- **Secrets:** none in the folder. The env file with the keys stays on the server.

### 2026-11-21 — Claude Code (Opus 5.5), watcher intake confirmed live (branch `docs/intake-live`)

- Checked the host instead of assuming: port 13385 is listening, and `logs/bot.log` shows the intake started 2026-11-20 09:37 with dry run off, 23 watcher calls (all 200), 12 requests linked and 30 closed by the library watcher. The live database has 42 requests, all Fulfilled. The in-place channel resync also already ran (09:41). §13 item 10 and the earlier entries corrected; the earlier "restart still to do" notes were wrong.
- State: docs only, on branch `docs/intake-live`.

### 2026-11-20 — Claude Code (Opus 5.5), requests channel synced in place (branch `feature/resync-in-place`)

- **Why:** Thea asked why the audiobook channel was still wiped and re-posted on every cog load (including self-update hot reloads) when the buttons already survive restarts. Now it works like the tasks board: posts stay.
- `cogs/audiobooks.py`: pure `plan_resync(message_ids, rows, channel_id)` → (delete, check, post); `embed_signature`, `message_buttons`, `view_buttons` to skip edits when nothing changed; `resync_channel` rewritten to keep, edit, post and delete. `docs/AUDIOBOOKS.md` updated.
- Tests: new `tests/test_audiobook_resync.py`. Not tested against real Discord yet (first real run is the next restart; its log line gives kept/updated/posted/deleted counts).
- State: merged into master and pushed (2026-11-20). Live: the 09:41 load on 2026-11-20 logged "24 kept, 0 updated, 0 posted, 0 deleted".

### 2026-11-20 — Claude Code (Opus 5.5), retroactive request check, ASIN titles, camelCase authors (branch `fix/camelcase-authors`)

- **Retroactive check (read-only):** all 24 open requests (Jolee's Lynsay Sands Argeneau requests, 2026-11-19) against all 403 library books, ignoring dates: none are in the library. The pack added 2026-11-20 12:35 holds the early Argeneau books; the requested ones are later entries. Nothing closed.
- **Data fix on the host `audiobooks.db`** (backup `audiobooks.db.pre-asin-titles-20261120-092844.bak` first): requests #17, #36–40 had bare Audible IDs as titles because the Audnexus lookup failed at request time. Set from Audnexus: Vampire Valentine (12.5), The Trouble With Vampires (29), Immortal Born (30), Immortal Angel (31), Meant to Be Immortal (32), Mile High with a Vampire (33), author Lynsay Sands. Their Discord posts show the new titles after the next restart's resync.
- **Tightened (same branch, Thea asked):** new `match_title_exact` for sources with a clean title and author (library, intake): same title words on both sides, brackets and series numbers ignored, surname must agree when both sides have an author. Torrent names stay on the looser `match_torrent` (names carry series, narrator and format words). Checked read-only against the live data: all 18 Fulfilled requests still find their library book under the new rule; still 0 of 24 open ones in the library. Tests: 309 passing.
- **Code (`audiobooks_torrents.tokens`):** camelCase split, so `lynsaySands` gives the surname `sands`; the bare-ID guard now also catches ISBN-10-shaped ASINs. Used by the torrent, library and intake matchers. Tests: 305 passing (2 new, 1 extended).
- State: merged into `master` and pushed (2026-11-20); the database fix is live. Takes effect on the next bot restart.

### 2026-11-20 — Claude Code (Opus 5.5), intake secret on the host

- Generated the watcher intake secret and stored it as the user environment variable `AUDIOBOOKS_INTAKE_SECRET` and in `codingShit/intakeSecret.txt` on the Drive for the Ubuntu watcher (value never printed or recorded). Added `audiobooks.intake` to the host `config.json` (backup `config.json.bak`), then turned dry run off at Thea's request. Thea added the firewall rule "Astrabot watcher intake" (inbound TCP 13385 from 192.168.4.74 only; checked). Console restart: done the same morning (see the 2026-11-21 entry).
- State: merged into `master` and pushed (2026-11-20).

### 2026-11-20 — Claude Code (Opus 5.5), watcher intake (branch `feature/watcher-intake`)

- **Why:** the Ubuntu-server session reported that a request for one book of a multi-book torrent never matched (the pack's name has no book titles), so it stayed `Requested` forever. Thea chose: the server's watcher sends the real titles to the bot, the bot links them to requests and the torrent, Audiobookshelf closes them; one email only (Fulfilled); books nobody requested are ignored; private port plus secret.
- **New `cogs/audiobooks_intake.py`:** `POST /intake/books` and `GET /intake/health` on port 13385 (§7.1). `check_caller` (no proxy headers, allowed IPs, constant-time Bearer secret), `parse_payload`, `plan_links` (unique matches only, resend-safe), `build_intake` (off unless enabled with a secret and allowed IPs).
- `audiobooks_db.py`: `requests.torrentHash` column with a backup-first migration, `link_torrent()`. `audiobooks.py`: `mark_downloading()` (In Progress, no email, embed updated), `admin_fulfilled(allow_requested=)`, intake server started/stopped with the cog. `audiobooks_library.py`: closes `Requested` rows too. `config.example.json`: `audiobooks.intake`. New `docs/WATCHER_INTAKE.md` for the server side.
- Tests: 303 passing (20 new in `tests/test_audiobook_intake.py`, 1 new and 2 updated in `test_audiobook_library.py`). Not tested: the real watcher calling in, the firewall, a live database migration.
- **State:** merged into `master` and pushed (2026-11-20). Not deployed: the intake is off until configured, and the server's watcher does not call it yet.

### 2026-11-20 — Claude Code (Opus 5.5), ignore all of `data/`

- `.gitignore`: `data/portfolio_*` replaced by `data/`, so the whole runtime data folder (tasks state, training labels, overrides, `TODO.md`, portfolio files, test torrents) is ignored, as §0 already said. Before this, everything in `data/` except the portfolio files showed as untracked and could be committed by a stray `git add .`.
- State: merged into `master` and pushed (2026-11-20). No code changed.

### 2026-11-17 — Claude Code (Opus 5.5), full design review

- **Rewrote §0–13 of this file after reading every file in the repo.** New: an agents' read-me-first section with the authorship note (agents wrote most code since 2026-10-29, so treat it as your own), a file-by-file authorship table, a repo map, the startup sequence, a full config and env reference, a data-store table, a section for every cog including music, clip and each legacy cog, and current open items. Removed stale Muse-era notes (the `~/workspace` staging paths, the snapshot-push GitHub flow, the host `bot.py` divergence, the old known-failing tests, which now pass). Section numbers changed: the change log is now §14.
- Added `AGENTS.md` (for non-Claude tools) pointing at this file, updated `CLAUDE.md` to match, and corrected the README's cog list (music and clip are loaded; cogs are chosen in `config.json`).
- Tests: 282 passing. No code changed.
- **Update rule changed (Thea):** this file is now updated after every change, finished or in progress, instead of at the end of each day (§0, `CLAUDE.md`, `AGENTS.md`).

### 2026-11-17 — Claude Code (Sonnet 5.5), YouTube cookies for music/clip

- **`scripts/export_youtube_cookies.py`** copies only the YouTube and Google cookies from a signed-in browser (Firefox by default) into `cookies.txt` (`cookiesFile` in `config.json`), then does a test lookup. Guide: `docs/YOUTUBE_COOKIES.md`. `cookies.txt` is now git-ignored (it was not before). The music cog reads it fresh on every lookup, so no restart is needed after a refresh.
- `cogs/music.py` now falls back to Node when Deno is not installed (this host has Node, no Deno).
- Checked 2026-11-17: normal videos already resolved without cookies from the host's IP; cookies matter for age-restricted videos and bot checks. Not tested: playing audio in a voice channel.

### 2026-11-16 — Claude Code (Sonnet 5.5), cogs chosen by config

- **Loaded cogs now come from `config.json`** (`"cogs"` list) via new `startup_cogs.py`; the hard-coded `startup_extensions` list in `bot.py` is gone. Turning a cog on or off is a config edit, not a commit. New key in `config.example.json`; tests in `tests/test_startup_cogs.py`; full suite 279 passing.
- **Host needs:** add a `"cogs"` list to its `config.json` (without one it gets the repo defaults, which include `clip`, currently off on the host), then restart. After that, the host's local `bot.py` edit can be discarded (`git checkout bot.py`) so the self-updater can pull it.

### 2026-11-14 — Claude Code (Sonnet 5.5), requests site security

- **Admin only from the home network** and **invite code to create an account** on the requests site (see §4, "Public-site security"). New `cogs/audiobooks_access.py` (pure rules), changes in `cogs/audiobooks_web.py`, new keys in `config.example.json`. The invite code is read only from `config.json`; nothing in the repo contains it.
- **Same invite code on the Audiobookshelf signup** in the separate absLogin repo (branch `feature/invite-code`).
- **Tests:** `tests/test_audiobooks_security.py` (19 tests); full suite 271 passing. **Not tested:** behind the real nginx. Check that nginx sends `X-Forwarded-For`, then try the admin page from a phone on mobile data (should be refused) and from home (should work).

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
