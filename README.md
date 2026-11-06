# Astrabot

A personal Discord bot (discord.py 2.x) for the Aboard the Astra team and Thea's audiobook server.

- **Audiobook requests:** a Discord and web workflow for requesting audiobooks. See [AUDIOBOOKS.md](AUDIOBOOKS.md).
- **Tasks:** reads #astra-feedback, classifies messages with a local model, sends unclear ones to #human-intervention, and posts tasks to #todo. See [README_tasks.md](README_tasks.md).
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

