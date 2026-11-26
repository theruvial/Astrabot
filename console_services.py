"""Helpers for the Golurk Console's extra services. No tkinter in here.

The console has built-in windows (bot, site, absLogin, wiki, classifier,
portfolio, librarian and the Strata terminal) that are always there. Anything you add in Settings is a
"service": a program, a PowerShell command or a batch file that the
console starts, watches and restarts, with a pane of its own that can
be removed again. This module holds what those services need, so it is
testable without a window:

* ``console.json`` (next to the console, never committed): load, merge,
  and atomic save. A broken file never stops the console from opening.
* Turning what you typed in Settings into a command Windows can run
  (``command_to_argv``), and checking its paths (``check_entry``).
* Where each pane sits in the grid (``pane_cells``) and how much room a
  row gets (``inner_row_weights``, ``outer_row_weights``), so collapsing
  a pane or the Astrabot group gives its space back.

Adapted from the Service Console that ships as service_console.py on
the other machines.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONSOLE_CONFIG = HERE / "console.json"
STARTUP_NAME = "Golurk Console.bat"

IS_WINDOWS = os.name == "nt"

# The windows that are part of the console and cannot be removed.
BUILTIN_PANES = ("bot", "site", "absLogin", "wiki", "classifier", "portfolio", "librarian",
                 "strata")
BUILTIN_TITLES = {"bot": "Bot", "site": "Site (13380)", "absLogin": "absLogin (13381)",
                  "wiki": "Wiki (13382)", "classifier": "Classifier (13383)",
                  "portfolio": "Portfolio (13384)",
                  "librarian": "Automated Librarian (Ubuntu server)",
                  "strata": "Strata (8080)"}
# The seven built-in windows live together in one collapsible group.
GROUP_KEY = "astrabot"
GROUP_TITLE = "Astrabot"
# Where a built-in pane sits inside that group's grid: (row, column, columnspan).
BUILTIN_CELLS = {"bot": (0, 0, 1), "site": (0, 1, 1), "absLogin": (1, 0, 1),
                 "wiki": (1, 1, 1), "classifier": (2, 0, 1), "portfolio": (2, 1, 1),
                 "librarian": (3, 0, 2)}
# Shorter rows inside the group (the rest get 3).
SHORT_ROWS = {BUILTIN_CELLS["classifier"][0], BUILTIN_CELLS["librarian"][0]}
# The Strata pane (an embedded terminal running the Strata monitor, see
# console_terminal.py) is not part of Astrabot: it sits in the outer grid
# right under the group, full width, and gets more room than a log pane
# because the monitor is a full-screen program.
STRATA_KEY = "strata"
OUTER_CELLS = {STRATA_KEY: (1, 0, 2)}
STRATA_WEIGHT = 10
# Added services sit in the outer grid under the group and the Strata pane.
FIRST_CUSTOM_ROW = 2

DEFAULT_CONSOLE_NAME = "Golurk Console"

DEFAULT_CONFIG = {
    "console_name": DEFAULT_CONSOLE_NAME,
    # Wait this long after the console opens before starting the extra services.
    "startup_delay_seconds": 0,
    # Pane key -> True when that pane is collapsed (remembered between runs).
    "collapsed": {},
    # The services added in Settings. Built-in windows are not listed here.
    "services": [],
    # The Automated Librarian pane tails the librarian's journal on the Ubuntu
    # server over SSH (key login, no password). A blank host turns it off.
    "librarian": {"host": "thea-server", "unit": "automated-librarian"},
    # The local Strata LLM server: its CLI folder and the launch profile the
    # pane's Start / Stop / Restart act on (see console_strata.py). A blank
    # dir turns the Strata pane off.
    "strata": {"dir": str(Path(os.environ.get("LOCALAPPDATA", "")) / "Strata" / "terminal"
                          / "current"),
               "profile": "launch-iq1_m-32k"},
    # The remote feed (console_remote.py, protocol in docs/CONSOLE_REMOTE.md):
    # one literal IP to listen on (blank = off). Leave token blank and set
    # GOLURK_REMOTE_TOKEN instead, so it stays out of this Drive-synced file.
    "remote": {"bind": "", "port": 13386, "token": ""},
}

# Safe to put in the remote shell command: an SSH alias or address, a unit name.
LIBRARIAN_HOST = re.compile(r"^[A-Za-z0-9_.@-]+$")
LIBRARIAN_UNIT = re.compile(r"^[A-Za-z0-9_.@-]+$")
STRATA_PROFILE = re.compile(r"^[A-Za-z0-9_.-]+$")

DEFAULT_SERVICE = {
    "name": "", "title": "", "run_as": "program", "command": "", "cwd": "",
    "env": {}, "stop_input": [], "stop_grace": 30, "autostart": True,
}

RUN_AS = {"program": "Program (path + arguments)",
          "powershell": "PowerShell command",
          "cmd": "Batch file / cmd command"}


# -- config ---------------------------------------------------------------------
def write_json_atomic(path, payload) -> None:
    """Write JSON so a crash mid-write can never leave a half-made file."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def clean_service(spec) -> dict:
    """A service entry with every field present and the right type. Pure."""
    spec = spec if isinstance(spec, dict) else {}
    out = json.loads(json.dumps(DEFAULT_SERVICE))
    for key, value in spec.items():
        if key.startswith("_") or key == "user":  # "user": an old, removed option
            continue
        out[key] = value
    if isinstance(out.get("stop_input"), str):
        out["stop_input"] = [out["stop_input"]]
    if not isinstance(out.get("env"), dict):
        out["env"] = {}
    out["run_as"] = out["run_as"] if out["run_as"] in RUN_AS else "program"
    out["stop_grace"] = _int(out.get("stop_grace"), 30)
    out["autostart"] = bool(out.get("autostart", True))
    out["name"] = service_name(out)
    out["title"] = str(out.get("title") or out["name"])
    return out


