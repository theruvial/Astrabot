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
| Golurk Console | Tkinter control panel that runs and supervises the bot and its sibling services | §8 |
| Legacy cogs | 2019–2021 economy, roles, shop, gambling, pets, houses; in the repo, off by default | §7.9 |

### 1.2 Architecture at a glance

```
startConsole.bat ──> astra_console.py (Golurk Console, tkinter)
                       ├─ bot.py  (py -3.14 -u)            Discord gateway
                       │    ├─ cogs.audiobooks ── aiohttp web UI :13380 (requests.theruvial.com)
                       │    │     ├─ TorrentWatcher  → Transmission RPC 192.168.4.74:9092
                       │    │     ├─ LibraryWatcher  → Audiobookshelf audio.theruvial.com
                       │    │     └─ intake :13385   ← audiobook watcher on 192.168.4.74 (private, secret)
                       │    ├─ cogs.portfolio  ── aiohttp :13384 (theruvial.com), polls GitHub CV
                       │    ├─ cogs.tasks      → classifier :13383, wiki backend :13382
                       │    ├─ cogs.wiki       → wiki backend :13382
                       │    ├─ cogs.music / cogs.clip → yt-dlp + FFmpeg → voice
                       │    ├─ cogs.moderation (polls logs/*.flag every 2 s)
                       │    └─ cogs.selfupdate (git fetch/pull every 15 min)
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
  roles.py points.py gambling.py shop.py pet.py house.py test.py   legacy cogs (§7.9)
scripts/                one-off tools: backfill_audiobook_titles, check_torrents, check_library,
                        export_youtube_cookies
audiobook-watcher/      server-side companion that runs on the Ubuntu server, not the PC: files torrents into the
                        Audiobookshelf library (see §9); has its own tests/ and README.md
tests/                  unittest suites (282 tests, all passing on 2026-11-17)
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
| `AUDIOBOOKS_INTAKE_SECRET` | audiobooks watcher intake when `intake.secret` is blank (shared with the watcher on the Ubuntu server) |
| `EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT`, `EMAIL_SMTP_USER`, `EMAIL_SMTP_PASSWORD`, `EMAIL_FROM_ADDRESS` | audiobooks_notify (override `audiobooks.email`) |
| `AUDIOBOOKS_CHANNEL_ID`, `AUDIOBOOKS_WEB_HOST`, `AUDIOBOOKS_WEB_PORT`, `AUDIOBOOKS_DB_PATH`, `AUDIOBOOKS_ADMIN_IDS`, `AUDIOBOOKS_ADMIN_EMAIL` | audiobooks overrides |
| `PORTFOLIO_WEB_HOST`, `PORTFOLIO_WEB_PORT` | portfolio overrides |
| `SELFUPDATE_ENABLED`, `SELFUPDATE_INTERVAL_MINUTES` | selfupdate overrides |
| `GITHUB_TOKEN` | wiki backend (not the bot) |
| `SIGNUP_CONFIG` | absLogin, path to its config.json |

---

## 5. Host, launchers and deployment

- **Run everything with `startConsole.bat`** (pulls Astrabot and absLogin, opens the Golurk Console, §8). `startBot.bat` is the older plain launcher (pulls, starts absLogin in its own window, runs the bot with crash restarts). Use one, never both: each starts its own bot.
- **Crash handling (both):** output appended to `logs\bot.log` (UTF-8); each start/stop with exit code in `logs\crashes.log`; restart after 10 s; stop after 5 crashes in a row; exit code 0 stops normally. The console also logs `logs\wiki.log`, `logs\absLogin.log`, `logs\classifier.log`.
- **Piped Python:** always `-u` and `PYTHONIOENCODING=utf-8`, or prints are buffered and emoji crash cp1252.
- **Going live:** a push to `master` reaches the host when the self-updater pulls it (cogs only, §7.8) or on "Pull + Restart" / a relaunch. `bot.py`, `startup_cogs.py`, `config.py` and launcher changes need a full restart. `config.json` changes need a restart (or a cog reload for cog-specific keys).
- **Restart Site / Restart Portfolio** in the console write the cog name to `logs\reload_cog.flag`; `cogs/moderation.py` polls it every 2 s and calls `reload_extension`. Reloading audiobooks re-runs `cog_load`, which clears and re-posts the requests channel.
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

**Request lifecycle:** `Requested` → (Fulfill, torrent match, or watcher intake) → `In Progress` → (Request Fulfilled, or library match) → `Fulfilled`. The library match also closes a row still `Requested` straight to `Fulfilled` (one email). Reject and cancel hard-delete the row. Every step emails the requester, except the watcher intake's move to In Progress (no email, by Thea's choice) (SMTP, `audiobooks_notify.py`; Gmail needs an app password). If email fails, the admin is told; it never pretends it was sent.

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
- `audiobooks_intake.py` (2026-11-20): private aiohttp endpoint on its own port (default 13385) for the audiobook watcher on the Ubuntu server, which files torrents into the library and knows each book's real title/author, including every book of a multi-book pack. `POST /intake/books` {torrent {hash, name}, books [{title, author, series}]}: each book matching exactly one `Requested` row (`match_title_exact`, no date check) moves it to In Progress **without an email** and stores `requests.torrentHash`; books nobody requested are ignored. Security: refuses any proxy header (nginx is on the watcher's server, so the address alone proves nothing), peer must be in `allowed_ips`, Bearer secret compared in constant time (`secret` or env `AUDIOBOOKS_INTAKE_SECRET`; no secret = does not start). Contract for the watcher side: `docs/WATCHER_INTAKE.md`.

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
- Model: SetFit `tasks-AstraThought-v2`; latest scores in the 2026-11-11 change-log entry.

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

---

## 8. Golurk Console (`astra_console.py`, `console_services.py`, `console_settings.py`)

Details: `docs/CONSOLE.md`.
- Six built-in panes in a collapsible "Astrabot" group: bot, audiobook site (log lines from `audiobooks*`/`aiohttp*` loggers), absLogin, wiki backend, classifier, portfolio (from `portfolio` logger). Each has Start/Restart/Stop and crash handling (10 s restart, give up after 5, a 60 s run resets the streak). Built-ins cannot be removed.
- Settings adds extra services (program, PowerShell, batch; env, working folder, stop commands, start at login), saved in `console.json`; those can be removed. The Dragonwilds dedicated server (`C:\Servers\Dragonwilds`) is one.
- "Pull + Restart" pulls Astrabot, absLogin and the wiki repo (not AstraThought; use Restart Classifier). Every start frees the service's port first.
- Decisions panel: last 50 classifier decisions, refreshed on classifier log events, not polled.
- Watchdog (on by default; `--no-watchdog`): if a service's watcher thread is dead on two checks 15 s apart (after a 2 min grace), it restarts the whole console, at most 3 times an hour (`logs/watchdog_restarts.json`), then gives up and alerts the owner via the flag file.
- Closing the window stops everything. Do not run hand-started copies of the services alongside it.

---

## 9. Related repos and services

| Repo | What | Local path |
|---|---|---|
| `theruvial/absLogin` (private, `main`) | Audiobookshelf signup at audio.theruvial.com/signup (`:13381`, stdlib). Creates `user` accounts (download on; upload/update/delete off). Needs `ABS_API_KEY` (admin). Invite code from its own `config.json` (`invite_code`, read every request; empty = closed; 20 bad codes / 10 min cap). Password change page verifies the current password. Usernames forced lowercase. | `G:\My Drive\codingShit\absLogin` |
| `theruvial/AstraThought` (public) | SetFit task classifier service on `:13383`; data and model git-ignored | `C:\codingShit\AstraThought` (own venv) |
| `egamI-rorriM/Aboard-the-Astra---Remake` | the game; wiki backend under `Tools/wiki/backend` | `C:\codingShit\Aboard-the-Astra-Wiki` |
| `audiobook-watcher/` (in this repo) | Runs on `thea-ubuntu-server` as the user service `audiobook-watcher`. Files each `.torrent` as `Author/Series/Title`, adds it to Transmission, checks the downloaded file's tags, links multi-book packs into the library over SSH on the TrueNAS, asks Audiobookshelf to scan and match, and calls the bot's intake (`docs/WATCHER_INTAKE.md`). Python 3.12, standard library only; secrets live in `~/.config/audiobook-watcher/env` on the server, never in the repo | `~/repos/Astrabot/audiobook-watcher` on the server (a clone of this repo; the service runs straight from it; update with `audiobook-watcher/update.sh`) |
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

- Run from the repo root: `py -3.14 -m unittest discover -s tests` (282 tests, all passing 2026-11-17). Modules importing `config` need a `config.json` in the working directory: in a fresh worktree, copy `config.example.json` to `config.json` (git-ignored).
- Suites: `test_audiobooks`, `test_audiobooks_security`, `test_audiobook_torrents`, `test_audiobook_library`, `test_tasks`, `test_wiki`, `test_selfupdate`, `test_guild_commands`, `test_portfolio_cv`, `test_portfolio_render`, `test_portfolio_cog`, `test_astra_console`, `test_golurk_console`, `test_startup_cogs`, `test_export_youtube_cookies`.
- The watcher has its own suites: `cd audiobook-watcher && python3 -m unittest discover -s tests` (46 tests, all passing on the Ubuntu server with Python 3.12 on 2026-11-21). The root run above does not collect them, and they have not been run on the Windows host.
- No tests yet for `music.py`, `clip.py` (beyond imports), `moderation.py` or the legacy cogs.
- Windows quirk: SQLite keeps files open, so temp dirs in tests use `ignore_cleanup_errors=True`; a rare temp-cleanup error on a full run passes on rerun.
- Test values only: tests use made-up codes and keys (`test-invite-code`, fake ABS/Transmission servers). Never put real ones in tests.

---

## 12. Security notes and known risks

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
6. DM AI agent cog (idea only): awaiting scope and API key; do not build.
7. Possible cleanups: tighten intents; add tests for music/clip/moderation; a `requirements.txt`; the old fallback `cogs/portfolio.html` still says "directed".
8. Two bot "web UI listening" lines 16 s apart were seen in `logs/bot.log` on 2026-11-17 09:58; not investigated (possible double start or reload).
9. Dragonwilds server build choice (newer 26419371 vs old 26161880) is open (see `docs/HANDOFF-2026-11-10.md`).
10. **Watcher intake (branch `feature/watcher-intake`, 2026-11-20):** bot side built, tested and merged. **Host setup 2026-11-20:** `AUDIOBOOKS_INTAKE_SECRET` set as a Windows user environment variable (random 64 hex chars); the same value is in `codingShit/intakeSecret.txt` on the Drive (next to `absAPIKey.txt`, outside the repo) for the watcher to read; host `config.json` has `audiobooks.intake` enabled with `dry_run: false` (Thea, 2026-11-20). Windows Firewall rule "Astrabot watcher intake" added by Thea (inbound TCP 13385, remote 192.168.4.74 only; verified). **Live since 2026-11-20 09:37** (intake listening, dry run off; checked in `logs/bot.log` on 2026-11-21): the Ubuntu watcher (`audiobook-watcher/0.1`) made 23 `POST /intake/books` calls that day, none refused; 12 requests linked to torrents (In Progress, no email), and the library watcher then closed 30 requests, so all 42 rows were Fulfilled with none open. Nothing left to do here; keep an eye on `ambiguous` results in the log.
12. **Audiobook watcher code (branch `feature/audiobook-watcher`, 2026-11-21):** added to the repo. Open: the server's clone follows this feature branch until it is merged (then `git checkout master`); author-spelling variants (`ccPenaranda` vs "Chloe C. Peñaranda") can create a duplicate author folder for staged books and many-author packs; the model's author for a whole pack is not cross-checked against the catalogue; Gemini's free tier returns 503 now and then (the watcher retries three times).
11. **Request-time matching (decided 2026-11-20, not built):** when a request is made, look the book up in canonical metadata to fix title/author/series, and warn if it is already in Audiobookshelf or already requested. Series requests stay website-only: Thea said the Discord side is for her, so no Discord request command.

---

## 14. Change log

Newest first. One dated entry per change (finished or in progress; say which, and which branch), by the agent that did the work. Entries before 2026-11-17 were written one per working day.

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
