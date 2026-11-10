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
| Command | What to run, for example `"D:\Servers\Dragonwilds\start.bat"`. Several lines run one after another. |
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

## Running a service under another Windows account (guest user)

Tick **Run as another Windows user** in the service's settings and give a user
name (and a domain only for a domain account; blank means this PC). The program,
and everything it starts, then run under that account instead of yours. A
batch file works the same way: set Run as to **Batch file / cmd command**.

- **Password:** it is never saved. Settings asks for the *name* of an environment
  variable (for example `GOLURK_GUEST_PASSWORD`) and the console reads the
  password from it when the service starts. Set it with `setx` and restart the
  console. Leave the name blank for an account that has no password.
- **The account needs:** permission to log on to this PC, read and execute access
  to the server's folder, and write access to wherever the server saves its world,
  config and logs. Windows only creates the account's profile the first time it
  logs on.
- **Stopping:** the console ends the whole process tree through a Windows job
  object, since `taskkill` cannot end another account's processes. If the console
  itself dies, Windows ends the guest processes with it.
- **What `Check paths` cannot verify:** whether the account may log on or can
  read the folder. A refused login shows in the pane, for example
  `Windows could not start the program as guest: The user name or password is
  incorrect. (error 1326)`.
- **The built-in "Guest" account** is disabled by default and is meant to stay
  that way. A normal limited local user (a standard account with no admin rights)
  is the safer choice.

### RuneScape: Dragonwilds server

From public setup guides (not Jagex documentation, so check against the official
page before relying on it):

- The dedicated server is a separate free Steam tool (app id 4019830), also
  available through SteamCMD.
- Settings live in `DedicatedServer.ini` under
  `RSDragonwilds\Saved\Config\WindowsServer\`, not in command-line flags. The
  server will not start or appear in the browser without an owner ID set there.
- The default game port is UDP 7777, so allow it in the Windows firewall.
- Ending the server process with no clean stop command can lose progress since
  the last save. If the server has a console command that saves and quits, put it
  in Stop commands.

Guides: <https://hub.tcno.co/games/dragonwilds/dedicated_server/> and
<https://xgamingserver.com/docs/runescape-dragonwilds/configure-your-server>.