def merge_config(user) -> dict:
    """DEFAULT_CONFIG overlaid with the user's console.json. Pure."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if not isinstance(user, dict):
        return cfg
    for key, value in user.items():
        if key.startswith("_") or key not in cfg:
            continue
        cfg[key] = value
    cfg["services"] = [clean_service(s) for s in cfg["services"] if isinstance(s, dict)]
    # Two entries must never share a name: they would share a pane and a log.
    seen: set[str] = set()
    for spec in cfg["services"]:
        spec["name"] = unique_key(spec["name"], seen)
        seen.add(spec["name"])
    if not isinstance(cfg["collapsed"], dict):
        cfg["collapsed"] = {}
    librarian = dict(DEFAULT_CONFIG["librarian"])
    if isinstance(cfg["librarian"], dict):
        librarian.update({k: str(v or "").strip() for k, v in cfg["librarian"].items()
                          if k in librarian})
    cfg["librarian"] = librarian
    strata = dict(DEFAULT_CONFIG["strata"])
    if isinstance(cfg["strata"], dict):
        strata.update({k: str(v or "").strip() for k, v in cfg["strata"].items()
                       if k in strata})
    if not STRATA_PROFILE.match(strata["profile"]):
        strata["profile"] = DEFAULT_CONFIG["strata"]["profile"]
    cfg["strata"] = strata
    remote = dict(DEFAULT_CONFIG["remote"])
    if isinstance(cfg["remote"], dict):
        remote.update({k: v for k, v in cfg["remote"].items() if k in remote})
    remote["bind"] = str(remote["bind"] or "").strip()
    remote["token"] = str(remote["token"] or "").strip()
    remote["port"] = _int(remote["port"], DEFAULT_CONFIG["remote"]["port"])
    cfg["remote"] = remote
    cfg["startup_delay_seconds"] = max(0, _int(cfg["startup_delay_seconds"], 0))
    cfg["console_name"] = str(cfg["console_name"] or DEFAULT_CONSOLE_NAME)
    return cfg


def load_console_config(path=None) -> tuple[dict, str]:
    """Read console.json. Never raises: a missing or broken file gives the
    defaults, plus a note saying why when there is something to say."""
    path = Path(path or CONSOLE_CONFIG)
    if not path.exists():
        return merge_config({}), ""
    try:
        return merge_config(json.loads(path.read_text(encoding="utf-8"))), ""
    except Exception as exc:  # noqa: BLE001 - a bad file must not stop the console
        return merge_config({}), f"{path.name} could not be read ({exc}); using defaults."


def save_console_config(cfg, path=None) -> None:
    """Write the settings, keeping only the keys the console owns."""
    path = Path(path or CONSOLE_CONFIG)
    out = {key: cfg.get(key, DEFAULT_CONFIG[key]) for key in DEFAULT_CONFIG}
    out["services"] = [clean_service(s) for s in out["services"]]
    write_json_atomic(path, out)


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def service_name(spec) -> str:
    """The pane/log key for a service. Never clashes with a built-in window."""
    base = re.sub(r"[^0-9A-Za-z_-]+", "_", str(spec.get("name") or spec.get("title") or "service"))
    base = base.strip("_") or "service"
    return f"svc_{base}" if base in BUILTIN_PANES else base


def unique_key(title, taken) -> str:
    """A key from a title that is not already taken: 'New service' -> 'new_service_2'."""
    base = re.sub(r"[^0-9a-z]+", "_", str(title or "").lower()).strip("_") or "service"
    if base in BUILTIN_PANES:
        base = f"svc_{base}"
    key, i = base, 2
    while key in taken:
        key, i = f"{base}_{i}", i + 1
    return key


def build_saved_config(raw, general, services) -> dict:
    """The console.json to write from the Settings window's edits. Pure.

    Only ``services`` can lose entries, and only the services passed in:
    the built-in windows are not in the file, so there is nothing here
    that could remove one."""
    out = merge_config(raw)
    for key in ("console_name", "startup_delay_seconds"):
        if key in general:
            out[key] = general[key]
    out["services"] = [clean_service(s) for s in services]
    return merge_config(out)


# -- commands -------------------------------------------------------------------
def join_continuations(lines, run_as="program") -> list:
    """Merge lines that continue onto the next one, the way the shell would. Pure.

    A command pasted from a terminal is often split with PowerShell's
    trailing backtick or cmd's trailing caret; those lines are one
    command. Blank lines are dropped."""
    markers = {"powershell": ("`",), "cmd": ("^",)}.get(run_as, ("`", "^"))
    out, pending = [], ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.endswith(markers):
            pending += line[:-1].rstrip() + " "
            continue
        out.append(pending + line)
        pending = ""
    if pending.strip():
        out.append(pending.strip())
    return out


def command_to_argv(command, run_as="program"):
    """What Popen runs for an entry's command. Pure.

    ``program``: the text itself on Windows (CreateProcess parses the
    quoting, so "C:/Program Files/x.exe" --flag works). ``powershell``:
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command <cmd>.
    ``cmd``: cmd.exe /d /s /c "<cmd>", for .bat files. Several lines run
    one after another. An empty command returns [] (monitor only)."""
    if isinstance(command, (list, tuple)):
        return [str(c) for c in command]
    lines = join_continuations(str(command or "").splitlines(), run_as)
    if not lines:
        return []
    if run_as == "powershell":
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-Command", "; ".join(lines)]
    if run_as == "cmd":
        joined = " & ".join(lines)
        return f'cmd.exe /d /s /c "{joined}"' if IS_WINDOWS else ["sh", "-c", joined]
    joined = " ".join(lines)
    if IS_WINDOWS:
        return joined
    import shlex
    return shlex.split(joined)


def command_text(command) -> str:
    """An entry's command as editable text."""
    if isinstance(command, (list, tuple)):
        return subprocess.list2cmdline([str(c) for c in command])
    return str(command or "")


