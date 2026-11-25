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
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

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


def monitor_argv(folder) -> list[str]:
    """The interactive monitor. It finds the running launch by itself. Pure."""
    return [str(python_of(folder)), "strata.py", "monitor"]


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
