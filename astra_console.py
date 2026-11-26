"""Golurk Console (the Astrabot console): run the bot, site, wiki, absLogin,
classifier, portfolio, the Automated Librarian's journal and the Strata
monitor, plus any services you add, and watch their output.

Every window has an arrow on its header that collapses it, and its own
Restart button. The page scrolls when the window is too short for the
panes' minimum heights. The built-in windows can never be removed. Settings adds your own services
(a program, a PowerShell command or a batch file); those get a pane of
their own with Start, Restart, Stop and Remove, and are saved in console.json
(see console_services.py and console_settings.py).

Run with startConsole.bat. This window owns the bot, wiki backend,
absLogin and classifier processes, so it replaces startBot.bat and any
hand-started server.py, app.py or classifier_service.py: never run those
alongside it. absLogin needs ABS_API_KEY set (e.g. via setx) before this
runs. The classifier runs from the AstraThought checkout with that
repo's venv, so it can load the SetFit model the bot itself cannot.

The audiobook site is hosted by the audiobooks cog, so it only runs
while the bot does: the bot's Restart restarts it too. The Site pane's
Restart reloads that cog in the running bot (through logs/reload_cog.flag, see
cogs/moderation.py) and, like any load of that cog, rebuilds the
requests channel. The portfolio site (13384) is hosted by the portfolio cog
the same way: its pane shows that cog's log lines, and its Restart
reloads just that cog.

The Strata pane embeds the interactive Strata monitor in a pseudo-console
(console_terminal.py) with Start/Stop/Restart through plan-guarded runtime
calls (console_strata.py), and can pop out into its own window.

The right-hand Decisions panel lists the classifier service's final
decisions (most recent first, up to 50), with a search box and an
approve button and a disagree button on each row. It does not poll:
the console watches the classifier's own output, and when the
service logs a new numbered decision the panel fetches the list
once. It also fetches when the classifier starts, and shows "not
running" when it stops. A ruling is saved by the service as a
training label and takes effect at once through the overrides file.

"Pull + Restart" fast-forwards every repo, then restarts the bot,
wiki backend and absLogin. It does not pull AstraThought, so it does
not restart the classifier; use the Classifier pane's Restart for that. If any
pull fails nothing is restarted.

Crash handling matches startBot.bat: restart after 10 seconds, give up
after 5 crashes in a row, stay stopped after a clean exit. Output goes
to logs/bot.log, logs/wiki.log, logs/absLogin.log or
logs/classifier.log, and start/stop lines of all four go to
logs/crashes.log. The wiki backend, absLogin and classifier get the
same crash handling as the bot.
"""

import json
import atexit
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import console_remote
import console_strata
import console_terminal
from console_services import (
    BUILTIN_CELLS,
    BUILTIN_TITLES,
    STRATA_KEY,
    build_saved_config,
    clean_service,
    command_to_argv,
    load_console_config,
    GROUP_KEY,
    GROUP_TITLE,
    inner_row_minsizes,
    inner_row_weights,
    outer_row_minsizes,
    outer_row_weights,
    pane_cells,
    librarian_command,
    save_console_config,
)

ROOT = Path(__file__).resolve().parent
ABSLOGIN_DIR = Path(r"G:\My Drive\codingShit\absLogin")
WIKI_DIR = Path(r"C:\codingShit\Aboard-the-Astra-Wiki")
WIKI_BACKEND = WIKI_DIR / "Tools" / "wiki" / "backend"
CLASSIFIER_DIR = Path(r"C:\codingShit\AstraThought")
CLASSIFIER_SERVICE = CLASSIFIER_DIR / "classifier_service.py"
CLASSIFIER_PYTHON = CLASSIFIER_DIR / ".venv" / "Scripts" / "python.exe"

SITE_PORT = 13380
ABSLOGIN_PORT = 13381
WIKI_PORT = 13382
CLASSIFIER_PORT = 13383
PORTFOLIO_PORT = 13384

MAX_CRASHES = 5
RESTART_DELAY = 10
# A run at least this long breaks a "crashes in a row" streak.
STABLE_SECONDS = 60
MAX_PANE_LINES = 5000

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Every service appends to the same crashes.log.
CRASH_LOG_LOCK = threading.Lock()

LOG_LINE = re.compile(r"^\[[^\]]*\] \[[A-Z]+\s*\] ")
SITE_LINE = re.compile(r"^\[[^\]]*\] \[[A-Z]+\s*\] (aiohttp|audiobooks)\b")
PORTFOLIO_LINE = re.compile(r"^\[[^\]]*\] \[[A-Z]+\s*\] portfolio\b")
CONTINUATION = re.compile(r"^(\s|Traceback |\w+(\.\w+)*(Error|Exception)\b)")


def route_line(line, previous="bot"):
    """Pick the pane for a line. Tracebacks follow the line above them."""
    if SITE_LINE.match(line):
        return "site"
    if PORTFOLIO_LINE.match(line):
        return "portfolio"
    if LOG_LINE.match(line):
        return "bot"
    return previous if CONTINUATION.match(line) else "bot"


def python_exe():
    """The console-mode interpreter, even when running under pythonw."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        return str(exe.with_name("python.exe"))
    return str(exe)


class Supervisor:
    """Runs one program, restarts it, and hands each output line to emit."""

    def __init__(self, emit, name, cwd, command, log_dir,
                 restart_delay=RESTART_DELAY, stable_seconds=STABLE_SECONDS,
                 ports=()):
        self.emit = emit
        # Ports this program listens on. Whatever still holds one when we
        # are about to start it is a leftover, and is stopped first.
        self.ports = tuple(ports)
        self.name = name
        self.cwd = Path(cwd)
        self.command = command
        self.log_dir = Path(log_dir)
        self.restart_delay = restart_delay
        self.stable_seconds = stable_seconds
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._proc = None
        self._want_running = True
        self._intentional = False
        self._closing = False
        self._waiting = False
        self._started = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def title(self):
        return self.name[0].upper() + self.name[1:]

    # -- controls ------------------------------------------------------
    def start(self):
        self._started = True
        self._thread.start()

    def is_lost(self):
        """True when this service was started but its watcher thread died.

        The program it launched may still be running with nothing
        reading its output, and the Restart button refuses to work
        ("was not found at startup"). Only a console restart fixes that.
        """
        return self._started and not self._closing and not self._thread.is_alive()

    def restart(self):
        if self.name != "bot" and not self._thread.is_alive():
            self._say(self.name, f"{self.title} was not found at startup, so it cannot restart.")
            return
        self._say(self.name, f"Restarting {self.name}.")
        with self._lock:
            self._want_running = True
            if self._proc is not None:
                self._intentional = True
                self._stop(self._proc)
        self._wake.set()

    def shutdown(self):
        with self._lock:
            self._closing = True
            self._want_running = False
            if self._proc is not None:
                self._stop(self._proc)
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=10)

    def status(self):
        proc = self._proc
        if proc is not None:
            return f"Running (PID {proc.pid})"
        return "Waiting to restart" if self._waiting else "Stopped"

    # -- internals -----------------------------------------------------
    def _say(self, stream, text):
        self.emit(stream, f"[{datetime.now():%H:%M:%S}] {text}")

    def _record(self, text):
        with CRASH_LOG_LOCK, open(self.log_dir / "crashes.log", "a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")

    @staticmethod
    def _kill(proc):
        # /T also ends anything the bot spawned, which would otherwise
        # hold the output pipe open and stall the restart.
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, creationflags=NO_WINDOW)
        if proc.poll() is None:
            proc.kill()

    def _stop(self, proc):
        """End the program. Subclasses may ask it to stop cleanly first."""
        self._kill(proc)

    def _spawn(self, env):
        """Start the program with its output piped back. Subclasses may change how."""
        return subprocess.Popen(self.command, cwd=self.cwd, env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, encoding="utf-8",
                                errors="replace", creationflags=NO_WINDOW)

    def _before_start(self):
        pass

    def _clear_ports(self):
        """Stop leftovers holding this service's ports, so the start can bind."""
        if not self.ports:
            return
        try:
            killed = kill_listeners(self.ports)
        except Exception as exc:  # never let cleanup stop the start
            self._say(self.name, f"Could not check ports {self.ports}: {exc}")
            return
        if killed:
            self._say(self.name, f"Cleared port {'/'.join(map(str, self.ports))}: "
                                 f"stopped leftover PID {', '.join(map(str, killed))}.")

    def _route(self, line, previous):
        return self.name

    def _retry_delay(self, crashes):
        """Seconds to wait before restart number ``crashes``, or None to give up."""
        return None if crashes >= MAX_CRASHES else self.restart_delay

    def _retry_message(self, crashes, delay):
        return f"Crash {crashes} of {MAX_CRASHES}, restarting in {delay:g} seconds."

    def _run_once(self):
        """Start the program, stream its output until it exits, return its code."""
        self._before_start()
        self._clear_ports()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self._record(f"Starting {self.name}")
        self._say(self.name, f"Starting {self.name}.")
        try:
            proc = self._spawn(env)
        except OSError as exc:
            # A program that cannot start (wrong path, refused login) counts as
            # a crash like any other, with the reason shown in its pane.
            self._say(self.name, f"Could not start {self.title}: {exc}")
            self._record(f"{self.name} could not start: {exc}")
            return 1
        with self._lock:
            # shutdown() may have run between the spawn and here: it saw no
            # process to stop, so this one would be orphaned. Checked under
            # the same lock shutdown() takes, so one of the two always stops it.
            orphan = self._closing
            if orphan:
                self._stop(proc)
            else:
                self._proc = proc
        if orphan:
            proc.wait()
            proc.stdout.close()
            self._say(self.name, f"{self.title} was stopped as it started (closing).")
            return 0
        stream = self.name
        with open(self.log_dir / f"{self.name}.log", "a", encoding="utf-8") as log:
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                log.write(line + "\n")
                log.flush()
                stream = self._route(line, stream)
                self.emit(stream, line)
        code = proc.wait()
        with self._lock:
            self._proc = None
        message = f"{self.title} stopped, exit code {code}"
        self._record(message)
        self._say(self.name, message + ".")
        return code

    def _run(self):
        self.log_dir.mkdir(exist_ok=True)
        crashes = 0
        while not self._closing:
            if not self._want_running:
                self._wake.wait()
                self._wake.clear()
                continue
            started = time.monotonic()
            code = self._run_once()
            with self._lock:
                intentional, self._intentional = self._intentional, False
            if self._closing:
                break
            if intentional:
                crashes = 0
                continue
            if code == 0:
                self._want_running = False
                continue
            if time.monotonic() - started >= self.stable_seconds:
                crashes = 0
            crashes += 1
            delay = self._retry_delay(crashes)
            if delay is None:
                self._want_running = False
                message = (f"{self.title} crashed {crashes} "
                           "times in a row - not restarting.")
                crashes = 0
                self._record(message)
                self._say(self.name, message + " Press Restart when it is fixed.")
                continue
            self._say(self.name, self._retry_message(crashes, delay))
            self._waiting = True
            if self._wake.wait(delay):
                self._wake.clear()
            self._waiting = False