def program_of(command) -> str:
    """The program a 'program' command runs: the first token, quotes removed."""
    text = command_text(command).strip()
    if not text:
        return ""
    if text[0] in "\"'":
        end = text.find(text[0], 1)
        return text[1:end] if end > 0 else text[1:]
    return text.split()[0]


def parse_pairs(text, sep="=") -> dict:
    """'KEY=VALUE' lines to a dict; blank lines and lines without `sep` are skipped."""
    out = {}
    for line in str(text or "").splitlines():
        if sep in line:
            key, value = line.split(sep, 1)
            if key.strip():
                out[key.strip()] = value.strip()
    return out


def pairs_text(d, sep="=") -> str:
    return "\n".join(f"{k}{sep}{v}" for k, v in (d or {}).items())


def text_lines(text) -> list:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def check_entry(spec, which=None) -> list:
    """Problems with one service's settings, as plain sentences ([] = looks fine).

    Checks the working folder and that the program exists (or is on
    PATH). ``which`` is injectable."""
    import shutil
    which = which or shutil.which
    problems = []
    cwd = str(spec.get("cwd") or "").strip()
    if cwd and not Path(cwd).is_dir():
        problems.append(f"Working folder not found: {cwd}")
    command = spec.get("command")
    if not command_text(command).strip():
        problems.append("No command set: it will only be monitored, never started.")
    elif spec.get("run_as", "program") == "program":
        prog = program_of(command)
        if "/" in prog or "\\" in prog or ":" in prog:
            path = Path(prog)
            if not path.is_absolute() and cwd:
                path = Path(cwd) / path
            if not path.exists():
                problems.append(f"Program not found: {prog}")
        elif not which(prog):
            problems.append(f"'{prog}' is not on PATH; use its full path.")
    return problems


