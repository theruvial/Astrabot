"""Tests for astra_console.py and the cog reload it triggers.

Run from the repo root:  python -m unittest discover -s tests -v
The supervisor tests run a small fake bot, no Discord token needed.
"""

import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import astra_console
from astra_console import BotSupervisor, Supervisor, route_line
from cogs import moderation


class RouteLineTests(unittest.TestCase):
    def test_site_loggers_go_to_site(self):
        self.assertEqual(route_line("[2026-11-05 21:00:00] [INFO    ] aiohttp.access: GET /"), "site")
        self.assertEqual(route_line("[2026-11-05 21:00:00] [WARNING ] audiobooks.web: x"), "site")

    def test_other_loggers_and_prints_go_to_bot(self):
        self.assertEqual(route_line("[2026-11-05 21:00:00] [INFO    ] discord.client: x", "site"), "bot")
        self.assertEqual(route_line("Logged in as Astrabot", "site"), "bot")

    def test_traceback_follows_the_line_above(self):
        self.assertEqual(route_line("Traceback (most recent call last):", "site"), "site")
        self.assertEqual(route_line('  File "x.py", line 1', "site"), "site")
        self.assertEqual(route_line("RuntimeError: boom", "site"), "site")


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lines = []
        self.supervisor = None

    def tearDown(self):
        if self.supervisor is not None:
            self.supervisor.shutdown()
        self.tmp.cleanup()

    def run_bot(self, source):
        (self.root / "bot.py").write_text(source, encoding="utf-8")
        self.supervisor = BotSupervisor(
            lambda stream, line: self.lines.append((stream, line)),
            root=self.root, command=[sys.executable, "-u", "bot.py"], restart_delay=0)
        self.supervisor.start()

    def wait_for(self, condition, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.05)
        self.fail(f"timed out; lines: {self.lines}")

    def saw(self, text):
        return any(text in line for _, line in self.lines)

    def crash_log(self):
        path = self.root / "logs" / "crashes.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_output_arrives_while_running_and_emoji_survive(self):
        self.run_bot('import time\nprint("Book Club \\U0001F4DA")\ntime.sleep(60)\n')
        self.wait_for(lambda: self.saw("Book Club \U0001F4DA"))
        self.assertIn("Running", self.supervisor.status())
        log = (self.root / "logs" / "bot.log").read_text(encoding="utf-8")
        self.assertIn("Book Club \U0001F4DA", log)

    def test_clean_exit_stays_stopped(self):
        self.run_bot('print("bye")\n')
        self.wait_for(lambda: "exit code 0" in self.crash_log())
        time.sleep(0.3)
        self.assertEqual(self.crash_log().count("Starting bot"), 1)

    def test_gives_up_after_five_crashes(self):
        self.run_bot('raise SystemExit(3)\n')
        self.wait_for(lambda: "not restarting" in self.crash_log())
        self.assertEqual(self.crash_log().count("Starting bot"), 5)

    def test_manual_restart_is_not_a_crash(self):
        self.run_bot('import time\nprint("up")\ntime.sleep(60)\n')
        self.wait_for(lambda: self.saw("up"))
        self.supervisor.restart()
        self.wait_for(lambda: self.crash_log().count("Starting bot") == 2)
        self.assertFalse(self.saw("Crash 1"))

    def test_reload_site_refused_when_bot_is_down(self):
        self.run_bot('print("bye")\n')
        self.wait_for(lambda: "exit code 0" in self.crash_log())
        self.assertFalse(self.supervisor.reload_site())
        self.assertFalse(self.supervisor.flag.exists())

    def test_reload_site_writes_cog_name(self):
        self.run_bot('import time\nprint("up")\ntime.sleep(60)\n')
        self.wait_for(lambda: self.saw("up"))
        self.assertTrue(self.supervisor.reload_site())
        self.assertEqual(self.supervisor.flag.read_text(encoding="utf-8"), "audiobooks")


