"""Start, stop and restart the local Strata LLM server from the Golurk Console
(2026-11-25). No tkinter in here.

Strata is the model server the Home Assistant agent talks to
(http://127.0.0.1:8080). It is not a child of the console: ``strata.py
runtime start`` hands it to Strata's own host process, so the server keeps
running when the console closes, and the console never kills it by PID.
Every action goes through Strata's CLI, in the order docs/AGENT_COG_DESIGN.md
section 3 (Strata's management protocol) asks for:

* Start:   ``runtime start --profile P --json``.
* Stop:    ``runtime stop --plan`` first, then ``runtime stop`` with the
  ``--if-owner-id`` / ``--if-instance-id`` the plan showed, so the console
  only ever stops the instance it looked at.
* Restart: ``runtime restart --plan`` first, then ``runtime restart`` with
  the plan's current owner and instance and ``--startup-wait 600``. When
  the plan shows the launch file was edited (``source_migration``), the
  edit is allowed with ``--allow-source-migration --if-source-sha256``.
  When it would switch to a different launch file (``source_switch``) the
  console refuses and says why: that is for a person to decide.

Strata's JSON can carry credentials, so the console never shows it raw:
``summarise`` picks out the few fields worth a line in the pane.

The folder and profile come from console.json's ``strata`` block (see
console_services.DEFAULT_CONFIG). A blank folder turns the pane off.

Remote server (2026-11-25): when the model runs on another PC, ``strata.ssh``
names it (an SSH alias with key login). Strata refuses to monitor a remote
server over plain HTTP ("remote monitoring requires HTTPS or a local SSH
tunnel"), so ``Tunnel`` keeps ``ssh -N -L 127.0.0.1:<tunnel_port>:127.0.0.1:
<port>`` running and the monitor gets ``--url http://127.0.0.1:<tunnel_port>``.
The Windows Strata still runs the monitor (the remote checkout lacks the
monitor's libraries). Start / Stop / Restart stay local-only, so the console
hides them in that mode.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

import console_job

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
STARTUP_WAIT = 600
# Longest a single CLI call may take: a restart can wait STARTUP_WAIT for the
# model to load, plus the drain before it.
CALL_TIMEOUT = STARTUP_WAIT + 300


def python_of(folder) -> Path:
    return Path(folder) / "python" / "python.exe"


def is_installed(folder) -> bool:
    return bool(str(folder or "").strip()) and python_of(folder).is_file() \
        and (Path(folder) / "strata.py").is_file()


def runtime_argv(folder, profile, action, *extra) -> list[str]:
    """The CLI call for one runtime action, always with --json. Pure."""
    return [str(python_of(folder)), "strata.py", "runtime", action,
            "--profile", profile, *extra, "--json"]


def monitor_argv(folder, url=None) -> list[str]:
    """The interactive monitor. Without ``url`` it finds this PC's running
    launch by itself; with one (the tunnel) it watches that server. Pure."""
    argv = [str(python_of(folder)), "strata.py", "monitor"]
    return argv + ["--url", url] if url else argv


# An SSH alias or address only: never starts with "-", so it can't be read as
# an ssh option.
SSH_HOST = re.compile(r"^[A-Za-z0-9_.@][A-Za-z0-9_.@-]*$")


def _port(text, default):
    try:
        value = int(str(text).strip())
    except (TypeError, ValueError):
        return default
    return value if 0 < value < 65536 else default


def remote_of(strata_cfg) -> dict | None:
    """``{"host", "port", "tunnel_port"}`` when console.json's strata block
    names a remote server (``ssh``), else None. A host that isn't a plain
    alias or address counts as none. Pure."""
    host = str((strata_cfg or {}).get("ssh") or "").strip()
    if not host or not SSH_HOST.match(host):
        return None
    return {"host": host,
            "port": _port(strata_cfg.get("port"), 8080),
            "tunnel_port": _port(strata_cfg.get("tunnel_port"), 18080)}


def tunnel_argv(host, port, tunnel_port) -> list[str]:
    """``ssh`` forwarding 127.0.0.1:tunnel_port here to 127.0.0.1:port there.

    BatchMode: key login only, never a password prompt nobody can see.
    ExitOnForwardFailure: a taken local port fails at once instead of
    leaving an ssh with no tunnel. ServerAlive: a dead link ends ssh within
    about 45 s, so the console knows to reopen it. Pure."""
    return ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
            "-L", f"127.0.0.1:{tunnel_port}:127.0.0.1:{port}", "--", host]


def port_open(port, host="127.0.0.1", timeout=0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class Tunnel:
    """One ``ssh -L`` process, opened on demand and closed with the console.

    ``ensure()`` blocks (call it from a worker thread) until the local port
    accepts connections, or raises RuntimeError with ssh's reason. If the
    port already answers before this Tunnel opened anything (an ssh opened
    by hand, or left by an older console), that one is used. The ssh this
    one opens ends with the console process, however that ends
    (console_job.py). ``popen``, ``probe`` and ``sleep`` are replaceable for
    the tests."""

    def __init__(self, host, port, tunnel_port, log_path=None, wait=10.0,
                 popen=subprocess.Popen, probe=port_open, sleep=time.sleep):
        self.host, self.port, self.tunnel_port = host, port, tunnel_port
        self.log_path = Path(log_path) if log_path else None
        self.wait = wait
        self.popen, self.probe, self.sleep = popen, probe, sleep
        self.proc = None
        self.lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.tunnel_port}"

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _reason(self):
        """ssh's last stderr line from the log, if any."""
        if self.log_path is None:
            return ""
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[-1] if lines else ""

    def ensure(self):
        with self.lock:
            if self.alive or (self.proc is None and self.probe(self.tunnel_port)):
                return
            self.proc = None
            log = None
            if self.log_path is not None:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                log = open(self.log_path, "w", encoding="utf-8")
            try:
                self.proc = self.popen(
                    tunnel_argv(self.host, self.port, self.tunnel_port),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=log or subprocess.DEVNULL, creationflags=NO_WINDOW)
            except OSError as exc:
                raise RuntimeError(f"Could not run ssh for the Strata tunnel: {exc}") from exc
            finally:
                if log is not None:
                    log.close()  # the child keeps its own handle
            console_job.tie(self.proc)  # best effort; stop() is the usual way
            waited = 0.0
            while waited < self.wait:
                if self.proc.poll() is not None:
                    self.proc = None
                    reason = self._reason()
                    raise RuntimeError(f"The SSH tunnel to {self.host} closed"
                                       + (f": {reason}" if reason else "."))
                if self.probe(self.tunnel_port):
                    return
                self.sleep(0.2)
                waited += 0.2
            self._stop_locked()
            raise RuntimeError(f"The SSH tunnel to {self.host} didn't open within "
                               f"{self.wait:.0f} s.")

    def _stop_locked(self):
        proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    def stop(self):
        """Close the tunnel this Tunnel opened (never one it only reused)."""
        with self.lock:
            self._stop_locked()


