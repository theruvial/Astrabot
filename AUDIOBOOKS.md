# Audiobook Requests (Astrabot cog + web UI)

Built from `~/workspace/your_files/audiobook-requests-design.md`, on branch
`audiobook-requests` (based off `aiTestBranch`). This file is the
run-book for the feature; the design document is the full spec.

## What it does

- A Material Design website where people log in with an email
  address, see all audiobook requests, and submit new ones by
  pasting a link from Audible, Spotify, Apple Books, Google Books, or
  Goodreads. (2026-10-29: migrated from phone-number login to email
  login at Thea's request; old phone-based accounts are preserved in
  `*_phone_legacy` tables and must re-create an account with email.)
- Requests live in a SQLite database called `audiobooks`
  (`audiobooks.db` by default), tables `users` and `requests`.
- A new Astrabot cog (`cogs/audiobooks.py`) serves the website with
  aiohttp (already a discord.py dependency, same event loop as the bot)
  and posts one Discord embed per request to a configured channel.
- Discord buttons, in order:
  1. **Fulfill Request** — status `In Progress`, requester emailed that
     Thea has begun working on it. The button is then replaced by
     **Request Fulfilled**.
  2. **Request Fulfilled** — status `Fulfilled`, requester emailed:
     `The requested book {book} has been added to the server`.
  3. **Reject Request** — the row is hard-deleted from the database and
     the requester is emailed:
     `Request Rejected for {book} Speak to Thea for more information`.
- Series links: a request is created for each book in the series that
  has an audiobook; books without one are skipped and listed back to
  the user. If no book in the series has an audiobook, nothing is
  created.
- Web UI theme (2026-10-29): Material Design **dark mode** —
  dark surfaces, light text, light-purple primary buttons.
- Website cancel (2026-10-29): cancelling from the website **deletes**
  the request — it is removed from the website list, its Discord
  message is deleted, and the requester is emailed "Your audiobook
  request for {book} has been cancelled." It is no longer kept as a
  `Cancelled` row. (Admin Reject is different: it deletes the row but
  edits its embed to Rejected, so the rejection stays visible.)
- Admin Reject reason (2026-10-29): clicking Reject now opens a modal
  asking for an optional reason. If a reason is given, it is appended
  to the rejection email after the original wording ("Reason:
  {reason}") and shown as a Reason field on the Rejected embed. If
  left blank, the email is exactly the original wording.
- Admin web dashboard (2026-10-29): `/admin`, accessible **only** to
  the logged-in user `theruvial237@gmail.com` (config
  `audiobooks.admin_email` / env `AUDIOBOOKS_ADMIN_EMAIL` can change
  it). Everyone else gets 403, even Discord admins. It lists all
  users (edit email — which cascades to their requests and sessions —
  edit first name, create user, delete user including all of their
  requests and Discord messages) and all requests (edit book name /
  link, Fulfill, Request Fulfilled, Reject with an optional reason,
  Delete). Dashboard actions send the same emails and update the
  same Discord embeds as the Discord buttons.
- Admin routing / sidebar (2026-10-29): logging in as
  `theruvial237@gmail.com` sends you straight to `/admin` (which
  lands on `/admin/users`); an already-logged-in admin visiting `/`
  is sent there too. There is **no** admin button on the regular
  requests page — regular users never see one. The admin area is
  split into two pages, `/admin/users` and `/admin/requests`, with a
  narrow 72px icon sidebar on the left like Muse's sidebar (Users
  person icon, Requests book icon, current section highlighted,
  View site and Log out at the bottom).
- Audible links (2026-10-29): Audible is not scraped at all. Audible
  has no regular books, so an Audible product link is an audiobook
  link by definition, and the title comes from the URL slug
  (`/pd/Dune-Audiobook/B002V1OF70` -> "Dune"). Search pages and the
  homepage still fail, since they are not a book at all; a slug-less
  `/pd/<ASIN>` URL uses the ASIN as the title, editable later in
  the admin dashboard. Goodreads links were tested by Thea herself
  2026-10-29; no further Goodreads testing was needed.
- Goodreads series (2026-10-29): only **primary works** are picked,
  not all works. The page's "X primary works" count caps the list in
  series order, and books headed with a range ("Book 1-3") are dropped
  as collected editions. Omnibus/anthology extras are therefore not
  requested, and are not reported as "skipped" either — they were
  never candidates.
- **Restart resync (2026-10-29):** every time the bot starts, the cog
  clears the requests channel completely and re-posts one embed per
  database row, oldest first, with buttons matching its status
  (Requested: Fulfill + Reject enabled; In Progress: Request
  Fulfilled + Reject enabled; Fulfilled: Request Fulfilled + Reject
  disabled; Cancelled: Fulfill + Reject disabled). New message IDs
  are stored back on each row, so clicks on the re-posted embeds work.
  Treat that channel as dedicated to these embeds — anything else in
  it is deleted on restart. The bot needs permission to read, send,
  and manage messages in that channel. If the channel cannot be
  reached at startup, the resync logs a warning and the bot and web
  UI still start; the next restart will resync.

