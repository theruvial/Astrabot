"""End the console's own programs when the console process ends (2026-11-28).

``Supervisor.shutdown()`` only runs when the console gets to say goodbye.
Force-killed or crashed, it runs no Python at all, and on Windows its
children live on. The programs with a port were stopped by the next console
(``kill_listeners``); the Automated Librarian's ``ssh`` journal tail has no
port, so one was left behind each time.

A Windows job object with "kill on close" is the part the kernel does for
us: this process holds the only handle, so when it ends, however it ends,
every process in the job ends too.

Programs are added one by one with ``tie(proc)``. The console itself is
never in the job: everything it started would join it, and that includes
the Strata server, which must keep running (console_strata.py). So Strata's
CLI calls are never tied, and neither are services added in Settings, which
have their own stop commands (a game server has to save first).
"""

from __future__ import annotations

import os
import threading

_KILL_ON_JOB_CLOSE = 0x2000
# A program that asks to leave the job (CREATE_BREAKAWAY_FROM_JOB, as some
# background hosts do) may. Without this its start would fail instead.
_BREAKAWAY_OK = 0x0800
_EXTENDED_LIMIT_INFORMATION = 9

_lock = threading.Lock()
_job = None


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               wintypes.LPVOID, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel


def _create_job(kernel):
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic),
                    ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    # No security attributes: the handle is not inheritable. A child holding
    # a copy would keep the job open after the console had gone.
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = Extended()
    limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE | _BREAKAWAY_OK
    if not kernel.SetInformationJobObject(job, _EXTENDED_LIMIT_INFORMATION,
                                          ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(job)
        raise error
    return job


def tie(proc) -> str:
    """Make ``proc`` (a Popen), and whatever it starts from now on, end when
    this process ends. Returns "" or why it could not; never raises, since
    a program that cannot be tied should still run. A no-op off Windows."""
    global _job
    if os.name != "nt":
        return ""
    import ctypes

    handle = getattr(proc, "_handle", None)
    if handle is None:
        return "it has no process handle"
    try:
        kernel = _kernel32()
        with _lock:
            if _job is None:
                _job = _create_job(kernel)  # kept open until this process ends
        if not kernel.AssignProcessToJobObject(_job, int(handle)):
            if proc.poll() is not None:
                return ""  # already over, so there is nothing to leave behind
            raise ctypes.WinError(ctypes.get_last_error())
    except OSError as exc:
        return str(exc)
    return ""
