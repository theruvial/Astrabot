"""Helpers for the Golurk Console's extra services. No tkinter in here.

The console has five built-in windows (bot, site, absLogin, wiki and
classifier) that are always there. Anything you add in Settings is a
"service": a program, a PowerShell command or a batch file that the
console starts, watches and restarts, with a pane of its own that can
be removed again. This module holds what those services need, so it is
testable without a window:

* ``console.json`` (next to the console, never committed): load, merge,
  and atomic save. A broken file never stops the console from opening.
* Turning what you typed in Settings into a command Windows can run
  (``command_to_argv``), and checking its paths (``check_entry``).
* ``popen_as_user``: start a program under a different Windows account
  (for example a limited guest user) with its output captured, and
  everything it starts killed together when it is stopped. The password
  is never stored: the service names an environment variable that holds
  it, and a blank password is allowed for an account that has none.
* Where each pane sits in the grid (``pane_cells``) and how much room a
  row gets (``row_weights``), so collapsing a pane gives its space back.

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
BUILTIN_PANES = ("bot", "site", "absLogin", "wiki", "classifier")
BUILTIN_TITLES = {"bot": "Bot", "site": "Site (13380)", "absLogin": "absLogin (13381)",
                  "wiki": "Wiki (13382)", "classifier": "Classifier (13383)"}
# The five built-in windows live together in one collapsible group.
GROUP_KEY = "astrabot"
GROUP_TITLE = "Astrabot"
# Where a built-in pane sits inside that group's grid: (row, column, columnspan).
BUILTIN_CELLS = {"bot": (0, 0, 1), "site": (0, 1, 1), "absLogin": (1, 0, 1),
                 "wiki": (1, 1, 1), "classifier": (2, 0, 2)}
# Added services sit in the outer grid under the group (row 0 is the group).
FIRST_CUSTOM_ROW = 1

DEFAULT_CONSOLE_NAME = "Golurk Console"

DEFAULT_CONFIG = {
    "console_name": DEFAULT_CONSOLE_NAME,
    # Wait this long after the console opens before starting the extra services.
    "startup_delay_seconds": 0,
    # Pane key -> True when that pane is collapsed (remembered between runs).
    "collapsed": {},
    # The services added in Settings. Built-in windows are not listed here.
    "services": [],
}

DEFAULT_SERVICE = {
    "name": "", "title": "", "run_as": "program", "command": "", "cwd": "",
    "env": {}, "stop_input": [], "stop_grace": 30, "autostart": True,
    "user": {"enabled": False, "username": "", "domain": "", "password_env": ""},
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
        if key.startswith("_"):
            continue
        if key == "user" and isinstance(value, dict):
            out["user"].update({k: v for k, v in value.items() if k in out["user"]})
        else:
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


def check_entry(spec, which=None, environ=None) -> list:
    """Problems with one service's settings, as plain sentences ([] = looks fine).

    Checks the working folder, that the program exists (or is on PATH),
    and, when it runs as another user, that a user name is given and the
    password variable it names is set. ``which`` and ``environ`` are
    injectable."""
    import shutil
    which = which or shutil.which
    environ = os.environ if environ is None else environ
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
    user = spec.get("user") or {}
    if user.get("enabled"):
        if not str(user.get("username") or "").strip():
            problems.append("Run as another user is ticked but no user name is set.")
        var = str(user.get("password_env") or "").strip()
        if var and var not in environ:
            problems.append(f"The password variable {var} is not set in this console's "
                            "environment. Leave the name blank if the account has no password.")
    return problems


# -- pane layout ----------------------------------------------------------------
def pane_cells(custom_keys) -> dict:
    """Grid cell (row, column, columnspan) for every pane. Pure.

    The built-in panes keep their places inside the Astrabot group (its
    own grid). Added services fill two to a row in the outer grid, under
    the group, in the order they were added."""
    cells = dict(BUILTIN_CELLS)
    for index, key in enumerate(custom_keys):
        cells[key] = (FIRST_CUSTOM_ROW + index // 2, index % 2, 1)
    return cells


def inner_row_weights(collapsed) -> dict:
    """Vertical room for each row inside the Astrabot group. Pure.

    A row whose panes are all collapsed gets none, so the others grow
    into the space. The classifier row is a shorter row."""
    weights: dict[int, int] = {}
    for key, (row, _col, _span) in BUILTIN_CELLS.items():
        base = 2 if row == BUILTIN_CELLS["classifier"][0] else 3
        weights.setdefault(row, 0)
        if not collapsed.get(key):
            weights[row] = base
    return weights


def outer_row_weights(custom_keys, collapsed) -> dict:
    """Vertical room for the Astrabot group (row 0) and each row of added
    services. Pure. A collapsed group, or a row of collapsed services,
    gets none; an open group gets what its panes need."""
    weights = {0: 0 if collapsed.get(GROUP_KEY) else sum(inner_row_weights(collapsed).values())}
    for key, (row, _col, _span) in pane_cells(custom_keys).items():
        if key in BUILTIN_CELLS:
            continue
        weights.setdefault(row, 0)
        if not collapsed.get(key):
            weights[row] = 3
    return weights


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


# -- running as another Windows user ---------------------------------------------
def guest_command_line(command, env=None) -> str:
    """The command line to hand to the other account. Pure.

    A new process for another account starts with that account's own
    environment, not ours, so any variables set for the service are put
    in front with ``set`` inside cmd.exe."""
    line = command if isinstance(command, str) else subprocess.list2cmdline(list(command))
    sets = "".join(f'set "{k}={v}"&& ' for k, v in (env or {}).items())
    return f'cmd.exe /d /s /c "{sets}{line}"' if sets else line


def password_from_env(user, environ=None) -> str:
    """The account's password from the variable the service names; "" if none.

    The password is never written to console.json or the logs."""
    environ = os.environ if environ is None else environ
    var = str(user.get("password_env") or "").strip()
    return environ.get(var, "") if var else ""


if IS_WINDOWS:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _HANDLE = ctypes.c_void_p
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    class _STARTUPINFO(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                    ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                    ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                    ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                    ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.c_void_p), ("hStdInput", _HANDLE),
                    ("hStdOutput", _HANDLE), ("hStdError", _HANDLE)]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", _HANDLE), ("hThread", _HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]

    class _BASIC_LIMITS(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _EXTENDED_LIMITS(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC_LIMITS), ("IoInfo", _IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _kernel32.CreatePipe.argtypes = [ctypes.POINTER(_HANDLE), ctypes.POINTER(_HANDLE),
                                     ctypes.POINTER(_SECURITY_ATTRIBUTES), wintypes.DWORD]
    _kernel32.SetHandleInformation.argtypes = [_HANDLE, wintypes.DWORD, wintypes.DWORD]
    _kernel32.CloseHandle.argtypes = [_HANDLE]
    _kernel32.ResumeThread.argtypes = [_HANDLE]
    _kernel32.WaitForSingleObject.argtypes = [_HANDLE, wintypes.DWORD]
    _kernel32.GetExitCodeProcess.argtypes = [_HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.CreateJobObjectW.restype = _HANDLE
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.SetInformationJobObject.argtypes = [_HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                  wintypes.DWORD]
    _kernel32.AssignProcessToJobObject.argtypes = [_HANDLE, _HANDLE]
    _kernel32.TerminateJobObject.argtypes = [_HANDLE, wintypes.UINT]
    _kernel32.TerminateProcess.argtypes = [_HANDLE, wintypes.UINT]
    _advapi32.CreateProcessWithLogonW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.c_void_p,
        wintypes.LPCWSTR, ctypes.POINTER(_STARTUPINFO),
        ctypes.POINTER(_PROCESS_INFORMATION)]

    _LOGON_WITH_PROFILE = 0x1
    _CREATE_SUSPENDED = 0x4
    _CREATE_UNICODE_ENVIRONMENT = 0x400
    _CREATE_NO_WINDOW = 0x08000000
    _STARTF_USESTDHANDLES = 0x100
    _HANDLE_FLAG_INHERIT = 0x1
    _JOB_KILL_ON_CLOSE = 0x2000
    _JOB_EXTENDED_LIMITS = 9
    _WAIT_TIMEOUT = 0x102

    class GuestProcess:
        """A program running under another Windows account.

        Quacks like the parts of ``subprocess.Popen`` the console uses:
        ``pid``, ``stdout`` (text lines), ``stdin``, ``poll``, ``wait``
        and ``kill``. ``kill`` ends the whole job, so everything the
        program started goes with it, which ``taskkill`` could not do
        for another account's processes. The job also dies with the
        console, so a crashed console never leaves the server behind."""

        def __init__(self, process, thread, job, stdout, stdin, pid):
            self._process, self._thread, self._job = process, thread, job
            self.stdout, self.stdin, self.pid = stdout, stdin, pid
            self.returncode = None

        def poll(self):
            if self.returncode is not None:
                return self.returncode
            if _kernel32.WaitForSingleObject(self._process, 0) == _WAIT_TIMEOUT:
                return None
            code = wintypes.DWORD()
            _kernel32.GetExitCodeProcess(self._process, ctypes.byref(code))
            self.returncode = int(code.value)
            return self.returncode

        def wait(self, timeout=None):
            milliseconds = 0xFFFFFFFF if timeout is None else int(timeout * 1000)
            if _kernel32.WaitForSingleObject(self._process, milliseconds) == _WAIT_TIMEOUT:
                raise subprocess.TimeoutExpired("guest process", timeout)
            return self.poll()

        def kill(self):
            if self._job:
                _kernel32.TerminateJobObject(self._job, 1)
            elif self.poll() is None:
                _kernel32.TerminateProcess(self._process, 1)

    def _make_pipe(parent_reads: bool):
        """A pipe whose child end can be inherited and whose parent end cannot."""
        sa = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), None, True)
        read, write = _HANDLE(), _HANDLE()
        if not _kernel32.CreatePipe(ctypes.byref(read), ctypes.byref(write),
                                    ctypes.byref(sa), 0):
            raise ctypes.WinError(ctypes.get_last_error())
        parent, child = (read, write) if parent_reads else (write, read)
        _kernel32.SetHandleInformation(parent, _HANDLE_FLAG_INHERIT, 0)
        return parent, child

    def popen_as_user(command, cwd, username, password="", domain="", env=None):
        """Start ``command`` as another local or domain account. Raises OSError.

        The account needs "log on locally" rights and read/execute access
        to the program's folder (and write access wherever the program
        saves). A blank password only works for an account that really
        has none and that Windows allows to log on this way."""
        line = guest_command_line(command, env)
        buffer = ctypes.create_unicode_buffer(line)
        out_parent, out_child = _make_pipe(parent_reads=True)
        in_parent, in_child = _make_pipe(parent_reads=False)
        startup = _STARTUPINFO()
        startup.cb = ctypes.sizeof(_STARTUPINFO)
        startup.dwFlags = _STARTF_USESTDHANDLES
        startup.hStdInput, startup.hStdOutput, startup.hStdError = in_child, out_child, out_child
        info = _PROCESS_INFORMATION()
        ok = _advapi32.CreateProcessWithLogonW(
            username, domain or ".", password or "", _LOGON_WITH_PROFILE, None, buffer,
            _CREATE_SUSPENDED | _CREATE_UNICODE_ENVIRONMENT | _CREATE_NO_WINDOW, None,
            str(cwd) if cwd else None, ctypes.byref(startup), ctypes.byref(info))
        error = ctypes.get_last_error()
        # Our copies of the child's ends must close, or the pipe never reports its end.
        _kernel32.CloseHandle(out_child)
        _kernel32.CloseHandle(in_child)
        if not ok:
            _kernel32.CloseHandle(out_parent)
            _kernel32.CloseHandle(in_parent)
            who = f"{domain}\\{username}" if domain else username
            raise OSError(f"Windows could not start the program as {who}: "
                          f"{ctypes.FormatError(error).strip()} (error {error})")
        job = _kernel32.CreateJobObjectW(None, None)
        if job:
            limits = _EXTENDED_LIMITS()
            limits.BasicLimitInformation.LimitFlags = _JOB_KILL_ON_CLOSE
            _kernel32.SetInformationJobObject(job, _JOB_EXTENDED_LIMITS, ctypes.byref(limits),
                                              ctypes.sizeof(limits))
            if not _kernel32.AssignProcessToJobObject(job, info.hProcess):
                _kernel32.CloseHandle(job)
                job = None
        _kernel32.ResumeThread(info.hThread)
        stdout = os.fdopen(msvcrt.open_osfhandle(out_parent.value, os.O_RDONLY), "r",
                           encoding="utf-8", errors="replace")
        stdin = os.fdopen(msvcrt.open_osfhandle(in_parent.value, os.O_WRONLY), "w",
                          encoding="utf-8")
        return GuestProcess(info.hProcess, info.hThread, job, stdout, stdin, info.dwProcessId)

else:  # pragma: no cover - the console is Windows-only

    def popen_as_user(*_args, **_kwargs):
        raise OSError("Running as another user is only available on Windows.")
