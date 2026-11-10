# Golurk Console

`startConsole.bat` opens the Golurk Console (`astra_console.py`, formerly the
Astrabot Console). It runs the Astrabot services and any extra programs you add,
with one pane of live output for each.

## Layout

- The five Astrabot windows (**Bot**, **Site**, **absLogin**, **Wiki**,
  **Classifier**) sit together in one **Astrabot** group. The arrow on the group
  header collapses all five at once; the arrow on each window's header collapses
  just that window. Collapsed windows give their room back to the others.
- The **Decisions** panel on the right collapses the same way.
- Services you add appear below the Astrabot group, each with its own pane.
- Which windows are collapsed is remembered in `console.json`.
- The five built-in windows can never be removed. Only services you added have a
  **Remove** button (and Settings only enables Remove for those).

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
built-in ones. Closing the console stops everything it started.

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