class BotSupervisor(Supervisor):
    """The bot, plus the actions that only make sense for it."""

    def __init__(self, emit, root=ROOT, command=None, **kwargs):
        if command is None:  # a custom command (the tests) never clears the real site port
            kwargs.setdefault("ports", (SITE_PORT, PORTFOLIO_PORT))
        super().__init__(emit, "bot", root, command or [python_exe(), "-u", "bot.py"],
                         Path(root) / "logs", **kwargs)
        self.flag = self.log_dir / "reload_cog.flag"

    def _before_start(self):
        self.flag.unlink(missing_ok=True)

    def _route(self, line, previous):
        return route_line(line, previous)

    def _reload_cog(self, cog, pane, what):
        """Ask the running bot to reload one cog, through the flag file."""
        if self._proc is None:
            self._say(pane, f"Bot is not running, so there is no {what} to reload.")
            return False
        # Written then renamed so the bot can never read a half-made flag.
        pending = self.flag.with_suffix(".tmp")
        pending.write_text(cog, encoding="utf-8")
        pending.replace(self.flag)
        self._say(pane, f"{what.capitalize()} reload requested.")
        return True

    def reload_site(self):
        return self._reload_cog("audiobooks", "site", "site")

    def reload_portfolio(self):
        return self._reload_cog("portfolio", "portfolio", "portfolio")

    def pull_latest(self):
        """Fast-forward every repo. Never merges, so it cannot make a mess."""
        repos = [("Astrabot", self.cwd, ["git", "pull", "--ff-only", "origin", "master"])]
        for name, path in (("absLogin", ABSLOGIN_DIR), ("Wiki", WIKI_DIR)):
            if path.is_dir():
                repos.append((name, path, ["git", "pull", "--ff-only"]))
        ok = True
        for name, cwd, command in repos:
            result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace",
                                    creationflags=NO_WINDOW)
            self._say("bot", f"{name} pull: " + (result.stdout + result.stderr).strip())
            ok = ok and result.returncode == 0
        self._say("bot", "Pull finished." if ok
                  else "Pull failed, see the message above. Nothing was restarted.")
        return ok


def pull_and_restart(bot, services):
    """Restart services only if every pull worked: a bad pull changes nothing."""
    if not bot.pull_latest():
        return False
    for service in services:
        service.restart()
    return True


class CustomSupervisor(Supervisor):
    """A service added in Settings: its own pane with Start, Restart, Stop
    and Remove. Unlike the built-in services it can be stopped and left
    stopped, and can stop cleanly by typing commands into the program."""

    stop_pause = 2  # seconds between typed stop commands

    def __init__(self, emit, spec, log_dir=None):
        self._title = ""
        self._apply(spec)
        super().__init__(emit, self.spec["name"], self.cwd, self.command,
                         log_dir or ROOT / "logs")

    def _apply(self, spec):
        spec = clean_service(spec)
        self.spec = spec
        self._title = spec["title"]
        self.command = command_to_argv(spec["command"], spec["run_as"])
        self.cwd = Path(spec["cwd"] or ROOT)
        self.stop_input = list(spec["stop_input"])
        self.stop_grace = spec["stop_grace"]
        self.autostart = spec["autostart"]
        self.extra_env = {str(k): str(v) for k, v in spec["env"].items()}

    @property
    def title(self):
        return self._title

    def reconfigure(self, spec):
        """New settings; they apply the next time the program starts."""
        self._apply(spec)

    def _spawn(self, env):
        env = dict(env, **self.extra_env)
        return subprocess.Popen(self.command, cwd=self.cwd, env=env,
                                stdin=subprocess.PIPE if self.stop_input else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                encoding="utf-8", errors="replace", creationflags=NO_WINDOW)

    def _stop(self, proc):
        if self.stop_input and proc.stdin is not None:
            try:
                for command in self.stop_input:
                    proc.stdin.write(command + "\n")
                    proc.stdin.flush()
                    time.sleep(self.stop_pause)
                proc.wait(timeout=self.stop_grace)
                return
            except subprocess.TimeoutExpired:
                self._say(self.name, f"{self.title} did not stop within "
                                     f"{self.stop_grace}s, ending it.")
            except (OSError, ValueError) as exc:
                self._say(self.name, f"Clean stop of {self.title} failed ({exc}), ending it.")
        self._kill(proc)

    def start(self):
        if not self.command:
            self._say(self.name, f"No command set for {self.title} (see Settings).")
            return
        if not self._started:
            super().start()
            return
        with self._lock:
            self._want_running = True
        self._wake.set()

    def restart(self):
        if not self._started:
            self.start()
            return
        super().restart()

    def stop(self):
        """Stop it and leave it stopped until Start or Restart."""
        with self._lock:
            self._want_running = False
            if self._proc is not None:
                self._intentional = True
                self._say(self.name, f"Stopping {self.title}.")
                self._stop(self._proc)
        self._wake.set()


# -- watchdog ------------------------------------------------------------
WATCH_INTERVAL_SECONDS = 15
# Do not judge a console that only just opened.
WATCH_GRACE_SECONDS = 120
WATCH_STRIKES = 2
# At most this many watchdog restarts per hour, so a fault that kills a
# watcher every time cannot make the console restart forever.
WATCH_MAX_RESTARTS = 3
WATCH_WINDOW_SECONDS = 3600


