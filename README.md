# Astrabot

My Discord bot, running since 2019. It started as a points-and-roles bot for my friends' server and grew into the thing that runs my team's to-do list, my audiobook server's request queue and our game's mechanics wiki.

Built with Python and discord.py 2.x, with SQLite for storage (PostgreSQL until 2026).

## A bit of history

- **2019–2021: written by hand.** I wrote the original bot myself, before AI coding tools existed. It had reaction roles, a points economy with gambling and a shop, a tasks system and Trello integration. The economy is an append-only ledger: every movement of currency is recorded, and balances are worked out from the ledger rather than stored and overwritten. The role system uses parameterized SQL, including a window-function query that dedupes emoji-to-role mappings.
- **2026: designed by me, built with AI help.** I came back to the bot in 2026, ported it to discord.py 2.x slash commands, moved it from PostgreSQL to SQLite and added everything in the "What it does now" section. I designed those features and directed AI coding tools to build them.

## What it does now

### Team tasks with a local classifier
- Watches #astra-feedback and decides whether each message is a task, using [**AstraThought**](https://github.com/theruvial/AstraThought), a small SetFit model I trained myself that runs locally on my own machine.
- Posts clear tasks to #todo, and sends unsure ones to #human-intervention, where approve/disagree buttons decide. Every ruling is saved as a new training label.
- Slash commands manage the team's to-do list: `/task_add`, `/task_list`, `/task_start`, `/task_done`, `/task_edit`, `/task_delete`, `/todo` (the whole list as a markdown file) and `/task_stats` (classifier and label counts).
- Details: [docs/README_tasks.md](docs/README_tasks.md).

### Audiobook requests
- A website (Material dark theme, email login) where people request audiobooks by pasting a link from Audible, Spotify, Apple Books, Google Books or Goodreads.
- Each request becomes a Discord post with **Fulfill**, **Request Fulfilled** and **Reject** buttons, and the requester gets an email at each step.
- Watches my torrent client and Audiobookshelf library, so requests move to In Progress and Fulfilled on their own when the book shows up.
- An admin-only dashboard, and `/email_user` for messaging a user.
- Details: [docs/AUDIOBOOKS.md](docs/AUDIOBOOKS.md).

### Audio clips
- `/clip` streams a section of a track into your voice channel.
- `/sclip` saves a clip and posts it to the saved-clips channel, and `/cplay` plays a saved clip.

### Aboard the Astra wiki
- `/wiki` sends the mechanics wiki for the game my team is making, as a single HTML file. It fetches a fresh copy from the wiki backend using a shared secret, and falls back to a bundled copy if the backend is down.

### Self-update
- Every 15 minutes the bot fetches from GitHub and hot-reloads only the cogs whose files changed, so most updates don't need a restart. `/check_updates` runs the check on demand.

### Moderation and admin
- `/kick`, `/purge`, `/speak`, `/reloadcog`, and `/streamlive` / `/streamended` for setting the bot's status during streams.

### Golurk Console
- A desktop control panel (`startConsole.bat`) that runs the bot, the audiobook site, absLogin, the wiki backend and the classifier, each with its own live output pane and restart button. Any other program can be added in Settings.
- Includes a decisions panel for reviewing the classifier's recent calls.
- Details: [docs/CONSOLE.md](docs/CONSOLE.md).

## Still in the code, currently switched off

These are mostly the original hand-written features. They're still in the repo but not loaded in `bot.py` right now:

- **Reaction roles:** `/maprole`, `/roles`, `/roleall`.
- **Points and economy:** XP and Astrabucks per message, `/exp` and `/award`.
- **Gambling:** `/slots`, `/jackpot`, `/pool` and `/giftbucks`.
- **Role shop:** buy roles with Astrabucks; `/addpaidrole` adds them.
- **Music player:** `/play`, `/skip` and `/queue`, streaming from YouTube, YouTube Music, SoundCloud or Spotify.
- **Pets:** a pet that gains XP and HP as you chat (never finished).

## Running it

Astrabot is built for my own servers and home network, so it isn't plug-and-play, but here's how it runs:

1. Install Python 3 and discord.py 2.x. There's no requirements file yet, so other cogs may ask for a package or two.
2. Copy `config.example.json` to `config.json` and fill in your bot token and channel IDs. `config.json` holds live secrets and is never committed.
3. Start everything with `startConsole.bat`, or just the bot with `startBot.bat` / `startBot.sh`.

Secrets that aren't in `config.json` (SMTP, the wiki key, the Audiobookshelf key) are read from environment variables.

## Tests

```bash
python -m unittest discover -s tests
```

The tests cover the tasks cog, audiobooks, the library and torrent watchers, self-update, the wiki command, command scoping and the console.

## Project notes

[DESIGN.md](DESIGN.md) is the running record of how everything fits together, for me and for the AI agents that work on the bot.
