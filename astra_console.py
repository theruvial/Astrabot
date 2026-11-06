"""Astrabot console: run the bot, site, wiki and absLogin, watch output.

Run with startConsole.bat. This window owns the bot, wiki backend and
absLogin processes, so it replaces startBot.bat and any hand-started
server.py or app.py: never run those alongside it. absLogin needs
ABS_API_KEY set (e.g. via setx) before this runs.

The audiobook site is hosted by the audiobooks cog, so it only runs
while the bot does: "Restart Bot" restarts it too. "Restart Site"
reloads that cog in the running bot (through logs/reload_cog.flag, see
cogs/moderation.py) and, like any load of that cog, rebuilds the
requests channel.

"Pull + Restart" fast-forwards every repo, then restarts the bot,
wiki backend and absLogin. If any pull fails nothing is restarted.

Crash handling matches startBot.bat: restart after 10 seconds, give up
after 5 crashes in a row, stay stopped after a clean exit. Output goes
to logs/bot.log, logs/wiki.log or logs/absLogin.log, and start/stop
lines of all three go to logs/crashes.log. The wiki backend and
absLogin get the same crash handling as the bot.
"""

import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ABSLOGIN_DIR = Path(r"G:\My Drive\codingShit\absLogin")
WIKI_DIR = Path(r"C:\codingShit\Aboard-the-Astra-Wiki")
WIKI_BACKEND = WIKI_DIR / "Tools" / "wiki" / "backend"

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
                 restart_delay=RESTART_DELAY, stable_seconds=STABLE_SECONDS):
        self.emit = emit
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
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def title(self):
        return self.name[0].upper() + self.name[1:]

    # -- controls ------------------------------------------------------
    def start(self):
        self._thread.start()

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

    def _route(self, line, previous):
        return self.name

    def _run_once(self):
        """Start the program, stream its output until it exits, return its code."""
        self._before_start()
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


def wiki_supervisor(emit):
    return Supervisor(emit, "wiki", WIKI_BACKEND, [python_exe(), "-u", "server.py"],
                      ROOT / "logs")


def abslogin_supervisor(emit):
    return Supervisor(emit, "absLogin", ABSLOGIN_DIR, [python_exe(), "-u", "app.py"],
                      ROOT / "logs")


def main():
    import tkinter as tk
    from tkinter import messagebox

    bg, card, text_colour, accent = "#141218", "#1e1b24", "#e6e0e9", "#d0bcff"
    lines = queue.Queue()

    root = tk.Tk()
    root.title("Astrabot Console")
    root.geometry("1800x800")
    root.configure(bg=bg)

    def emit(stream, line):
        lines.put((stream, line))

    supervisor = BotSupervisor(emit)
    wiki = wiki_supervisor(emit)
    abs_login = abslogin_supervisor(emit)
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

    bar = tk.Frame(root, bg=bg)
    bar.pack(fill="x", padx=10, pady=10)

    def button(label, command):
        widget = tk.Button(bar, text=label, command=command, bg=accent, fg=bg,
                           activebackground=text_colour, relief="flat",
                           padx=14, pady=6, font=("Segoe UI", 10, "bold"))
        widget.pack(side="left", padx=(0, 8))
        return widget

    button("Restart Bot", lambda: in_background(supervisor.restart))
    button("Restart Site", supervisor.reload_site)
    button("Restart Wiki", lambda: in_background(wiki.restart))
    button("Restart absLogin", lambda: in_background(abs_login.restart))
    pull_button = button("Pull + Restart", pull)
    status = tk.Label(bar, bg=bg, fg=text_colour, font=("Segoe UI", 10))
    status.pack(side="right")

    panes = tk.PanedWindow(root, orient="horizontal", bg=bg, sashwidth=6, bd=0)
    panes.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def pane(title):
        frame = tk.Frame(panes, bg=bg)
        tk.Label(frame, text=title, bg=bg, fg=accent, anchor="w",
                 font=("Segoe UI", 10, "bold")).pack(fill="x")
        scroll = tk.Scrollbar(frame)
        scroll.pack(side="right", fill="y")
        box = tk.Text(frame, bg=card, fg=text_colour, insertbackground=text_colour,
                      relief="flat", wrap="word", state="disabled",
                      font=("Consolas", 10), yscrollcommand=scroll.set)
        box.pack(side="left", fill="both", expand=True)
        scroll.configure(command=box.yview)
        panes.add(frame, stretch="always")
        return box

    boxes = {"bot": pane("Bot"), "site": pane("Site"), "wiki": pane("Wiki"),
             "absLogin": pane("absLogin")}

    def drain():
        while True:
            try:
                stream, line = lines.get_nowait()
            except queue.Empty:
                break
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
                                         for s in (supervisor, wiki, abs_login)))
        root.after(100, drain)

    def on_close():
        if messagebox.askokcancel("Astrabot Console",
                                  "Closing this window stops the bot, site, wiki and absLogin. Close?"):
            for service in (supervisor, wiki, abs_login):
                service.shutdown()
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    supervisor.start()
    for service, path in extras:
        if path.is_file():
            service.start()
        else:
            emit(service.name, f"Not found, so not started: {path}")
    drain()
    root.mainloop()


if __name__ == "__main__":
    main()