class Watchdog:
    """Notices when a service's watcher thread has died, twice in a row.

    Two checks in a row keep one unlucky moment from restarting the
    bot. trip(lost_names) is called once, then the watchdog is done.
    """

    def __init__(self, services, trip, strikes=WATCH_STRIKES):
        self.services = list(services)
        self.trip = trip
        self.strikes = strikes
        self.bad_checks = 0
        self.tripped = False

    def check(self):
        lost = [service.name for service in self.services if service.is_lost()]
        self.bad_checks = self.bad_checks + 1 if lost else 0
        if lost and self.bad_checks >= self.strikes and not self.tripped:
            self.tripped = True
            self.trip(lost)
        return lost


def write_watchdog_alert(flag_path, lost, limit=WATCH_MAX_RESTARTS):
    """Leave a DM for the bot to send (see cogs/moderation.py ALERT_FLAG)."""
    path = Path(flag_path)
    text = (f"Astra Console watchdog gave up. It already restarted the console {limit} "
            f"times in the last hour, and it just lost: {', '.join(lost)}. "
            "It will not restart the console again until you reopen it. "
            "Look at logs/crashes.log on the host.")
    pending = path.with_suffix(".tmp")
    pending.write_text(text, encoding="utf-8")
    pending.replace(path)  # renamed so the bot never reads half a message


def restart_allowed(history_path, now=None, limit=WATCH_MAX_RESTARTS,
                    window=WATCH_WINDOW_SECONDS, record=False):
    """True while fewer than ``limit`` watchdog restarts happened in ``window`` seconds.

    The restart times live in a small JSON file so the budget survives
    the restart itself. With record=True the current time is added.
    """
    now = time.time() if now is None else now
    path = Path(history_path)
    try:
        times = [float(t) for t in json.loads(path.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError):
        times = []
    times = [t for t in times if now - t < window]
    allowed = len(times) < limit
    if record and allowed:
        times.append(now)
    try:
        path.write_text(json.dumps(times), encoding="utf-8")
    except OSError:
        pass
    return allowed


def listening_pids(netstat_output, ports):
    """PIDs listening on the given local ports (any local address)."""
    wanted = {str(port) for port in ports}
    pids = set()
    for line in netstat_output.splitlines():
        parts = line.split()
        if (len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING"
                and parts[1].rsplit(":", 1)[-1] in wanted and parts[4].isdigit()):
            pids.add(int(parts[4]))
    return sorted(pids)


def kill_listeners(ports):
    """Stop anything listening on these ports; return the PIDs stopped.

    Never touches this console's own process.
    """
    result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                            creationflags=NO_WINDOW)
    pids = [pid for pid in listening_pids(result.stdout, ports) if pid != os.getpid()]
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, creationflags=NO_WINDOW)
    return pids


def relaunch_command(executable=None, script=None, delay=5):
    """A command line that waits for this console to exit, then opens a new one.

    The new console starts with the watchdog already on.
    """
    executable = executable or sys.executable
    script = script or str(ROOT / "astra_console.py")
    return (f'cmd /c "ping -n {delay + 1} 127.0.0.1 >nul & '
            f'"{executable}" "{script}" --watchdog"')


# -- classifier decisions (the right-hand panel) -----------------------
# Same env var as the tasks cog, so one setting covers both.
CLASSIFIER_URL = os.environ.get(
    "TASKS_CLASSIFIER_URL", "http://127.0.0.1:13383").rstrip("/")
DECISIONS_LIMIT = 50


# The classifier logs with a timestamp and level in front:
#   2026-11-10 00:02:07,325 INFO   #1 [review 0.76 model_regex_disagreement] text
# The old pattern expected the line to start with "#1 [", which no real
# line does, so the box was never refreshed by a decision.
DECISION_LINE = re.compile(r"(?:^|\s)#\d+ \[(?:auto_task|auto_drop|review)\b")
CLASSIFIER_READY_TEXT = "Classifier service listening"


def should_fetch_decisions(line):
    """True for a line that means the classifier's decision list changed
    or the service just became reachable."""
    return bool(DECISION_LINE.search(line)
                or "Starting classifier" in line
                or CLASSIFIER_READY_TEXT in line)