## Files

- `cogs/audiobooks.py` — cog, Discord persistent views, embed handling
- `cogs/audiobooks_db.py` — SQLite schema, users/requests/sessions,
  email normalisation (lowercased, validated)
- `cogs/audiobooks_providers.py` — provider allowlist, audiobook
  evidence checks, series enumeration
- `cogs/audiobooks_web.py` — aiohttp pages + API, session cookies
- `cogs/audiobooks_notify.py` — email notifier (SMTP via env vars or config)
- `cogs/__init__.py` — makes `cogs` a regular package so the cog's
  relative imports work
- `bot.py` — `"cogs.audiobooks"` added to `startup_extensions`, and
  `"cogs.music"` removed from `startup_extensions` (music cog unloaded;
  `cogs.clip` is still loaded)
- `tests/test_audiobooks.py` — stdlib tests for DB + providers
  (run: `python -m unittest discover -s tests -v`)

## Configuration

Update 2026-10-29: `config.json` on this branch now contains the
`"audiobooks"` object below, with the request channel ID Thea provided
(1565347062965477386). Note: that file also contains other bot
secrets, because Thea explicitly asked for it to be committed on this
branch — it is in this branch's git history now, so removing it from a
later commit would not erase it from history. Do not merge this branch
to a public repo.

The cog reads the `"audiobooks"` object from the bot's config:

```json
{
  "audiobooks": {
    "request_channel_id": 0,
    "web_host": "127.0.0.1",
    "web_port": 8080,
    "db_path": "audiobooks.db",
    "admin_user_ids": []
  }
}
```

Environment variables do the same job without touching `config.json`
and override the config values:

- `AUDIOBOOKS_CHANNEL_ID` — Discord channel for request embeds
  (**required in production; without it embeds are logged, not sent**)
- `AUDIOBOOKS_WEB_HOST` (default `127.0.0.1`), `AUDIOBOOKS_WEB_PORT`
  (default `8080`)
- `AUDIOBOOKS_DB_PATH` (default `audiobooks.db`)
- `AUDIOBOOKS_ADMIN_IDS` — comma-separated Discord user IDs allowed to
  click Fulfill / Reject / Request Fulfilled. If this is empty, the
  guild owner and the bot application's owner may still click the
  buttons (fix 2026-10-29: the old code read `bot.owner_id` directly,
  which Astrabot never sets, so everyone was denied).
- `EMAIL_SMTP_HOST` (default `smtp.gmail.com`), `EMAIL_SMTP_PORT`
  (default 587; 465 for implicit TLS), `EMAIL_SMTP_USER`,
  `EMAIL_SMTP_PASSWORD`, `EMAIL_FROM_ADDRESS` (defaults to the SMTP
  user) — email provider, as environment variables on the machine
  running the bot. Alternatively, put them in the config's
  `audiobooks` object as `email: { "smtp_host": "...",
  "smtp_port": 587, "smtp_user": "...", "smtp_password": "...",
  "from_address": "..." }` — environment variables win if both are
  set. For Gmail, the password must be an app password, not the Google
  account password. Without any of these, emails are logged (address
  masked) and reported to the admin as not sent; statuses still update.
  SMS/Twilio is no longer used (removed 2026-10-29).

## Running

1. Check out branch `audiobook-requests`.
2. Set `AUDIOBOOKS_CHANNEL_ID` and `AUDIOBOOKS_ADMIN_IDS` (and the
   email SMTP variables if email notifications are wanted).
3. Start the bot as usual (`bot.py`). The website is served on the
   configured host/port while the bot runs; the bot must be running
   for the website and the Discord buttons to work.
4. Open the website, log in with an email address, and use
   **New Request +**.

## Still needed from Thea (see design doc §15)

- Discord channel ID for request embeds
- Admin Discord user IDs
- Email sender setup (SMTP host/user/password, or a sender address in config)
- How the site will be hosted/exposed (localhost by default — do not
  expose it publicly without HTTPS / a reverse proxy)
- Final wording approval for the Fulfill text (the Reject and
  Fulfilled texts are fixed in the spec)

## Notes / deliberate deviations from the design doc

- `config.json` was originally left untouched to avoid touching
  secrets; on 2026-10-29 Thea explicitly asked for it to be committed
  with the request channel ID, so it now contains the `"audiobooks"`
  object. Environment variables still override it (see above).
- Discord buttons are routed by the embed's message ID (stored on the
  request row) with two persistent views (`Requested` and
  `In Progress`), rather than one view per request ID. This is the
  discord.py persistent-view pattern and survives bot restarts.
- `audiobooks.db` is created at runtime and must never be committed
  (add it to `.gitignore` on the host if it is not already ignored).
