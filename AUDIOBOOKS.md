# Audiobook Requests (Astrabot cog + web UI)

Built from `~/workspace/your_files/audiobook-requests-design.md`, on branch
`audiobook-requests` (based off `aiTestBranch`). This file is the
run-book for the feature; the design document is the full spec.

## What it does

- A Material Design website where people log in with a 10-digit
  cellphone number, see all audiobook requests, and submit new ones by
  pasting a link from Audible, Spotify, Apple Books, Google Books, or
  Goodreads.
- Requests live in a SQLite database called `audiobooks`
  (`audiobooks.db` by default), tables `users` and `requests`.
- A new Astrabot cog (`cogs/audiobooks.py`) serves the website with
  aiohttp (already a discord.py dependency, same event loop as the bot)
  and posts one Discord embed per request to a configured channel.
- Discord buttons, in order:
  1. **Fulfill Request** — status `In Progress`, requester texted that
     Thea has begun working on it. The button is then replaced by
     **Request Fulfilled**.
  2. **Request Fulfilled** — status `Fulfilled`, requester texted:
     `The requested book {book} has been added to the server`.
  3. **Reject Request** — the row is hard-deleted from the database and
     the requester is texted:
     `Request Rejected for {book} Speak to Thea for more information`.
- Series links: a request is created for each book in the series that
  has an audiobook; books without one are skipped and listed back to
  the user. If no book in the series has an audiobook, nothing is
  created.

## Files

- `cogs/audiobooks.py` — cog, Discord persistent views, embed handling
- `cogs/audiobooks_db.py` — SQLite schema, users/requests/sessions,
  phone normalisation (`727-266-9924` <-> `7272669924`)
- `cogs/audiobooks_providers.py` — provider allowlist, audiobook
  evidence checks, series enumeration
- `cogs/audiobooks_web.py` — aiohttp pages + API, session cookies
- `cogs/audiobooks_notify.py` — SMS notifier (Twilio via env vars)
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
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` —
  SMS provider, as environment variables on the machine running the
  bot. Alternatively, put them in the config's `audiobooks` object as
  `sms: { "account_sid": "...", "auth_token": "...",
  "from_number": "+1..." }` — environment variables win if both are
  set. Without any of these, texts are logged (number masked) and
  reported to the admin as not sent; statuses still update.
- Trial warning: a Twilio trial account can only text numbers you
  have verified with Twilio, and trial texts are prefixed. To text
  arbitrary requesters, upgrade Twilio to a paid account.

## Running

1. Check out branch `audiobook-requests`.
2. Set `AUDIOBOOKS_CHANNEL_ID` and `AUDIOBOOKS_ADMIN_IDS` (and the
   Twilio variables if SMS is wanted).
3. Start the bot as usual (`bot.py`). The website is served on the
   configured host/port while the bot runs; the bot must be running
   for the website and the Discord buttons to work.
4. Open the website, log in with a 10-digit phone number, and use
   **New Request +**.

## Still needed from Thea (see design doc §15)

- Discord channel ID for request embeds
- Admin Discord user IDs
- SMS provider confirmation (Twilio is assumed; sender number needed)
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
