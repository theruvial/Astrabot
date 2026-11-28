"""A stand-in Golurk Console for tests/test_console_orphans.py.

Run as a separate process so a test can end it the way a real console ends.
It uses the console's real code, but every program it starts is a Python
process that only sleeps, and every log goes to the folder it is given.

    python standin_console.py <mode> <folder>

It prints one line, the PID(s) of what it started, then waits for a line on
stdin (or to be killed).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import astra_console  # noqa: E402
import console_services  # noqa: E402
import console_strata  # noqa: E402

# Ends by itself after a minute, so a failing test cannot leave it for long.
SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


def report(*pids):
    print(" ".join(map(str, pids)), flush=True)


def started(supervisor):
    """Start it and return the PID of its program."""
    supervisor.start()
    while supervisor._proc is None:
        supervisor._wake.wait(0.02)
    return supervisor._proc.pid


def librarian(folder):
    sup = astra_console.LibrarianSupervisor(lambda *_: None, SLEEPER, log_dir=folder)
    report(started(sup))
    sys.stdin.readline()


def custom(folder):
    sup = astra_console.CustomSupervisor(
        lambda *_: None, {"name": "game", "title": "Game", "command": "x"}, log_dir=folder)
    sup.command = SLEEPER
    report(started(sup))
    sys.stdin.readline()


def tunnel(folder):
    answers = iter([False])  # nothing on the port yet, then the tunnel is up
    made = console_strata.Tunnel(
        "laptop", 8080, 18080, probe=lambda *_: next(answers, True),
        popen=lambda argv, **kwargs: subprocess.Popen(SLEEPER, **kwargs))
    made.ensure()
    report(made.proc.pid)
    sys.stdin.readline()


def strata(folder):
    """A Strata runtime call whose host process must outlive the console.
    The real CLI starts the model server in a process of its own and exits."""
    launcher = ("import json, subprocess; "
                f"host = subprocess.Popen({SLEEPER!r}, stdin=subprocess.DEVNULL, "
                "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, "
                f"creationflags={getattr(subprocess, 'CREATE_NO_WINDOW', 0)}); "
                "print(json.dumps({'ok': True, 'pid': host.pid}))")
    control = console_strata.StrataControl(folder, "test", lambda _text: None)
    reply = control._run([sys.executable, "-c", launcher], timeout=30)
    # A supervised program too, so the console has already done whatever it
    # does to the children it owns.
    sup = astra_console.LibrarianSupervisor(lambda *_: None, SLEEPER, log_dir=folder)
    report(reply["pid"], started(sup))
    sys.stdin.readline()


def watchdog(folder):
    """The real console window (hidden), tripped into a Watchdog relaunch
    once the test sends a line. Nothing real is started, no port is touched
    and no new console is opened."""
    import tkinter

    class HiddenTk(tkinter.Tk):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.withdraw()

    tkinter.Tk = HiddenTk
    logs = folder / "logs"
    logs.mkdir()
    made = {}

    def service(name):
        return lambda emit: astra_console.Supervisor(emit, name, folder, SLEEPER, logs)

    real_bot = astra_console.BotSupervisor
    real_librarian = astra_console.LibrarianSupervisor

    def bot(emit):
        made["bot"] = real_bot(emit, root=folder, command=SLEEPER)
        made["bot"].is_lost = lambda: made.get("trip", False)
        return made["bot"]

    def librarian_tail(emit, command):
        made["librarian"] = real_librarian(emit, command, log_dir=logs)
        return made["librarian"]

    config = console_services.merge_config({"strata": {"dir": ""}, "startup_delay_seconds": 0})
    astra_console.ROOT = folder
    astra_console.load_console_config = lambda: (config, "")
    astra_console.BotSupervisor = bot
    astra_console.LibrarianSupervisor = librarian_tail
    astra_console.librarian_command = lambda _cfg: SLEEPER
    astra_console.wiki_supervisor = service("wiki")
    astra_console.abslogin_supervisor = service("absLogin")
    astra_console.classifier_supervisor = service("classifier")
    astra_console.kill_listeners = lambda _ports: []  # never the real ports
    astra_console.fetch_decisions = lambda *_a, **_k: None
    astra_console.restart_allowed = lambda *_a, **_k: True
    astra_console.relaunch_command = lambda: [sys.executable, "-c", "pass"]
    astra_console.WATCH_GRACE_SECONDS = 0
    astra_console.WATCH_INTERVAL_SECONDS = 1

    import threading

    def operator():
        while "librarian" not in made or made["librarian"]._proc is None:
            threading.Event().wait(0.05)
        report(made["librarian"]._proc.pid)
        sys.stdin.readline()
        made["trip"] = True

    threading.Thread(target=operator, daemon=True).start()
    astra_console.main()  # returns when the relaunch destroys the window


if __name__ == "__main__":
    globals()[sys.argv[1]](Path(sys.argv[2]))
    os._exit(0)  # no cleanup handlers: only the code under test may clean up