def fetch_decisions(base_url=CLASSIFIER_URL, timeout=2):
    """GET /decisions from the classifier service.

    Returns the decisions list (most recent first, up to 50), an
    empty list when the service is up with nothing yet, or None when
    the service cannot be reached. Never raises.
    """
    try:
        with urllib.request.urlopen(base_url + "/decisions",
                                    timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    decisions = payload.get("decisions")
    return decisions if isinstance(decisions, list) else None


def rule_decision(base_url, entry_id, agree, timeout=5):
    """POST a ruling for one decision. Returns (ok, message). Never raises."""
    try:
        request = urllib.request.Request(
            f"{base_url}/decisions/{entry_id}/rule",
            data=json.dumps({"agree": bool(agree)}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return False, f"Could not reach the classifier service: {exc}"
    if isinstance(payload, dict) and payload.get("ok"):
        return True, str(payload.get("message") or "Recorded.")
    error = payload.get("error") if isinstance(payload, dict) else None
    return False, str(error or "Classifier service refused the ruling.")


def filter_decisions(decisions, query):
    """Decisions whose text matches the search, case-insensitive."""
    query = (query or "").strip().lower()
    if not query:
        return list(decisions)
    return [d for d in decisions if query in str(d.get("text", "")).lower()]


def decision_summary(decision):
    """One short line for a decision: id, verdict, score, trigger, lean."""
    label = decision.get("label")
    if decision.get("decision") == "review":
        verdict = "leans task" if label == 1 else "leans not a task"
    else:
        verdict = "says task" if label == 1 else "says not a task"
    return (f"#{decision.get('id')} - {decision.get('decision')} - "
            f"{float(decision.get('score') or 0):.0%} - "
            f"{decision.get('trigger')} - {verdict}")


def wiki_supervisor(emit):
    return Supervisor(emit, "wiki", WIKI_BACKEND, [python_exe(), "-u", "server.py"],
                      ROOT / "logs", ports=(WIKI_PORT,))


def abslogin_supervisor(emit):
    return Supervisor(emit, "absLogin", ABSLOGIN_DIR, [python_exe(), "-u", "app.py"],
                      ROOT / "logs", ports=(ABSLOGIN_PORT,))


class LibrarianSupervisor(Supervisor):
    """The Automated Librarian's journal, followed over SSH (2026-11-22).

    The librarian runs on the Ubuntu server, not here, so this does not run
    or restart the librarian itself: it only shows its output. Restart here
    reconnects. The connection drops whenever the server reboots or the
    network blips, so its retry policy differs from a local program's."""

    def __init__(self, emit, command, log_dir=ROOT / "logs", **kwargs):
        super().__init__(emit, "librarian", ROOT, command, log_dir, **kwargs)

    # Reconnect waits double from restart_delay up to this cap, forever.
    MAX_RETRY_DELAY = 300

    def _retry_delay(self, crashes):
        """10 s, 20 s, 40 s ... capped at 5 minutes, and never give up.

        A dropped connection is almost always the server rebooting or the
        network blipping, so the pane should come back by itself. Backing
        off keeps a real fault (a broken key, the server off for the
        night) to a dozen cheap attempts an hour, and each failure's
        reason stays visible in the pane."""
        return min(self.restart_delay * 2 ** (crashes - 1), self.MAX_RETRY_DELAY)

    def _retry_message(self, crashes, delay):
        return (f"Lost the connection to the librarian ({crashes} in a row), "
                f"reconnecting in {delay:g} seconds.")


def classifier_supervisor(emit):
    """The classifier runs from the AstraThought checkout, with that
    repo's venv (torch lives there, not in the bot's Python), and from
    that directory so ./tasks-AstraThought-v2 resolves."""
    return Supervisor(emit, "classifier", CLASSIFIER_DIR,
                      [str(CLASSIFIER_PYTHON), "-u", "classifier_service.py"],
                      ROOT / "logs", ports=(CLASSIFIER_PORT,))


def main():
    import tkinter as tk
    from tkinter import messagebox, ttk

    # Material Design dark theme, same tokens as the audiobook
    # requests site (cogs/audiobooks_web.py): background #141218,
    # card #1e1b24, text #e6e0e9, primary #d0bcff on #381e72,
    # input #2b2930 with #938f99 outline, secondary text #cac4d0,
    # chip #4a4458, error #b3261e, dark green #274d27.
    bg, card, text_colour, accent = "#141218", "#1e1b24", "#e6e0e9", "#d0bcff"
    topbar, input_bg, outline = "#2b2930", "#2b2930", "#938f99"
    secondary, on_primary, chip_bg = "#cac4d0", "#381e72", "#4a4458"
    error, ok_dark = "#b3261e", "#274d27"
    # Every (stream, line) also goes to the remote feed's hub, from whatever
    # thread put it (console_remote.py); the hub never touches Tk.
    remote_hub = console_remote.Hub()
    lines = console_remote.TapQueue(remote_hub.publish_item)
    cfg, cfg_note = load_console_config()

    root = tk.Tk()
    root.title(cfg["console_name"])
    root.geometry("1400x900")
    root.configure(bg=bg)

    def emit(stream, line):
        lines.put((stream, line))

    # Dark, arrowless scrollbars in the theme's colours. The native Windows
    # scrollbar ignores colours, so these are ttk ones on the "clam" theme
    # (nothing else in the console uses ttk, so the theme change is safe).
    style = ttk.Style(root)
    style.theme_use("clam")
    style.layout("Dark.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {
        "sticky": "ns", "children": [("Vertical.Scrollbar.thumb",
                                      {"expand": "1", "sticky": "nswe"})]})])
    style.configure("Dark.Vertical.TScrollbar", background=chip_bg, troughcolor=bg,
                    bordercolor=bg, lightcolor=chip_bg, darkcolor=chip_bg, arrowsize=10,
                    gripcount=0, relief="flat")
    style.map("Dark.Vertical.TScrollbar", background=[("pressed", accent), ("active", outline)])

    def vscroll(parent, command):
        extra = {"command": command} if command else {}
        return ttk.Scrollbar(parent, orient="vertical", **extra,
                             style="Dark.Vertical.TScrollbar")

    supervisor = BotSupervisor(emit)
    wiki = wiki_supervisor(emit)
    abs_login = abslogin_supervisor(emit)
    classifier = classifier_supervisor(emit)
    librarian_cmd = librarian_command(cfg["librarian"])
    librarian = LibrarianSupervisor(emit, librarian_cmd or ["ssh"])
    extras = [(wiki, WIKI_BACKEND / "server.py"), (abs_login, ABSLOGIN_DIR / "app.py")]
    services = [supervisor] + [service for service, path in extras if path.is_file()]

    def in_background(work):
        threading.Thread(target=work, daemon=True).start()

    def pull():
        pull_button.configure(state="disabled")

        def work():
            try:
                pull_and_restart(supervisor, services)
            except Exception as exc:  # e.g. git not on PATH
                lines.put(("bot", f"Pull failed: {exc}"))
            root.after(0, lambda: pull_button.configure(state="normal"))

        in_background(work)

    bar = tk.Frame(root, bg=topbar)
    bar.pack(fill="x", padx=0, pady=0)

    def button(label, command):
        widget = tk.Button(bar, text=label, command=command, bg=accent, fg=on_primary,
                           activebackground=text_colour, relief="flat",
                           padx=14, pady=6, font=("Segoe UI", 10, "bold"))
        widget.pack(side="left", padx=(10, 0), pady=10)
        return widget

    # Each service's Restart sits on its own pane's header (2026-11-25); the
    # top bar keeps only what acts on the whole console.
    watchdog_button = button("Watchdog: Off", lambda: toggle_watchdog())
    pull_button = button("Pull + Restart", pull)
    button("Settings", lambda: open_settings_window())
    status = tk.Label(bar, bg=topbar, fg=text_colour, font=("Segoe UI", 10))
    status.pack(side="right", padx=10, pady=10)

    body = tk.Frame(root, bg=bg)
    body.pack(fill="both", expand=True)
    # The pane area scrolls: the panes live in `grid`, a frame inside a
    # canvas. The frame is always at least as tall as the canvas, so a tall
    # window stretches the rows as before; when the rows' minimum heights
    # (console_services.*_row_minsizes) need more, the page scrolls and the
    # scrollbar appears.
    page = tk.Frame(body, bg=bg)
    page.pack(side="left", fill="both", expand=True, padx=6, pady=(0, 6))
    page.rowconfigure(0, weight=1)
    page.columnconfigure(0, weight=1)
    page_canvas = tk.Canvas(page, bg=bg, highlightthickness=0, bd=0, yscrollincrement=40)
    page_canvas.grid(row=0, column=0, sticky="nsew")
    page_scroll = vscroll(page, page_canvas.yview)
    page_canvas.configure(yscrollcommand=page_scroll.set)
    grid = tk.Frame(page_canvas, bg=bg)
    page_window = page_canvas.create_window((0, 0), window=grid, anchor="nw")
    page_state = {"scrolls": False, "job": None}

    def fit_page(_event=None):
        page_state["job"] = None
        # Tk works out requested sizes in its own idle pass, which can run
        # after this one: without this, expanding a pane read the old (smaller)
        # height and the bottom of the Astrabot group was cut off.
        grid.update_idletasks()
        width, height = page_canvas.winfo_width(), page_canvas.winfo_height()
        need = grid.winfo_reqheight()
        tall = max(height, need)
        page_canvas.itemconfigure(page_window, width=width, height=tall)
        page_canvas.configure(scrollregion=(0, 0, width, tall))
        scrolls = need > height + 1
        if scrolls != page_state["scrolls"]:
            page_state["scrolls"] = scrolls
            if scrolls:
                page_scroll.grid(row=0, column=1, sticky="ns", padx=(4, 0))
            else:
                page_scroll.grid_remove()
                page_canvas.yview_moveto(0)

    def refit_page():
        if page_state["job"] is None:
            page_state["job"] = root.after_idle(fit_page)

    page_canvas.bind("<Configure>", fit_page)

    def scroll_page(steps):
        if page_state["scrolls"]:
            page_canvas.yview_scroll(steps, "units")

    def wheel_steps(event):
        return -1 if event.delta > 0 else 1

    def on_page_wheel(event):
        """The wheel scrolls the page anywhere in the main window, except
        over the Decisions list and the Strata monitor, which take it
        themselves."""
        widget = event.widget
        if isinstance(widget, str) or widget.winfo_toplevel() is not root:
            return
        if str(widget).startswith(str(sidebar)) or isinstance(widget, tk.Text):
            return
        scroll_page(wheel_steps(event))

    def on_box_wheel(event, box):
        """A log pane scrolls itself until it reaches its top or bottom, then
        hands the wheel to the page, like a browser."""
        steps = wheel_steps(event)
        top, bottom = box.yview()
        if (steps < 0 and top > 0) or (steps > 0 and bottom < 1):
            box.yview_scroll(steps * 3, "units")
        else:
            scroll_page(steps)
        return "break"

    root.bind_all("<MouseWheel>", on_page_wheel, add="+")
    grid.columnconfigure(0, weight=1, uniform="pane")
    grid.columnconfigure(1, weight=1, uniform="pane")

    # Every window is a pane with a header; the arrow collapses it. The five
    # built-in windows cannot be removed. Services added in Settings (the
    # `custom` dict, in the order added) can be.
    panes = {}
    custom = {}
    boxes = {}
    # The Strata monitor's own window while it is popped out (see pop_out).
    popout = {"window": None, "view": None}
    grid_rows = {"used": 0}
    arrow_open, arrow_shut = "\u25be ", "\u25b8 "

    # The five built-in windows share one collapsible "Astrabot" group, with
    # a grid of their own inside it. Added services sit below the group.
    group = tk.Frame(grid, bg=bg)
    group_head = tk.Frame(group, bg=bg)
    group_head.pack(fill="x")
    group_toggle = tk.Button(group_head, text="", anchor="w", relief="flat", bd=0, bg=bg,
                             fg=accent, activebackground=bg, activeforeground=text_colour,
                             cursor="hand2", font=("Segoe UI", 11, "bold"),
                             command=lambda: toggle_group())
    group_toggle.pack(side="left")
    group_content = tk.Frame(group, bg=bg)
    inner_grid = tk.Frame(group_content, bg=bg)
    inner_grid.pack(fill="both", expand=True)
    inner_grid.columnconfigure(0, weight=1, uniform="pane")
    inner_grid.columnconfigure(1, weight=1, uniform="pane")

    def show_group_collapsed():
        collapsed = bool(cfg["collapsed"].get(GROUP_KEY))
        if collapsed:
            group_content.pack_forget()
        else:
            group_content.pack(fill="both", expand=True)
        group_toggle.configure(text=(arrow_shut if collapsed else arrow_open) + GROUP_TITLE)

    def toggle_group():
        if cfg["collapsed"].get(GROUP_KEY):
            cfg["collapsed"].pop(GROUP_KEY)
        else:
            cfg["collapsed"][GROUP_KEY] = True
        show_group_collapsed()
        place_panes()
        save_settings_quietly()

    def save_settings_quietly():
        try:
            save_console_config(cfg)
        except OSError as exc:
            lines.put(("bot", f"Could not save console.json: {exc}"))

    def place_panes():
        cells = pane_cells(list(custom))
        shut = layout_collapsed()
        inner = inner_row_weights(shut)
        outer = outer_row_weights(list(custom), shut)
        group.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=4, pady=(4, 0))
        for key, entry in panes.items():
            row, column, span = cells[key]
            entry["frame"].grid(row=row, column=column, columnspan=span,
                                sticky="nsew", padx=4, pady=4)
        inner_min = inner_row_minsizes(shut)
        outer_min = outer_row_minsizes(list(custom), shut)
        for row, weight in inner.items():
            inner_grid.rowconfigure(row, weight=weight, minsize=inner_min.get(row, 0))
        total = max(outer) + 1
        for row in range(max(grid_rows["used"], total)):
            grid.rowconfigure(row, weight=outer.get(row, 0), minsize=outer_min.get(row, 0))
        grid_rows["used"] = total
        refit_page()

    def layout_collapsed():
        """cfg["collapsed"], plus the Strata pane while its monitor is popped
        out: the pane folds to its header so it gives its room back."""
        shut = dict(cfg["collapsed"])
        if popout["window"] is not None:
            shut[STRATA_KEY] = True
        return shut

    def show_collapsed(key):
        entry = panes[key]
        collapsed = bool(layout_collapsed().get(key))
        if collapsed:
            entry["content"].pack_forget()
        else:
            entry["content"].pack(fill="both", expand=True)
        title = entry["title"]
        if key == STRATA_KEY and popout["window"] is not None:
            title += "  (popped out)"
        entry["toggle"].configure(text=(arrow_shut if collapsed else arrow_open) + title)

    def toggle_pane(key):
        if cfg["collapsed"].get(key):
            cfg["collapsed"].pop(key)
        else:
            cfg["collapsed"][key] = True
        show_collapsed(key)
        place_panes()
        save_settings_quietly()

    def pane_frame(key, title, buttons=(), parent=None):
        """A pane's frame and header (arrow + title, buttons on the right).
        Buttons are listed right to left. Returns (frame, head, content)."""
        frame = tk.Frame(parent or grid, bg=bg)
        head = tk.Frame(frame, bg=bg)
        head.pack(fill="x")
        toggle = tk.Button(head, text="", anchor="w", relief="flat", bd=0, bg=bg, fg=accent,
                           activebackground=bg, activeforeground=text_colour, cursor="hand2",
                           font=("Segoe UI", 10, "bold"), command=lambda: toggle_pane(key))
        toggle.pack(side="left")
        for label, command in buttons:
            tk.Button(head, text=label, command=command, bg=chip_bg, fg=text_colour,
                      activebackground=card, relief="flat", font=("Segoe UI", 9),
                      padx=8).pack(side="right", padx=(4, 0))
        content = tk.Frame(frame, bg=bg)
        panes[key] = {"frame": frame, "content": content, "toggle": toggle, "title": title}
        return frame, head, content

    def pane(key, title, buttons=(), parent=None):
        _frame, _head, content = pane_frame(key, title, buttons, parent)
        scroll = vscroll(content, None)
        scroll.pack(side="right", fill="y")
        box = tk.Text(content, bg=card, fg=text_colour, insertbackground=text_colour,
                      relief="flat", wrap="word", state="disabled", width=1, height=1,
                      font=("Consolas", 10), yscrollcommand=scroll.set)
        box.pack(side="left", fill="both", expand=True)
        scroll.configure(command=box.yview)
        box.bind("<MouseWheel>", lambda e, b=box: on_box_wheel(e, b))
        boxes[key] = box
        show_collapsed(key)
        return box

    def add_custom(spec, start):
        svc = CustomSupervisor(emit, spec)
        custom[svc.name] = svc
        pane(svc.name, svc.title, buttons=[
            ("Remove", lambda n=svc.name: remove_custom(n)),
            ("Stop", lambda s=svc: in_background(s.stop)),
            ("Restart", lambda s=svc: in_background(s.restart)),
            ("Start", lambda s=svc: in_background(s.start))])
        place_panes()
        if start and svc.autostart:
            svc.start()
        return svc

    def retire_custom(name):
        """Close a service's window and stop it. Does not touch console.json."""
        svc = custom.pop(name, None)
        if svc is None:
            return
        entry = panes.pop(name)
        entry["frame"].destroy()
        boxes.pop(name, None)
        cfg["collapsed"].pop(name, None)
        place_panes()
        in_background(svc.shutdown)

    def remove_custom(name):
        svc = custom.get(name)
        if svc is None or not messagebox.askyesno(
                "Remove", f"Remove {svc.title}? It will be stopped and its window closed."):
            return
        cfg["services"] = [s for s in cfg["services"] if s["name"] != name]
        retire_custom(name)
        save_settings_quietly()

    # Bot first, then the servers in port order. The classifier gets a
    # shorter row of its own at the bottom: its log is the thought
    # process, worth watching, but quieter than the bot.
    restart_actions = {
        "bot": lambda: in_background(supervisor.restart),
        "site": supervisor.reload_site,
        "absLogin": lambda: in_background(abs_login.restart),
        "wiki": lambda: in_background(wiki.restart),
        "classifier": lambda: in_background(classifier.restart),
        "portfolio": supervisor.reload_portfolio,
        "librarian": lambda: in_background(librarian.restart),
    }
    for key in BUILTIN_CELLS:
        pane(key, BUILTIN_TITLES[key], buttons=[("Restart", restart_actions[key])],
             parent=inner_grid)
    show_group_collapsed()

    # -- Strata pane: the real Strata monitor in an embedded terminal ----
    # (console_terminal.py), with Start / Stop / Restart for the server
    # itself (console_strata.py). The server is not the console's child:
    # closing the console closes the monitor and leaves Strata running.
    strata_cfg = cfg["strata"]
    strata_state = {"busy": None, "view": None}
    strata_log = ROOT / "logs" / "strata.log"

    def strata_say(text):
        line = f"[{datetime.now():%H:%M:%S}] {text}"
        try:
            (ROOT / "logs").mkdir(exist_ok=True)
            with open(strata_log, "a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now():%Y-%m-%d} {line}\n")
        except OSError:
            pass
        lines.put((STRATA_KEY, line))

    strata = console_strata.StrataControl(strata_cfg["dir"], strata_cfg["profile"], strata_say)
    # The model may run on another PC (strata.ssh): the monitor then watches
    # it through an SSH tunnel, and Start / Stop / Restart (this PC only) are
    # left off the pane.
    strata_remote = console_strata.remote_of(strata_cfg)
    tunnel = (console_strata.Tunnel(strata_remote["host"], strata_remote["port"],
                                    strata_remote["tunnel_port"],
                                    log_path=ROOT / "logs" / "strata_tunnel.log")
              if strata_remote else None)
    strata_title = (f"Strata ({strata_remote['host']})" if strata_remote
                    else BUILTIN_TITLES[STRATA_KEY])
    if tunnel is not None:
        # Also when the console ends some other way than on_close; a tunnel
        # left by a hard crash is reused by the next console (Tunnel.ensure).
        atexit.register(tunnel.stop)

    def make_monitor(rows, cols):
        url = tunnel.url if tunnel is not None else None
        session = console_terminal.TerminalSession(
            console_strata.monitor_argv(strata_cfg["dir"], url), strata_cfg["dir"], rows, cols,
            on_exit=lambda: lines.put(("__monitor_ended__", None)))
        session.start()
        remote_hub.set_screen(STRATA_KEY, session, strata_title)  # the phone's copy
        return session

    def monitor_view():
        """The view showing the monitor: the pop-out window's when open."""
        return popout["view"] or strata_state["view"]

    def open_monitor(tunnel_ready=False):
        view = monitor_view()
        if view is None:
            return
        if tunnel is not None and not tunnel_ready:
            # Open (or check) the tunnel off the Tk thread, then come back.
            if not view.running:
                view.show_message(f"Opening the SSH tunnel to {strata_remote['host']}...")

            def connect():
                try:
                    tunnel.ensure()
                except Exception as exc:
                    strata_say(str(exc))
                    lines.put(("__monitor_tunnel_failed__", str(exc)))
                    return
                lines.put(("__monitor_tunnel__", None))

            in_background(connect)
            return
        try:
            view.start()  # replaces any monitor still open
            view.text.focus_set()
        except Exception as exc:
            view.stop()
            view.show_message(f"Could not open the Strata monitor: {exc}")

    def strata_action(name, work, reopen):
        if strata_state["busy"]:
            strata_say(f"Still busy with {strata_state['busy']}; try again when it finishes.")
            return
        strata_state["busy"] = name

        def run():
            try:
                work()
            except Exception as exc:
                strata_say(f"{name} failed: {exc}")
            finally:
                strata_state["busy"] = None
            if reopen:
                lines.put(("__monitor_reopen__", None))

        in_background(run)

    def strata_stop():
        if messagebox.askyesno("Stop Strata", "Stop the Strata server? The Home Assistant "
                                              "agent has no model until it starts again."):
            strata_action("Stop", strata.stop, reopen=False)

    def pop_out():
        """Move the running monitor into a window of its own. The program
        keeps running: only the view drawing it changes."""
        docked = strata_state["view"]
        if docked is None:
            return
        if popout["window"] is not None:
            popout["window"].lift()
            popout["window"].focus_force()
            return
        win = tk.Toplevel(root, bg=bg)
        win.title(f"Strata monitor - {cfg['console_name']}")
        win.geometry("1180x760")
        win.minsize(500, 300)
        bar = tk.Frame(win, bg=topbar)
        bar.pack(fill="x")
        tk.Label(bar, text=strata_title, bg=topbar, fg=accent,
                 font=("Segoe UI", 11, "bold")).pack(side="left", padx=12, pady=8)
        tk.Button(bar, text="Reattach", command=reattach, bg=accent, fg=on_primary,
                  activebackground=text_colour, relief="flat", padx=14, pady=4,
                  font=("Segoe UI", 10, "bold")).pack(side="right", padx=(6, 10), pady=8)
        for label, command in strata_buttons[1:]:  # Monitor, Stop, Restart, Start
            tk.Button(bar, text=label, command=command, bg=chip_bg, fg=text_colour,
                      activebackground=card, relief="flat", font=("Segoe UI", 9),
                      padx=8).pack(side="right", padx=(4, 0), pady=8)
        note = tk.Label(bar, text="", bg=topbar, fg=secondary, anchor="w",
                        font=("Segoe UI", 9))
        note.pack(side="left", fill="x", expand=True, padx=(4, 0))
        holder = tk.Frame(win, bg=bg)
        holder.pack(fill="both", expand=True, padx=6, pady=6)
        view = console_terminal.TerminalView(holder, make_monitor)
        popout.update(window=win, view=view, note=note)
        # Closing the window is the same as Reattach, never "stop the monitor".
        win.protocol("WM_DELETE_WINDOW", reattach)
        session = docked.detach()
        if session is not None and session.alive:
            view.attach(session)
            remote_hub.screen_moved(STRATA_KEY)
        else:
            win.after(300, open_monitor)
        view.text.focus_set()
        show_collapsed(STRATA_KEY)
        place_panes()

    def reattach():
        """Put the monitor back in its pane and close the pop-out window."""
        win, view = popout["window"], popout["view"]
        if win is None:
            return
        session = view.detach()
        popout.update(window=None, view=None, note=None)
        win.destroy()
        show_collapsed(STRATA_KEY)
        place_panes()
        docked = strata_state["view"]
        if session is not None and session.alive:
            docked.attach(session)
            remote_hub.screen_moved(STRATA_KEY)
        elif session is not None:
            session.close()
        if not docked.running:
            root.after(300, open_monitor)

    strata_buttons = [("Pop out", pop_out),
                      ("Monitor", open_monitor)]
    if tunnel is None:  # they act on this PC's Strata only
        strata_buttons += [("Stop", strata_stop),
                           ("Restart", lambda: strata_action("Restart", strata.restart, True)),
                           ("Start", lambda: strata_action("Start", strata.start, True))]
    _sframe, strata_head, strata_content = pane_frame(STRATA_KEY, strata_title, strata_buttons)
    strata_note = tk.Label(strata_head, text="", bg=bg, fg=secondary, anchor="w",
                           font=("Segoe UI", 9))
    strata_note.pack(side="left", padx=(10, 0), fill="x", expand=True)
    strata_missing = (console_terminal.available()
                      or ("" if console_strata.is_installed(strata_cfg["dir"]) else
                          f"Strata not found in {strata_cfg['dir'] or '(no folder set)'}; "
                          "set strata.dir in console.json."))
    remote_hub.set_screen(STRATA_KEY, None, strata_title,
                          strata_missing or "The monitor hasn't opened yet.")
    if strata_missing:
        tk.Label(strata_content, text=strata_missing, bg=card, fg=secondary, anchor="nw",
                 justify="left", font=("Segoe UI", 10), wraplength=900).pack(
                     fill="both", expand=True)
    else:
        strata_state["view"] = console_terminal.TerminalView(strata_content, make_monitor)
    show_collapsed(STRATA_KEY)
    for spec in cfg["services"]:
        add_custom(spec, start=False)
    place_panes()

    # -- decisions panel (right-hand sidebar) --------------------------
    # Most recent first, up to 50, with a search box. Each row is a
    # card with the decision text, a chip with id / verdict / score /
    # trigger / lean, and an approve button (check mark) and a
    # disagree button (X). A ruling is saved by the classifier
    # service as a training label at once.
    sidebar = tk.Frame(body, bg=bg, width=400)
    sidebar.pack(side="right", fill="y", padx=(0, 6), pady=(0, 6))
    sidebar.pack_propagate(False)
    decisions_toggle = tk.Button(sidebar, text="", anchor="w", relief="flat", bd=0, bg=bg,
                                 fg=accent, activebackground=bg, activeforeground=text_colour,
                                 cursor="hand2", font=("Segoe UI", 10, "bold"),
                                 command=lambda: toggle_decisions())
    decisions_toggle.pack(fill="x", pady=(4, 2))
    decisions_body = tk.Frame(sidebar, bg=bg)
    decisions_body.pack(fill="both", expand=True)

    def show_decisions_collapsed():
        collapsed = bool(cfg["collapsed"].get("decisions"))
        if collapsed:
            decisions_body.pack_forget()
        else:
            decisions_body.pack(fill="both", expand=True)
        sidebar.configure(width=150 if collapsed else 400)
        decisions_toggle.configure(text=(arrow_shut if collapsed else arrow_open) + "Decisions")

    def toggle_decisions():
        if cfg["collapsed"].get("decisions"):
            cfg["collapsed"].pop("decisions")
        else:
            cfg["collapsed"]["decisions"] = True
        show_decisions_collapsed()
        save_settings_quietly()

    decisions_status = tk.Label(decisions_body, text="Classifier: checking...",
                                bg=bg, fg=secondary, anchor="w",
                                font=("Segoe UI", 9))
    decisions_status.pack(fill="x")

    search_row = tk.Frame(decisions_body, bg=bg)
    search_row.pack(fill="x", pady=(6, 4))
    search_entry = tk.Entry(search_row, bg=input_bg, fg=text_colour,
                            insertbackground=text_colour, relief="flat",
                            highlightthickness=1, highlightbackground=outline,
                            highlightcolor=accent, font=("Segoe UI", 10))
    search_entry.pack(side="left", fill="x", expand=True, ipady=4)
    search_entry.insert(0, "Search decisions...")
    search_entry.configure(fg=outline)

    def clear_search():
        search_entry.delete(0, "end")
        search_entry.configure(fg=text_colour)
        render_decisions()

    tk.Button(search_row, text="\u00d7", command=clear_search,
              bg=chip_bg, fg=text_colour, relief="flat",
              font=("Segoe UI", 10, "bold")).pack(side="left", padx=(6, 0))

    note = tk.Label(decisions_body, text="", bg=bg, fg=secondary, anchor="w",
                    font=("Segoe UI", 9), wraplength=380, justify="left")
    note.pack(fill="x")

    list_canvas = tk.Canvas(decisions_body, bg=bg, highlightthickness=0)
    list_scroll = vscroll(decisions_body, list_canvas.yview)
    list_scroll.pack(side="right", fill="y")
    list_canvas.pack(side="left", fill="both", expand=True)
    list_canvas.configure(yscrollcommand=list_scroll.set)
    list_frame = tk.Frame(list_canvas, bg=bg)
    list_window = list_canvas.create_window((0, 0), window=list_frame, anchor="nw")
    list_frame.bind("<Configure>",
                    lambda e: list_canvas.configure(scrollregion=list_canvas.bbox("all")))
    list_canvas.bind("<Configure>",
                     lambda e: list_canvas.itemconfigure(list_window, width=e.width))

    def scroll_decisions(event):
        steps = -1 if event.delta > 0 else 1
        list_canvas.yview_scroll(steps, "units")
        return "break"

    def bind_wheel(widget):
        widget.bind("<MouseWheel>", scroll_decisions)
        for child in widget.winfo_children():
            bind_wheel(child)

    show_decisions_collapsed()
    decisions_state = {"snapshot": None, "query": "",
                       "render_key": None, "ruling": set()}

    def render_decisions():
        snapshot = decisions_state["snapshot"]
        query = decisions_state["query"]
        if snapshot is None:
            visible = None
        else:
            visible = filter_decisions(snapshot, query)
        key = (None if snapshot is None else
               tuple((d.get("id"), d.get("ruled_label")) for d in visible),
               query)
        if key == decisions_state["render_key"]:
            return
        decisions_state["render_key"] = key
        if snapshot is None:
            decisions_status.configure(text="Classifier: not running")
        else:
            decisions_status.configure(
                text=f"Classifier: running - {len(snapshot)} decision(s)")
        for child in list_frame.winfo_children():
            child.destroy()
        if snapshot is None:
            tk.Label(list_frame,
                     text="Classifier service is not running.\nStart "
                          "classifier_service.py and decisions will "
                          "appear here.",
                     bg=bg, fg=secondary, font=("Segoe UI", 10),
                     wraplength=360, justify="left", anchor="w").pack(
                         fill="x", padx=6, pady=8)
        elif not snapshot:
            tk.Label(list_frame, text="No decisions yet. They appear here "
                                      "as the bot classifies messages.",
                     bg=bg, fg=secondary, font=("Segoe UI", 10),
                     wraplength=360, justify="left", anchor="w").pack(
                         fill="x", padx=6, pady=8)
        elif not visible:
            tk.Label(list_frame, text=f"No decisions match '{query}'.",
                     bg=bg, fg=secondary, font=("Segoe UI", 10),
                     wraplength=360, justify="left", anchor="w").pack(
                         fill="x", padx=6, pady=8)
        else:
            for decision in visible:
                entry_id = decision.get("id")
                ruled = decision.get("ruled_label")
                card_frame = tk.Frame(list_frame, bg=card)
                card_frame.pack(fill="x", padx=6, pady=4)
                inner = tk.Frame(card_frame, bg=card)
                inner.pack(fill="x", padx=10, pady=8)
                tk.Label(inner, text=str(decision.get("text", "")),
                         bg=card, fg=text_colour, font=("Segoe UI", 10),
                         wraplength=340, justify="left", anchor="w").pack(
                             fill="x")
                tk.Label(inner, text=decision_summary(decision),
                         bg=chip_bg, fg=text_colour, font=("Segoe UI", 9),
                         padx=6, pady=2).pack(anchor="w", pady=(6, 0))
                if decision.get("reason"):
                    tk.Label(inner, text=str(decision.get("reason")),
                             bg=card, fg=secondary, font=("Segoe UI", 9),
                             wraplength=340, justify="left", anchor="w").pack(
                                 fill="x", pady=(4, 0))
                buttons = tk.Frame(inner, bg=card)
                buttons.pack(fill="x", pady=(8, 0))
                if ruled is None:
                    approve = tk.Button(buttons, text="\u2713",
                                        bg=ok_dark, fg="#ffffff", relief="flat",
                                        font=("Segoe UI", 11, "bold"), width=3)
                    disagree = tk.Button(buttons, text="\u2715",
                                         bg=error, fg="#ffffff", relief="flat",
                                         font=("Segoe UI", 11, "bold"), width=3)
                    approve.pack(side="left", padx=(0, 6))
                    disagree.pack(side="left")
                    if entry_id in decisions_state["ruling"]:
                        approve.configure(state="disabled")
                        disagree.configure(state="disabled")

                    def rule_clicked(entry_id=entry_id, agree=True,
                                     approve=approve, disagree=disagree):
                        approve.configure(state="disabled")
                        disagree.configure(state="disabled")
                        decisions_state["ruling"].add(entry_id)

                        def work():
                            ok, message = rule_decision(
                                CLASSIFIER_URL, entry_id, agree)
                            lines.put(("__decision_note__",
                                       message if ok else
                                       f"Ruling failed: {message}"))
                            decisions_state["ruling"].discard(entry_id)
                            lines.put(("__decisions__", fetch_decisions()))

                        in_background(work)

                    # Bind the current rule_clicked now: a bare closure
                    # over the loop variable would call the last row's
                    # function for every button.
                    approve.configure(command=lambda rc=rule_clicked: rc(agree=True))
                    disagree.configure(command=lambda rc=rule_clicked: rc(agree=False))
                else:
                    tk.Label(buttons,
                             text=("Recorded as a task" if ruled == 1 else
                                   "Recorded as not a task"),
                             bg=card, fg=secondary,
                             font=("Segoe UI", 9, "italic")).pack(side="left")
        bind_wheel(sidebar)
        list_canvas.configure(scrollregion=list_canvas.bbox("all"))

    def on_search_key(event):
        if search_entry.cget("fg") == outline:
            search_entry.configure(fg=text_colour)
        decisions_state["query"] = search_entry.get()
        decisions_state["render_key"] = None
        list_canvas.yview_moveto(0)
        render_decisions()

    search_entry.bind("<KeyRelease>", on_search_key)
    search_entry.bind("<FocusIn>", lambda e: (
        search_entry.delete(0, "end")
        if search_entry.cget("fg") == outline else None,
        search_entry.configure(fg=text_colour)))

    # The console does not poll for decisions. The classifier logs
    # each numbered decision to its own output, which flows through
    # here, so a decision line is the trigger to fetch once. A fetch
    # is also triggered when the classifier starts (its registry is
    # empty until then) and skipped while one is already running or
    # one ran less than a second ago, so one message with several
    # pieces causes a single fetch. Retries cover the service still
    # binding after a start; a fetch that never succeeds leaves the
    # last snapshot alone.
    fetch_state = {"pending": False, "last": 0.0}

    def trigger_decisions_fetch():
        now = time.monotonic()
        if fetch_state["pending"] or now - fetch_state["last"] < 1:
            return
        fetch_state["pending"] = True

        def work():
            snapshot = None
            # The model takes a while to load after a start, so keep
            # trying for about a minute.
            for _ in range(40):
                snapshot = fetch_decisions()
                if snapshot is not None:
                    break
                time.sleep(1)
            fetch_state["pending"] = False
            fetch_state["last"] = time.monotonic()
            if snapshot is not None:
                lines.put(("__decisions__", snapshot))

        in_background(work)

    def drain():
        while True:
            try:
                stream, line = lines.get_nowait()
            except queue.Empty:
                break
            if stream == "__decisions__":
                decisions_state["snapshot"] = line
                render_decisions()
                continue
            if stream == "__decision_note__":
                note.configure(text=line)
                continue
            if stream == STRATA_KEY:
                strata_note.configure(text=line)
                if popout["window"] is not None:
                    popout["note"].configure(text=line)
                continue
            if stream == "__monitor_reopen__":
                open_monitor()
                continue
            if stream == "__monitor_tunnel__":
                open_monitor(tunnel_ready=True)
                continue
            if stream == "__monitor_tunnel_failed__":
                view = monitor_view()
                if view is not None and not view.running:
                    view.show_message(f"{line}\nPress Monitor to try again.")
                continue
            if stream == "__monitor_ended__":
                view = monitor_view()
                if view is not None and not view.running:
                    ended = (f"[{datetime.now():%H:%M:%S}] The monitor closed. "
                             "Press Monitor to open it again.")
                    strata_note.configure(text=ended)
                    remote_hub.set_screen(STRATA_KEY, None, strata_title, ended)
                    if popout["window"] is not None:
                        popout["note"].configure(text=ended)
                continue
            if stream == "classifier":
                if should_fetch_decisions(line):
                    trigger_decisions_fetch()
                elif "stopped, exit code" in line or "not restarting" in line:
                    decisions_state["snapshot"] = None
                    render_decisions()
            box = boxes.get(stream)
            if box is None:  # a service removed while its output was still queued
                continue
            pinned = box.yview()[1] >= 0.999  # do not yank a scrolled-up view
            box.configure(state="normal")
            box.insert("end", line + "\n")
            excess = int(box.index("end-1c").split(".")[0]) - MAX_PANE_LINES
            if excess > 0:
                box.delete("1.0", f"{excess + 1}.0")
            box.configure(state="disabled")
            if pinned:
                box.see("end")
        status.configure(text="   ".join(f"{s.title}: {s.status()}"
                                         for s in (supervisor, abs_login, wiki,
                                                   classifier, librarian, *custom.values())))
        root.after(100, drain)

    watch = {"on": False, "started": time.monotonic()}
    all_services = (supervisor, wiki, abs_login, classifier)

    def remote_status():
        """The remote feed's pane list: the same supervisors as the status bar.
        Runs on the feed's thread, so it reads supervisors only, never Tk."""
        builtins = {"bot": supervisor, "absLogin": abs_login, "wiki": wiki,
                    "classifier": classifier, "librarian": librarian}
        return console_remote.pane_entries(builtins, BUILTIN_TITLES, list(custom.values()))

    remote = {"feed": None}

    def start_remote():
        settings, why = console_remote.remote_settings(cfg["remote"])
        if settings is None:
            if why:
                emit("bot", f"[{datetime.now():%H:%M:%S}] {why}")
            return
        feed = console_remote.RemoteFeed(
            remote_hub, settings, remote_status,
            lambda text: emit("bot", f"[{datetime.now():%H:%M:%S}] {text}"),
            console_name=cfg["console_name"])
        remote["feed"] = feed
        feed.start()

    def stop_remote():
        feed, remote["feed"] = remote["feed"], None
        if feed is not None:
            feed.stop()

    def restart_console(lost):
        """Stop whatever can still be found, close this window, open a new one."""
        names = ", ".join(lost)
        if not restart_allowed(ROOT / "logs" / "watchdog_restarts.json", record=True):
            lines.put(("bot", f"[{datetime.now():%H:%M:%S}] Watchdog: lost {names}, but it "
                              f"already restarted the console {WATCH_MAX_RESTARTS} times in "
                              "the last hour, so it is stopping here. Something keeps "
                              "killing a service watcher: look at logs/crashes.log."))
            try:
                write_watchdog_alert(ROOT / "logs" / "watchdog_alert.flag", lost)
            except OSError:
                pass
            root.after(0, lambda: watchdog_button.configure(text="Watchdog: gave up"))
            return
        lines.put(("bot", f"[{datetime.now():%H:%M:%S}] Watchdog: lost {names}. "
                          "Stopping everything and restarting the console."))
        stop_remote()  # clients get 1001 and the new console can take the port
        if tunnel is not None:
            tunnel.stop()  # frees the tunnel port for the new console
        try:
            with CRASH_LOG_LOCK, open(ROOT / "logs" / "crashes.log", "a",
                                      encoding="utf-8") as handle:
                handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Watchdog lost {names}; "
                             "restarting the console\n")
        except OSError:
            pass
        for service in (*all_services, *custom.values()):
            try:
                service.shutdown()
            except Exception:
                pass
        try:
            kill_listeners([port for service in all_services for port in service.ports])
        except Exception:
            pass
        subprocess.Popen(relaunch_command(), cwd=ROOT, shell=False,
                         creationflags=NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0),
                         close_fds=True)
        root.after(0, root.destroy)

    watchdog = Watchdog([s for s in all_services],
                        lambda lost: in_background(lambda: restart_console(lost)))

    def toggle_watchdog():
        watch["on"] = not watch["on"]
        watch["started"] = time.monotonic()
        watchdog.bad_checks = 0
        watchdog_button.configure(text="Watchdog: On" if watch["on"] else "Watchdog: Off")

    def watch_tick():
        if watch["on"] and time.monotonic() - watch["started"] >= WATCH_GRACE_SECONDS:
            watchdog.check()
        root.after(WATCH_INTERVAL_SECONDS * 1000, watch_tick)

    def apply_settings(general, services):
        """Save the Settings window's edits and apply them to the open console."""
        new = build_saved_config(cfg, general, services)
        save_console_config(new)
        wanted = {s["name"]: s for s in new["services"]}
        cfg.clear()
        cfg.update(new)
        root.title(cfg["console_name"])
        for name in [n for n in custom if n not in wanted]:
            retire_custom(name)
        added = 0
        for name, spec in wanted.items():
            if name in custom:
                custom[name].reconfigure(spec)
                panes[name]["title"] = custom[name].title
                show_collapsed(name)
            else:
                add_custom(spec, start=True)
                added += 1
        message = ("Saved. Changes to a service that is running apply the next time it "
                   "starts: press its Restart.")
        if added:
            message += f"\n\n{added} new service(s) added and started."
        return message

    def open_settings_window():
        from console_settings import open_settings
        colours = {"bg": bg, "card": card, "text": text_colour, "accent": accent,
                   "topbar": topbar, "secondary": secondary, "on_primary": on_primary}
        open_settings(root, colours, cfg, apply_settings)

    def on_close():
        if messagebox.askokcancel(cfg["console_name"],
                                  "Closing this window stops the bot, site, wiki, "
                                  "absLogin and classifier, and every service you "
                                  "added. Strata keeps running. Close?"):
            for view in (popout["view"], strata_state["view"]):
                if view is not None:
                    view.stop()  # the monitor only; Strata keeps running
            everything = [supervisor, wiki, abs_login, classifier, librarian, *custom.values()]
            # In parallel: a service with a clean-stop command can take a while.
            stoppers = [threading.Thread(target=s.shutdown, daemon=True) for s in everything]
            for stopper in stoppers:
                stopper.start()
            for stopper in stoppers:
                stopper.join(timeout=90)
            stop_remote()
            if tunnel is not None:
                tunnel.stop()
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    supervisor.start()
    for service, path in extras:
        if path.is_file():
            service.start()
        else:
            emit(service.name, f"Not found, so not started: {path}")
    classifier_files = [CLASSIFIER_SERVICE, CLASSIFIER_PYTHON]
    if all(path.is_file() for path in classifier_files):
        classifier.start()
    else:
        missing = next(path for path in classifier_files if not path.is_file())
        emit("classifier", f"Not found, so not started: {missing}")
    if librarian_cmd:
        librarian.start()
    else:
        emit("librarian", "Off: console.json has no valid librarian host and unit.")
    render_decisions()
    drain()
    if strata_state["view"] is not None:
        root.after(800, open_monitor)
    # The classifier may already be running (started by an earlier
    # console), in which case no "Starting" line will ever arrive.
    trigger_decisions_fetch()
    if cfg_note:
        emit("bot", cfg_note)
    start_remote()

    def start_custom():
        for service in list(custom.values()):
            if service.autostart:
                service.start()

    root.after(cfg["startup_delay_seconds"] * 1000, start_custom)
    watch_tick()
    # On by default. --no-watchdog starts with it off (for debugging).
    if "--no-watchdog" not in sys.argv:
        toggle_watchdog()
    root.mainloop()


if __name__ == "__main__":
    main()
