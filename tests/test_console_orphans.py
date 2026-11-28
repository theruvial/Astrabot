"""The console must not leave its programs running after it is gone.

Found 2026-11-28: eight ssh.exe journal tails from consoles that had ended.
Each test runs tests/standin_console.py as its own process, ends it, and
looks at what is still running. Windows only, like the console.

Run from the repo root:  py -3.14 -m unittest tests.test_console_orphans -v
"""

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

STANDIN = Path(__file__).resolve().parent / "standin_console.py"


class Watched:
    """A handle on a process, opened while it is alive, so a reused PID can
    never be mistaken for it (or killed in its place)."""

    def __init__(self, pid):
        import ctypes
        from ctypes import wintypes

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        synchronize, terminate = 0x00100000, 0x0001
        self.handle = self.kernel.OpenProcess(synchronize | terminate, False, pid)
        if not self.handle:
            raise AssertionError(f"process {pid} was not running")

    def ended(self, within):
        return self.kernel.WaitForSingleObject(self.handle, int(within * 1000)) == 0

    def close(self):
        if not self.ended(0):
            self.kernel.TerminateProcess(self.handle, 1)
            self.ended(5)
        self.kernel.CloseHandle(self.handle)


@unittest.skipUnless(os.name == "nt", "the console and its job object are Windows only")
class ConsoleOrphanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def open_console(self, mode):
        """Start a stand-in; return it and a Watched for each PID it reports."""
        self.errors = open(self.folder / "standin.err", "w+", encoding="utf-8")
        self.addCleanup(self.errors.close)
        console = subprocess.Popen(
            [sys.executable, str(STANDIN), mode, str(self.folder)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.errors, encoding="utf-8")

        def close_console():
            if console.poll() is None:
                console.kill()
            console.wait()
            console.stdin.close()
            console.stdout.close()

        self.addCleanup(close_console)
        timer = threading.Timer(60, console.kill)  # a stuck stand-in fails the test
        timer.start()
        line = console.stdout.readline()
        timer.cancel()
        if not line.strip():
            self.errors.seek(0)
            self.fail(f"the stand-in console reported nothing:\n{self.errors.read()}")
        children = [Watched(int(pid)) for pid in line.split()]
        for child in children:
            self.addCleanup(child.close)
        return console, children

    def force_kill(self, console):
        """What Task Manager or a crash does: the console alone, no cleanup."""
        console.kill()
        console.wait(timeout=10)

    def test_a_force_killed_console_takes_the_librarian_tail_with_it(self):
        console, (tail,) = self.open_console("librarian")
        self.assertFalse(tail.ended(0))
        self.force_kill(console)
        self.assertTrue(tail.ended(10), "the librarian tail outlived the console")

    def test_a_force_killed_console_takes_the_strata_tunnel_with_it(self):
        console, (tunnel,) = self.open_console("tunnel")
        self.assertFalse(tunnel.ended(0))
        self.force_kill(console)
        self.assertTrue(tunnel.ended(10), "the SSH tunnel outlived the console")

    def test_a_force_killed_console_leaves_added_services_running(self):
        # Their stop commands let them save; ending them with the console
        # would skip that.
        console, (game,) = self.open_console("custom")
        self.force_kill(console)
        self.assertFalse(game.ended(2))

    def test_the_strata_server_outlives_the_console(self):
        console, (server, tail) = self.open_console("strata")
        self.force_kill(console)
        self.assertTrue(tail.ended(10), "the librarian tail outlived the console")
        self.assertFalse(server.ended(2), "closing the console stopped Strata")

    def test_a_watchdog_relaunch_stops_the_librarian_tail(self):
        console, (tail,) = self.open_console("watchdog")
        console.stdin.write("trip\n")
        console.stdin.flush()
        console.wait(timeout=60)
        self.assertTrue(tail.ended(10), "the librarian tail outlived the relaunch")
        log = (self.folder / "logs" / "crashes.log").read_text(encoding="utf-8")
        # Stopped by the console itself, and on record, like the other services.
        self.assertIn("Watchdog lost bot", log)
        self.assertIn("Librarian stopped", log)


if __name__ == "__main__":
    unittest.main()