def parse_reply(stdout) -> dict | None:
    """The JSON object a --json call printed, or None. Pure.

    Tolerates log lines before the object by reading from the first '{'.
    """
    text = (stdout or "").strip()
    start = text.find("{")
    if start < 0:
        return None
    try:
        payload = json.loads(text[start:])
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def error_of(reply) -> str:
    """Strata's own reason a call failed, as one short line. Pure."""
    if not isinstance(reply, dict):
        return "no reply"
    error = reply.get("error") or reply.get("message") or reply.get("reason")
    if isinstance(error, dict):
        error = error.get("message") or error.get("code") or ""
    return str(error or "it did not say why")[:300]


def summarise(reply) -> str:
    """A short, credential-free line for a status/plan/action reply. Pure."""
    if not isinstance(reply, dict):
        return "no reply"
    if not reply.get("ok", False):
        return f"failed: {error_of(reply)}"
    body = reply.get("plan") if isinstance(reply.get("plan"), dict) else reply
    parts = []
    for key in ("state", "readiness_state", "api_url"):
        value = body.get(key) or reply.get(key)
        if value:
            parts.append(str(value))
    return ", ".join(parts) or "ok"


def stop_args(plan) -> list[str] | None:
    """The guard flags for a stop, from a stop --plan reply; None when there
    is nothing running to stop. Pure."""
    body = (plan or {}).get("plan") if isinstance((plan or {}).get("plan"), dict) else None
    if not body or not body.get("owner_id") or not body.get("instance_id"):
        return None
    return ["--if-owner-id", str(body["owner_id"]),
            "--if-instance-id", str(body["instance_id"])]


