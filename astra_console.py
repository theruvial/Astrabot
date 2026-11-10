"""Astrabot console: run the bot, site, wiki, absLogin and classifier, watch output.

Run with startConsole.bat. This window owns the bot, wiki backend,
absLogin and classifier processes, so it replaces startBot.bat and any
hand-started server.py, app.py or classifier_service.py: never run those
alongside it. absLogin needs ABS_API_KEY set (e.g. via setx) before this
runs. The classifier runs from the AstraThought checkout with that
repo's venv, so it can load the SetFit model the bot itself cannot.

The audiobook site is hosted by the audiobooks cog, so it only runs
while the bot does: "Restart Bot" restarts it too. "Restart Site"
reloads that cog in the running bot (through logs/reload_cog.flag, see
cogs/moderation.py) and, like any load of that cog, rebuilds the
requests channel.

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
not restart the classifier; use "Restart Classifier" for that. If any
pull fails nothing is restarted.

Crash handling matches startBot.bat: restart after 10 seconds, give up
after 5 crashes in a row, stay stopped after a clean exit. Output goes
to logs/bot.log, logs/wiki.log, logs/absLogin.log or
logs/classifier.log, and start/stop lines of all four go to
logs/crashes.log. The wiki backend, absLogin and classifier get the
same crash handling as the bot.
"""

import json
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
CONTINUATION = re.compile(r"^(\s|Traceback |\w+(\.\w+)*(Error|Exception)\b)")


def route_line(line, previous="bot"):
    """Pick the pane for a line. Tracebacks follow the line above them."""
    if SITE_LINE.match(line):
        return "site"
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
                self._kill(self._proc)
        self._wake.set()

    def shutdown(self):
        with self._lock:
            self._closing = True
            self._want_running = False
            if self._proc is not None:
                self._kill(self._proc)
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

    def _run_once(self):
        """Start the program, stream its output until it exits, return its code."""
        self._before_start()
        self._clear_ports()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self._record(f"Starting {self.name}")
        self._say(self.name, f"Starting {self.name}.")
        proc = subprocess.Popen(self.command, cwd=self.cwd, env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, encoding="utf-8",
                                errors="replace", creationflags=NO_WINDOW)
        with self._lock:
            self._proc = proc
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
            if crashes >= MAX_CRASHES:
                self._want_running = False
                crashes = 0
                message = (f"{self.title} crashed {MAX_CRASHES} "
                           "times in a row - not restarting.")
                self._record(message)
                self._say(self.name, message + " Press Restart when it is fixed.")
                continue
            self._say(self.name, f"Crash {crashes} of {MAX_CRASHES}, "
                                 f"restarting in {self.restart_delay} seconds.")
            self._waiting = True
            if self._wake.wait(self.restart_delay):
                self._wake.clear()
            self._waiting = False


class BotSupervisor(Supervisor):
    """The bot, plus the actions that only make sense for it."""

    def __init__(self, emit, root=ROOT, command=None, **kwargs):
        if command is None:  # a custom command (the tests) never clears the real site port
            kwargs.setdefault("ports", (SITE_PORT,))
        super().__init__(emit, "bot", root, command or [python_exe(), "-u", "bot.py"],
                         Path(root) / "logs", **kwargs)
        self.flag = self.log_dir / "reload_cog.flag"

    def _before_start(self):
        self.flag.unlink(missing_ok=True)

    def _route(self, line, previous):
        return route_line(line, previous)

    def reload_site(self):
        if self._proc is None:
            self._say("site", "Bot is not running, so there is no site to reload.")
            return False
        # Written then renamed so the bot can never read a half-made flag.
        pending = self.flag.with_suffix(".tmp")
        pending.write_text("audiobooks", encoding="utf-8")
        pending.replace(self.flag)
        self._say("site", "Site reload requested.")
        return True

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


def classifier_supervisor(emit):
    """The classifier runs from the AstraThought checkout, with that
    repo's venv (torch lives there, not in the bot's Python), and from
    that directory so ./tasks-AstraThought-v2 resolves."""
    return Supervisor(emit, "classifier", CLASSIFIER_DIR,
                      [str(CLASSIFIER_PYTHON), "-u", "classifier_service.py"],
                      ROOT / "logs", ports=(CLASSIFIER_PORT,))