# -- pane layout ----------------------------------------------------------------
def pane_cells(custom_keys) -> dict:
    """Grid cell (row, column, columnspan) for every pane. Pure.

    The built-in panes keep their places inside the Astrabot group (its
    own grid). Added services fill two to a row in the outer grid, under
    the group, in the order they were added."""
    cells = dict(BUILTIN_CELLS)
    cells.update(OUTER_CELLS)
    for index, key in enumerate(custom_keys):
        cells[key] = (FIRST_CUSTOM_ROW + index // 2, index % 2, 1)
    return cells


def librarian_command(librarian) -> list[str] | None:
    """The ssh command that tails the librarian's journal, or None. Pure.

    It prints the service state once (``systemctl is-active``), then
    follows the journal from its last 30 lines. BatchMode means a missing
    key fails at once instead of waiting for a password nobody can type;
    the keepalives end a dead connection within about 45 seconds so the
    pane can reconnect. Host and unit are checked before they go into the
    remote command, so console.json cannot inject shell."""
    host = str((librarian or {}).get("host") or "").strip()
    unit = str((librarian or {}).get("unit") or "").strip()
    if not host or host.startswith("-") or not LIBRARIAN_HOST.match(host):
        return None
    if not LIBRARIAN_UNIT.match(unit):
        return None
    remote = (f'echo "{unit}: $(systemctl --user is-active {unit})"; '
              f"exec journalctl --user -u {unit} -f -n 30 -o short-iso --no-pager")
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
            host, remote]


def inner_row_weights(collapsed) -> dict:
    """Vertical room for each row inside the Astrabot group. Pure.

    A row whose panes are all collapsed gets none, so the others grow
    into the space. The classifier and librarian rows are shorter."""
    weights: dict[int, int] = {}
    for key, (row, _col, _span) in BUILTIN_CELLS.items():
        base = 2 if row in SHORT_ROWS else 3
        weights.setdefault(row, 0)
        if not collapsed.get(key):
            weights[row] = base
    return weights


def outer_row_weights(custom_keys, collapsed) -> dict:
    """Vertical room for the Astrabot group (row 0) and each row of added
    services. Pure. A collapsed group, or a row of collapsed services,
    gets none; an open group gets what its panes need. The Strata pane
    (row 1) gets STRATA_WEIGHT."""
    weights = {0: 0 if collapsed.get(GROUP_KEY) else sum(inner_row_weights(collapsed).values())}
    for key, (row, _col, _span) in pane_cells(custom_keys).items():
        if key in BUILTIN_CELLS:
            continue
        weights.setdefault(row, 0)
        if not collapsed.get(key):
            weights[row] = STRATA_WEIGHT if key == STRATA_KEY else 3
    return weights


# The pane area scrolls (2026-11-25): every open row keeps at least this many
# pixels, header included, so a small window scrolls instead of squashing
# panes to a few lines. A tall window still stretches rows by their weights.
PANE_MIN_HEIGHT = 230
SHORT_MIN_HEIGHT = 170
# The Strata monitor needs about 38 rows of Consolas 10 to show its Overview.
STRATA_MIN_HEIGHT = 640


def inner_row_minsizes(collapsed) -> dict:
    """Minimum pixel height of each row inside the Astrabot group. Pure.
    A row whose panes are all collapsed needs nothing (its headers still
    take their own room)."""
    sizes: dict[int, int] = {}
    for key, (row, _col, _span) in BUILTIN_CELLS.items():
        sizes.setdefault(row, 0)
        if not collapsed.get(key):
            sizes[row] = SHORT_MIN_HEIGHT if row in SHORT_ROWS else PANE_MIN_HEIGHT
    return sizes


def outer_row_minsizes(custom_keys, collapsed) -> dict:
    """Minimum pixel height of each outer row. Pure. Row 0 (the group) sizes
    itself from its inner rows, so it needs no minimum of its own."""
    sizes = {0: 0}
    for key, (row, _col, _span) in pane_cells(custom_keys).items():
        if key in BUILTIN_CELLS:
            continue
        sizes.setdefault(row, 0)
        if not collapsed.get(key):
            sizes[row] = STRATA_MIN_HEIGHT if key == STRATA_KEY else PANE_MIN_HEIGHT
    return sizes


# -- start at login ---------------------------------------------------------------
def startup_folder() -> Path:
    return (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows"
            / "Start Menu" / "Programs" / "Startup")


def startup_script(pythonw, script, workdir) -> str:
    """The .bat placed in the Startup folder. Pure."""
    return ("@echo off\r\n"
            f'cd /d "{workdir}"\r\n'
            f'start "" "{pythonw}" "{script}"\r\n')


def startup_installed() -> bool:
    return IS_WINDOWS and (startup_folder() / STARTUP_NAME).exists()


def set_startup(enabled: bool, script=None) -> None:
    """Add or remove the Startup-folder launcher (Windows only)."""
    if not IS_WINDOWS:
        return
    target = startup_folder() / STARTUP_NAME
    if enabled:
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        script = Path(script or HERE / "astra_console.py").resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(startup_script(str(pythonw if pythonw.exists() else exe),
                                         script, script.parent), encoding="utf-8")
    elif target.exists():
        target.unlink()


# -- output ---------------------------------------------------------------------
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


def strip_ansi(line: str) -> str:
    """Remove terminal colour and cursor codes, which are noise in a pane. Pure."""
    return _ANSI.sub("", line)