def restart_args(plan) -> tuple[list[str] | None, str]:
    """The flags for a restart from a restart --plan reply. Pure.

    Returns (args, "") to go ahead, or (None, reason) when the console must
    not restart: nothing running, or a switch to another launch file.
    """
    if not isinstance(plan, dict) or not plan.get("ok", False):
        return None, f"the plan failed: {error_of(plan)}"
    owner, instance = plan.get("current_owner_id"), plan.get("current_instance_id")
    if not owner or not instance:
        return None, "Strata is not running, so there is nothing to restart (use Start)."
    if plan.get("source_switch"):
        return None, ("the plan would switch to a different launch file; do that "
                      "by hand with --allow-source-switch.")
    args = ["--if-owner-id", str(owner), "--if-instance-id", str(instance),
            "--startup-wait", str(STARTUP_WAIT)]
    candidate = plan.get("candidate_source_sha256")
    if plan.get("source_migration") or (
            candidate and candidate != plan.get("current_source_sha256")):
        if not candidate:
            return None, "the launch file changed but the plan gave no checksum for it."
        args += ["--allow-source-migration", "--if-source-sha256", str(candidate)]
    return args, ""


class StrataControl:
    """Runs Strata CLI calls and reports each step through ``say(text)``.

    Blocking: call the actions from a worker thread. ``run`` is replaceable
    so the tests can script Strata's replies.
    """

    def __init__(self, folder, profile, say, run=None):
        self.folder = Path(folder)
        self.profile = profile
        self.say = say
        self.run = run or self._run

    def _run(self, argv, timeout=CALL_TIMEOUT):
        result = subprocess.run(argv, cwd=self.folder, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout,
                                creationflags=NO_WINDOW)
        return parse_reply(result.stdout)

    def call(self, action, *extra):
        try:
            return self.run(runtime_argv(self.folder, self.profile, action, *extra))
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"no answer within {CALL_TIMEOUT} seconds"}
        except OSError as exc:
            return {"ok": False, "error": f"could not run Strata: {exc}"}

    def status(self):
        reply = self.call("status")
        self.say(f"Status: {summarise(reply)}")
        return reply

    def start(self):
        self.say(f"Starting Strata ({self.profile})...")
        reply = self.call("start")
        self.say(f"Start: {summarise(reply)}")
        return bool(reply and reply.get("ok"))

    def stop(self):
        self.say("Stopping Strata: checking what is running...")
        plan = self.call("stop", "--plan")
        if not plan or not plan.get("ok"):
            self.say(f"Stop: the plan failed: {error_of(plan)}")
            return False
        guard = stop_args(plan)
        if guard is None:
            self.say("Stop: Strata is not running.")
            return True
        reply = self.call("stop", *guard)
        self.say(f"Stop: {summarise(reply)}")
        return bool(reply and reply.get("ok"))

    def restart(self):
        self.say("Restarting Strata: checking the plan...")
        plan = self.call("restart", "--plan")
        args, reason = restart_args(plan)
        if args is None:
            self.say(f"Restart refused: {reason}")
            return False
        if "--allow-source-migration" in args:
            self.say("The launch file was edited; restarting with the edited file.")
        self.say(f"Restarting (waits up to {STARTUP_WAIT // 60} minutes for the model)...")
        reply = self.call("restart", *args)
        self.say(f"Restart: {summarise(reply)}")
        return bool(reply and reply.get("ok"))