def main():
    import tkinter as tk
    from tkinter import messagebox

    # Material Design dark theme, same tokens as the audiobook
    # requests site (cogs/audiobooks_web.py): background #141218,
    # card #1e1b24, text #e6e0e9, primary #d0bcff on #381e72,
    # input #2b2930 with #938f99 outline, secondary text #cac4d0,
    # chip #4a4458, error #b3261e, dark green #274d27.
    bg, card, text_colour, accent = "#141218", "#1e1b24", "#e6e0e9", "#d0bcff"
    topbar, input_bg, outline = "#2b2930", "#2b2930", "#938f99"
    secondary, on_primary, chip_bg = "#cac4d0", "#381e72", "#4a4458"
    error, ok_dark = "#b3261e", "#274d27"
    lines = queue.Queue()

    root = tk.Tk()
    root.title("Astrabot Console")
    root.geometry("1400x900")
    root.configure(bg=bg)

    def emit(stream, line):
        lines.put((stream, line))

    supervisor = BotSupervisor(emit)
    wiki = wiki_supervisor(emit)
    abs_login = abslogin_supervisor(emit)
    classifier = classifier_supervisor(emit)
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

    button("Restart Bot", lambda: in_background(supervisor.restart))
    button("Restart Site", supervisor.reload_site)
    button("Restart absLogin", lambda: in_background(abs_login.restart))
    button("Restart Wiki", lambda: in_background(wiki.restart))
    button("Restart Classifier", lambda: in_background(classifier.restart))
    watchdog_button = button("Watchdog: Off", lambda: toggle_watchdog())
    pull_button = button("Pull + Restart", pull)
    status = tk.Label(bar, bg=topbar, fg=text_colour, font=("Segoe UI", 10))
    status.pack(side="right", padx=10, pady=10)

    body = tk.Frame(root, bg=bg)
    body.pack(fill="both", expand=True)
    grid = tk.Frame(body, bg=bg)
    grid.pack(side="left", fill="both", expand=True, padx=6, pady=(0, 6))
    for index in (0, 1):
        grid.rowconfigure(index, weight=3, uniform="pane")
        grid.columnconfigure(index, weight=1, uniform="pane")
    grid.rowconfigure(2, weight=2)

    def pane(title, row, column, columnspan=1):
        frame = tk.Frame(grid, bg=bg)
        frame.grid(row=row, column=column, columnspan=columnspan,
                   sticky="nsew", padx=4, pady=4)
        tk.Label(frame, text=title, bg=bg, fg=accent, anchor="w",
                 font=("Segoe UI", 10, "bold")).pack(fill="x")
        scroll = tk.Scrollbar(frame)
        scroll.pack(side="right", fill="y")
        box = tk.Text(frame, bg=card, fg=text_colour, insertbackground=text_colour,
                      relief="flat", wrap="word", state="disabled", width=1, height=1,
                      font=("Consolas", 10), yscrollcommand=scroll.set)
        box.pack(side="left", fill="both", expand=True)
        scroll.configure(command=box.yview)
        return box

    # Bot first, then the servers in port order. The classifier gets a
    # shorter row of its own at the bottom: its log is the thought
    # process, worth watching, but quieter than the bot.
    boxes = {"bot": pane("Bot", 0, 0), "site": pane("Site (13380)", 0, 1),
             "absLogin": pane("absLogin (13381)", 1, 0), "wiki": pane("Wiki (13382)", 1, 1),
             "classifier": pane("Classifier (13383)", 2, 0, columnspan=2)}

    # -- decisions panel (right-hand sidebar) --------------------------
    # Most recent first, up to 50, with a search box. Each row is a
    # card with the decision text, a chip with id / verdict / score /
    # trigger / lean, and an approve button (check mark) and a
    # disagree button (X). A ruling is saved by the classifier
    # service as a training label at once.
    sidebar = tk.Frame(body, bg=bg, width=400)
    sidebar.pack(side="right", fill="y", padx=(0, 6), pady=(0, 6))
    sidebar.pack_propagate(False)
    tk.Label(sidebar, text="Decisions", bg=bg, fg=accent, anchor="w",
             font=("Segoe UI", 10, "bold")).pack(fill="x", pady=(4, 2))
    decisions_status = tk.Label(sidebar, text="Classifier: checking...",
                                bg=bg, fg=secondary, anchor="w",
                                font=("Segoe UI", 9))
    decisions_status.pack(fill="x")

    search_row = tk.Frame(sidebar, bg=bg)
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

    note = tk.Label(sidebar, text="", bg=bg, fg=secondary, anchor="w",
                    font=("Segoe UI", 9), wraplength=380, justify="left")
    note.pack(fill="x")

    list_canvas = tk.Canvas(sidebar, bg=bg, highlightthickness=0)
    list_scroll = tk.Scrollbar(sidebar, command=list_canvas.yview)
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

    def bind_wheel(widget):
        widget.bind("<MouseWheel>", scroll_decisions)
        for child in widget.winfo_children():
            bind_wheel(child)

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
            if stream == "classifier":
                if should_fetch_decisions(line):
                    trigger_decisions_fetch()
                elif "stopped, exit code" in line or "not restarting" in line:
                    decisions_state["snapshot"] = None
                    render_decisions()
            box = boxes[stream]
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
                                                   classifier)))
        root.after(100, drain)

    watch = {"on": False, "started": time.monotonic()}
    all_services = (supervisor, wiki, abs_login, classifier)

    def restart_console(lost):
        """Stop whatever can still be found, close this window, open a new one."""
        names = ", ".join(lost)
        if not restart_allowed(ROOT / "logs" / "watchdog_restarts.json", record=True):
            lines.put(("bot", f"[{datetime.now():%H:%M:%S}] Watchdog: lost {names}, but it "
                              f"already restarted the console {WATCH_MAX_RESTARTS} times in "
                              "the last hour, so it is stopping here. Something keeps "
                              "killing a service watcher: look at logs/crashes.log."))
            root.after(0, lambda: watchdog_button.configure(text="Watchdog: gave up"))
            return
        lines.put(("bot", f"[{datetime.now():%H:%M:%S}] Watchdog: lost {names}. "
                          "Stopping everything and restarting the console."))
        try:
            with CRASH_LOG_LOCK, open(ROOT / "logs" / "crashes.log", "a",
                                      encoding="utf-8") as handle:
                handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Watchdog lost {names}; "
                             "restarting the console\n")
        except OSError:
            pass
        for service in all_services:
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

    def on_close():
        if messagebox.askokcancel("Astrabot Console",
                                  "Closing this window stops the bot, site, wiki, "
                                  "absLogin and classifier. Close?"):
            for service in (supervisor, wiki, abs_login, classifier):
                service.shutdown()
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
    render_decisions()
    drain()
    # The classifier may already be running (started by an earlier
    # console), in which case no "Starting" line will ever arrive.
    trigger_decisions_fetch()
    watch_tick()
    # On by default. --no-watchdog starts with it off (for debugging).
    if "--no-watchdog" not in sys.argv:
        toggle_watchdog()
    root.mainloop()


if __name__ == "__main__":
    main()
