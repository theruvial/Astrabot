# Golurk Console

`startConsole.bat` opens the Golurk Console (`astra_console.py`, formerly the
Astrabot Console). It runs the Astrabot services and any extra programs you add,
with one pane of live output for each.

## Layout

- The eight Astrabot windows (**Bot**, **Site**, **Golurk-Agent**, **absLogin**,
  **Wiki**, **Classifier**, **Portfolio**, **Automated Librarian**) sit together in one
  **Astrabot** group. The arrow on the group header collapses all eight at once; the arrow on each window's header collapses
  just that window. Collapsed windows give their room back to the others.
- The **Decisions** panel on the right collapses the same way.
- Services you add appear below the Astrabot group, each with its own pane.
- Which windows are collapsed is remembered in `console.json`.
- **Site** and **Portfolio** show the log lines of the audiobooks and portfolio
  cogs, which run inside the bot. The **Restart** buttons on Site and Portfolio
  reload just that cog; Bot's **Restart** restarts both.
- **Golurk-Agent** (full width under Bot and Site) has the agent's chatter, which
  used to fill the Bot pane: the `golurk_agent` cog, the `ha_agent` package, and the
  request lines for the model server and web searches (`httpx`, `httpx2`, `ddgs`,
  `primp`), tracebacks included. Its **Restart** reloads the agent cog in the
  running bot, which also reloads the agent's code (like `/ha reload`); the bot
  reports the result in the Bot pane. `logs/bot.log` still has everything.
- **Automated Librarian** shows the librarian that runs on the Ubuntu server. It
  connects with `ssh thea-server` (key login, no password prompt), prints whether
  the `automated-librarian` service is active, then follows its journal live.
  Its **Restart** reconnects; it does not restart the librarian itself. To use
  another host or unit, or to turn the pane off (blank host), add to `console.json`:
  `"librarian": {"host": "thea-server", "unit": "automated-librarian"}`.
- The eight built-in windows can never be removed. Only services you added have a
  **Remove** button (and Settings only enables Remove for those).

## Restart buttons and scrolling

- Every pane has its own **Restart** button on its header. The top bar keeps
  **Watchdog**, **Pull + Restart** and **Settings**.
- The panes keep a minimum height; when the window is too short for them the
  page scrolls (mouse wheel or the scrollbar on the right). The wheel scrolls a
  log pane first, then the page once that pane reaches its end.

## Strata

- The **Strata (8080)** pane runs the real Strata monitor, the same one you get
  from `strata.py monitor`. Click it and type: keys and mouse go to the monitor.
  `q` closes the monitor without stopping Strata; **Monitor** opens it again.
- **Start**, **Restart** and **Stop** run `strata.py runtime ...`. Stop and Restart
  first ask Strata for a plan and then only act on the instance that plan named,
  so they never stop a different run by mistake. Stop asks you first. Messages
  show next to the buttons and in `logs/strata.log`.
- **Pop out** moves the monitor into its own window (the same monitor keeps
  running). **Reattach**, or closing that window, puts it back in the pane.
- Closing the console closes the monitor only; Strata keeps running.
- Needs `pywinpty` and `pyte` (`py -3.14 -m pip install pywinpty pyte`). Cascadia
  Mono is used when installed, so the monitor's charts line up.
- Another Strata folder or profile: add to `console.json`
  `"strata": {"dir": "C:\\...\\Strata\\terminal\\current", "profile": "launch-iq1_m-32k"}`.
- The model on another PC: set `"ssh"` in that block to its SSH alias (key login,
  e.g. `"laptop"` from `~/.ssh/config`). The pane, titled **Strata (laptop)**, then
  watches that server through an SSH tunnel (local port `tunnel_port`, default
  18080, to the server's `port`, default 8080), and Start / Stop / Restart are
  hidden: they only act on this PC's Strata. **Monitor** reopens the tunnel if it
  dropped; ssh's error goes to the header note and `logs/strata_tunnel.log`.

## If the console is force-closed or crashes

Ending the console from Task Manager, `Stop-Process` or a crash runs none of its
shutdown code. Its built-in programs (bot, wiki, absLogin, classifier, the
Librarian's `ssh` log tail) and the Strata SSH tunnel still end with it: Windows
does that through a job object (`console_job.py`). So the bot goes offline too,
until you open the console again.

Two kinds of thing are left alone: the Strata server, and services you added (so
a game server is never ended without its stop commands). An added service can
therefore still be running after a force-close. Check Task Manager before you
start it again.

## Remote feed (phone)

The console can serve a watch-only feed of every pane for the phone app (no buttons:
the phone can't start or stop anything). It is off until you set it up:

1. Make a token of at least 32 characters and set it as a user environment variable,
   so it stays out of the Drive-synced `console.json`:
   `setx GOLURK_REMOTE_TOKEN "<token>"` (then reopen the console).
2. Add to `console.json`: `"remote": {"bind": "<this PC's Tailscale IP>", "port": 13386}`.
   It must be one address; `0.0.0.0` is refused.
3. Add a Windows Firewall inbound rule for TCP 13386 from the Tailscale range.

The Bot pane says "Remote feed listening on ..." when it works, or why it is off.
Use Tailscale: the feed has no TLS, so on plain Wi-Fi the token could be sniffed.
The protocol is in `docs/CONSOLE_REMOTE.md`.

## Adding a service

Press **Settings**, then **+ Add service**. For each one set:

| Field | Meaning |
|---|---|
| Name | The pane title. |
| Run as | Program (path + arguments), PowerShell command, or batch file / cmd command. |
| Command | What to run, for example `"C:\Servers\Dragonwilds\start.bat"`. Several lines run one after another. |
| Working folder | Where it runs. |
| Environment variables | `NAME=value`, one per line. |
| Stop commands | Lines typed into the program to stop it cleanly (for example `/save`, `/quit`). Empty means it is simply ended. |
| Stop wait | Seconds to wait for a clean stop before ending it. |
| Start when the console opens | Off means the pane waits for its Start button. |

Each added service pane has **Start**, **Restart**, **Stop** and **Remove**.
Stop leaves it stopped (it is not restarted behind your back). A service that
crashes restarts after 10 seconds and gives up after 5 crashes in a row, like the
built-in ones. Closing the console stops all of these services.

Added services are saved in `console.json` next to the console. That file is not
committed. `console.example.json` shows the format, including a RuneScape:
Dragonwilds entry.

## RuneScape: Dragonwilds server

From public setup guides (not Jagex documentation, so check against the official
page before relying on it):

- The dedicated server is a separate free Steam tool (app id 4019830). Installing it
  with SteamCMD's `+login anonymous` needs no Steam account.
- Settings live in `DedicatedServer.ini` under
  `RSDragonwilds\Saved\Config\WindowsServer\`, not in command-line flags. The
  server will not start or appear in the browser without an owner ID set there.
  World saves are the `.sav` files in `RSDragonwilds\Saved\SaveGames\`.
- The default game port is UDP 7777, so allow it in the Windows firewall.
- Ending the server process with no clean stop command can lose progress since
  the last save. If the server has a console command that saves and quits, put it in
  Stop commands.
- Launching the server from the Steam library's Play button counts as you playing a
  game, which logs the same account out on another PC. Start it from the console
  (which runs the `.exe` directly), or use a separate anonymous SteamCMD install so
  it never touches your library copy.

Guides: <https://hub.tcno.co/games/dragonwilds/dedicated_server/> and
<https://xgamingserver.com/docs/runescape-dragonwilds/configure-your-server>.
