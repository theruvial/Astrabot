"""Tests for the Golurk Console's extra services: config, commands, pane
layout, running a service under another account, and the supervisor
that runs an added service. No window is opened."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import console_services as cs
from astra_console import CustomSupervisor


def wait_for(condition, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


class ConfigTests(unittest.TestCase):
    def test_a_missing_file_gives_the_defaults(self):
        cfg, note = cs.load_console_config(Path(tempfile.gettempdir()) / "golurk-none.json")
        self.assertEqual(cfg["console_name"], "Golurk Console")
        self.assertEqual(cfg["services"], [])
        self.assertEqual(note, "")

    def test_a_broken_file_never_stops_the_console(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "console.json"
            path.write_text("{ not json", encoding="utf-8")
            cfg, note = cs.load_console_config(path)
        self.assertEqual(cfg["services"], [])
        self.assertIn("could not be read", note)

    def test_services_are_cleaned_and_named_uniquely(self):
        cfg = cs.merge_config({"services": [
            {"name": "dragon", "title": "Dragonwilds", "stop_input": "/quit"},
            {"name": "dragon"},
            {"name": "bot"},
        ]})
        names = [s["name"] for s in cfg["services"]]
        self.assertEqual(len(set(names)), 3)
        self.assertNotIn("bot", names)  # never clashes with a built-in window
        self.assertEqual(cfg["services"][0]["stop_input"], ["/quit"])
        self.assertNotIn("user", cfg["services"][0])

    def test_an_old_user_option_in_console_json_is_ignored(self):
        cfg = cs.merge_config({"services": [{"name": "d", "user": {"enabled": True,
                                                                    "username": "guest"}}]})
        self.assertNotIn("user", cfg["services"][0])

    def test_save_then_load_round_trips(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "console.json"
            cfg = cs.merge_config({"services": [{"name": "dragon", "command": "x.bat",
                                                 "run_as": "cmd"}]})
            cfg["collapsed"] = {"bot": True}
            cs.save_console_config(cfg, path)
            back, _ = cs.load_console_config(path)
        self.assertEqual(back["services"][0]["run_as"], "cmd")
        self.assertEqual(back["collapsed"], {"bot": True})

    def test_saving_settings_can_only_change_added_services(self):
        raw = cs.merge_config({"services": [{"name": "a"}, {"name": "b"}],
                               "collapsed": {"wiki": True}})
        out = cs.build_saved_config(raw, {"console_name": "Mine"}, [{"name": "a"}])
        self.assertEqual([s["name"] for s in out["services"]], ["a"])
        self.assertEqual(out["console_name"], "Mine")
        self.assertEqual(out["collapsed"], {"wiki": True})
        self.assertNotIn("bot", json.dumps(out["services"]))

class CommandTests(unittest.TestCase):
    def test_cmd_wraps_a_batch_file(self):
        argv = cs.command_to_argv('"D:\\Servers\\start.bat"', "cmd")
        self.assertIn("cmd.exe /d /s /c", argv if isinstance(argv, str) else " ".join(argv))

    def test_powershell_lines_join_with_semicolons(self):
        argv = cs.command_to_argv("cd x\nrun it", "powershell")
        self.assertEqual(argv[-1], "cd x; run it")

    def test_continuation_lines_are_one_command(self):
        self.assertEqual(cs.join_continuations(["a `", "b"], "powershell"), ["a b"])

    def test_empty_command_means_monitor_only(self):
        self.assertEqual(cs.command_to_argv("  ", "program"), [])


class CheckTests(unittest.TestCase):
    def test_a_missing_folder_is_reported(self):
        problems = cs.check_entry({"command": "x", "run_as": "cmd", "cwd": "Z:/nope/nope"})
        self.assertTrue(any("Working folder" in p for p in problems))


class LayoutTests(unittest.TestCase):
    def test_built_in_panes_keep_their_places(self):
        cells = cs.pane_cells([])
        self.assertEqual(cells["bot"], (0, 0, 1))
        self.assertEqual(cells["classifier"], (2, 0, 1))
        self.assertEqual(cells["portfolio"], (2, 1, 1))

    def test_added_services_fill_two_per_row_underneath(self):
        cells = cs.pane_cells(["a", "b", "c"])
        self.assertEqual(cells["a"], (1, 0, 1))
        self.assertEqual(cells["b"], (1, 1, 1))
        self.assertEqual(cells["c"], (2, 0, 1))

    def test_a_row_of_collapsed_panes_gives_its_room_back(self):
        self.assertEqual(cs.inner_row_weights({}), {0: 3, 1: 3, 2: 2})
        collapsed = {"absLogin": True, "wiki": True}
        self.assertEqual(cs.inner_row_weights(collapsed)[1], 0)
        self.assertEqual(cs.inner_row_weights({"absLogin": True})[1], 3)

    def test_the_astrabot_group_collapses_as_one(self):
        self.assertEqual(cs.outer_row_weights([], {})[0], 8)
        self.assertEqual(cs.outer_row_weights([], {"astrabot": True})[0], 0)
        # Collapsing some panes inside the group shrinks the group's share.
        self.assertEqual(cs.outer_row_weights([], {"wiki": True, "absLogin": True})[0], 5)

    def test_added_services_sit_below_the_group(self):
        weights = cs.outer_row_weights(["a", "b", "c"], {"a": True, "b": True})
        self.assertEqual(weights, {0: 8, 1: 0, 2: 3})
        self.assertEqual(cs.outer_row_weights(["a"], {"astrabot": True}), {0: 0, 1: 3})

    def test_the_built_in_windows_are_the_six_we_ship(self):
        self.assertEqual(set(cs.BUILTIN_PANES),
                         {"bot", "site", "absLogin", "wiki", "classifier", "portfolio"})

class CustomSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lines = []
        self.services = []

    def make(self, **spec):
        spec.setdefault("name", "demo")
        spec.setdefault("title", "Demo")
        svc = CustomSupervisor(lambda stream, line: self.lines.append((stream, line)),
                               spec, log_dir=self.tmp.name)
        svc.restart_delay = 0.1
        svc.stop_pause = 0
        self.services.append(svc)
        self.addCleanup(svc.shutdown)
        return svc

    def script(self, body):
        path = Path(self.tmp.name) / f"s{len(self.services)}.py"
        path.write_text(body, encoding="utf-8")
        return subprocess.list2cmdline([sys.executable, "-u", str(path)])

    def said(self, text):
        return any(text in line for _, line in self.lines)

    def test_it_runs_and_shows_output_in_its_own_pane(self):
        svc = self.make(command=self.script("print('hello pane')\nimport time; time.sleep(30)"))
        svc.start()
        self.assertTrue(wait_for(lambda: ("demo", "hello pane") in self.lines))
        self.assertIn("Running", svc.status())

    def test_stop_leaves_it_stopped_until_started_again(self):
        svc = self.make(command=self.script("print('up', flush=True)\nimport time; time.sleep(60)"))
        svc.start()
        self.assertTrue(wait_for(lambda: ("demo", "up") in self.lines))
        svc.stop()
        self.assertTrue(wait_for(lambda: svc.status() == "Stopped"))
        time.sleep(0.5)
        self.assertEqual(svc.status(), "Stopped")  # not restarted behind our back
        before = sum(1 for _, line in self.lines if line == "up")
        svc.start()
        self.assertTrue(wait_for(
            lambda: sum(1 for _, line in self.lines if line == "up") > before))

    def test_typed_stop_commands_let_the_program_exit_cleanly(self):
        svc = self.make(
            stop_input=["/save", "/quit"], stop_grace=15,
            command=self.script(
                "import sys\nprint('ready', flush=True)\n"
                "for line in sys.stdin:\n"
                "    print('got ' + line.strip(), flush=True)\n"
                "    if line.strip() == '/quit':\n"
                "        sys.exit(0)\n"))
        svc.start()
        self.assertTrue(wait_for(lambda: ("demo", "ready") in self.lines))
        svc.stop()
        self.assertTrue(wait_for(lambda: self.said("stopped, exit code 0")))
        self.assertTrue(self.said("got /save"))
        self.assertTrue(self.said("got /quit"))

    def test_a_program_that_ignores_stop_commands_is_ended(self):
        svc = self.make(
            stop_input=["/quit"], stop_grace=1,
            command=self.script("import sys, time\nprint('stubborn', flush=True)\n"
                                "time.sleep(60)"))
        svc.start()
        self.assertTrue(wait_for(lambda: ("demo", "stubborn") in self.lines))
        svc.stop()
        self.assertTrue(wait_for(lambda: svc.status() == "Stopped", seconds=20))
        self.assertTrue(self.said("did not stop within"))

    def test_a_program_that_cannot_start_says_why(self):
        svc = self.make(command='"Z:\\no\\such\\program.exe" --x')
        svc.start()
        self.assertTrue(wait_for(lambda: self.said("Could not start Demo")))

    def test_start_with_no_command_explains_instead_of_crashing(self):
        svc = self.make(command="")
        svc.start()
        self.assertTrue(self.said("No command set"))
        self.assertEqual(svc.status(), "Stopped")

    def test_an_added_service_is_not_lost_before_it_starts(self):
        svc = self.make(command="x")
        self.assertFalse(svc.is_lost())

    def test_environment_variables_reach_the_program(self):
        svc = self.make(env={"GOLURK_TEST": "yes"},
                        command=self.script("import os; print('env=' + os.environ['GOLURK_TEST'])"))
        svc.start()
        self.assertTrue(wait_for(lambda: ("demo", "env=yes") in self.lines))

    def test_reconfigure_changes_what_runs_next_time(self):
        svc = self.make(command=self.script("print('old')"))
        svc.reconfigure({"name": "demo", "title": "Renamed",
                         "command": self.script("print('new')")})
        self.assertEqual(svc.title, "Renamed")
        svc.start()
        self.assertTrue(wait_for(lambda: ("demo", "new") in self.lines))


if __name__ == "__main__":
    unittest.main()
