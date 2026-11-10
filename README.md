# Astrabot

A personal Discord bot (discord.py 2.x) for the Aboard the Astra team and Thea's audiobook server.

Built and maintained by Thea since 2019, originally as a solo project written by hand before AI coding tools: reaction roles, a points economy and gambling system backed by PostgreSQL (later SQLite), a shop, a tasks system with reaction persistence across restarts, and Trello integration. The economy is an append-only ledger — every movement is recorded and balances are derived from it — and the role system uses parameterized SQL, including a window-function query to dedupe emoji-to-role mappings. The bot has since grown with newer features (audiobook requests, a local task classifier, and the Astra mechanics wiki); see below.

- **Audiobook requests:** a Discord and web workflow for requesting audiobooks. See [docs/AUDIOBOOKS.md](docs/AUDIOBOOKS.md).
- **Tasks:** reads #astra-feedback, classifies messages with a local model, sends unclear ones to #human-intervention, and posts tasks to #todo. See [docs/README_tasks.md](docs/README_tasks.md).
- **Wiki:** `/wiki` sends the Aboard the Astra mechanics wiki.
- **Moderation and self-update:** admin commands, and a watcher that hot-reloads changed cogs.

Run it with `startConsole.bat`, which opens the Astrabot Console. The console runs the bot, audiobook site, absLogin, wiki backend and task classifier, with output panes and restart buttons.

Project notes for people and agents are in [DESIGN.md](DESIGN.md). `config.json` holds live secrets and is never committed; start from `config.example.json`.

## Old music commands
## Command List
<!-- - Play command takes a url as an input and plays a song into a voice channel: 
**!play url**

- Leave command make the bot leave the voice channel: 
**!leave**

- Clip command take a url and 2 timestamps as inputs and takes a clip of an audio file to play into the voice channel: 
**!clip url timestamp_1 timestamp_2**

- SClip command takes a url, 2 timestamps and a filename as inputs and saves a clip of an audio file for later playing:
**!sclip url timestamp_1 timestamp_2 filename**

- CPlay command plays audio files out of the ./Saved Folder:
**!cplay filename** -->