class WikiSupervisorTests(unittest.TestCase):
    def test_output_goes_to_its_own_pane_and_log(self):
        lines = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "server.py").write_text(
                'print("Traceback lookalike [INFO    ] audiobooks: x")\n', encoding="utf-8")
            wiki = Supervisor(lambda stream, line: lines.append((stream, line)), "wiki",
                              root, [sys.executable, "-u", "server.py"], root / "logs")
            wiki.start()
            deadline = time.monotonic() + 30
            while not any("exit code 0" in line for _, line in lines):
                self.assertLess(time.monotonic(), deadline, lines)
                time.sleep(0.05)
            wiki.shutdown()
            self.assertEqual({stream for stream, _ in lines}, {"wiki"})
            self.assertIn("lookalike", (root / "logs" / "wiki.log").read_text(encoding="utf-8"))


class AbsLoginSupervisorTests(unittest.TestCase):
    def test_runs_app_py_from_the_abslogin_folder(self):
        service = astra_console.abslogin_supervisor(lambda stream, line: None)
        self.assertEqual(service.cwd, astra_console.ABSLOGIN_DIR)
        self.assertEqual(service.command[-1], "app.py")
        self.assertEqual(service.name, "absLogin")

    def test_restart_is_refused_if_it_never_started(self):
        lines = []
        service = astra_console.abslogin_supervisor(lambda stream, line: lines.append(line))
        service.restart()
        self.assertTrue(any("not found" in line for line in lines))


class PullTests(unittest.TestCase):
    def pull(self, returncode):
        result = types.SimpleNamespace(returncode=returncode, stdout="ok", stderr="")
        lines = []
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(astra_console, "ABSLOGIN_DIR", Path(tmp)), \
                mock.patch.object(astra_console, "WIKI_DIR", Path(tmp)), \
                mock.patch.object(astra_console.subprocess, "run", return_value=result) as run:
            supervisor = BotSupervisor(lambda stream, line: lines.append(line), root=tmp)
            return supervisor.pull_latest(), run, lines

    def test_pulls_all_three_repos_fast_forward_only(self):
        ok, run, _ = self.pull(0)
        self.assertTrue(ok)
        self.assertEqual(run.call_count, 3)
        for call in run.call_args_list:
            self.assertIn("--ff-only", call.args[0])

    def test_reports_failure(self):
        ok, _, lines = self.pull(1)
        self.assertFalse(ok)
        self.assertTrue(any("Pull failed" in line for line in lines))


class PullAndRestartTests(unittest.TestCase):
    def run_with(self, pulled):
        bot = mock.Mock()
        bot.pull_latest.return_value = pulled
        wiki = mock.Mock()
        return astra_console.pull_and_restart(bot, [bot, wiki]), bot, wiki

    def test_restarts_everything_after_a_good_pull(self):
        ok, bot, wiki = self.run_with(True)
        self.assertTrue(ok)
        bot.restart.assert_called_once()
        wiki.restart.assert_called_once()

    def test_failed_pull_restarts_nothing(self):
        ok, bot, wiki = self.run_with(False)
        self.assertFalse(ok)
        bot.restart.assert_not_called()
        wiki.restart.assert_not_called()


class ReloadFlagTests(unittest.IsolatedAsyncioTestCase):
    async def test_flag_reloads_the_named_cog_once(self):
        calls = []

        async def reload_extension(name):
            calls.append(name)

        bot = types.SimpleNamespace(reload_extension=reload_extension)
        with tempfile.TemporaryDirectory() as tmp:
            flag = Path(tmp) / "reload_cog.flag"
            flag.write_text("audiobooks", encoding="utf-8")
            with mock.patch.object(moderation, "RELOAD_FLAG", flag):
                cog = moderation.Moderation(bot)
                await cog._check_reload_flag()
                await cog._check_reload_flag()  # flag consumed: no second reload
            self.assertFalse(flag.exists())
        self.assertEqual(calls, ["cogs.audiobooks"])


if __name__ == "__main__":
    unittest.main()
