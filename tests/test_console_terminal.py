"""The embedded terminal (console_terminal.py) and Strata controls
(console_strata.py): the pure parts, plus one real pyte screen."""

import subprocess
import sys
import unittest
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import console_strata as strata
import console_terminal as term

Cell = namedtuple("Cell", "data fg bg bold reverse")


def cell(data, fg="default", bg="default", bold=False, reverse=False):
    return Cell(data, fg, bg, bold, reverse)


class ScreenRunsTests(unittest.TestCase):
    def test_cells_with_the_same_look_join_into_one_run(self):
        line = {0: cell("a"), 1: cell("b"), 2: cell("c", fg="red")}
        self.assertEqual(term.screen_runs(line, 4), [
            ("ab", term.DEFAULT_FG, term.DEFAULT_BG, False),
            ("c", term.NAMED["red"], term.DEFAULT_BG, False),
            (" ", term.DEFAULT_FG, term.DEFAULT_BG, False)])

    def test_truecolour_passes_through_and_reverse_swaps(self):
        line = {0: cell("x", fg="34d399", bg="121212", reverse=True)}
        self.assertEqual(term.screen_runs(line, 1), [("x", "121212", "34d399", False)])

    def test_the_cursor_is_a_reversed_cell(self):
        runs = term.screen_runs({0: cell("a"), 1: cell("b")}, 2, cursor_col=1)
        self.assertEqual(runs[1], ("b", term.DEFAULT_BG, term.DEFAULT_FG, False))

    def test_the_second_half_of_a_wide_character_adds_nothing(self):
        runs = term.screen_runs({0: cell("\u4e2d"), 1: cell(""), 2: cell("x")}, 3)
        self.assertEqual(runs, [("\u4e2dx", term.DEFAULT_FG, term.DEFAULT_BG, False)])

    def test_unknown_colours_fall_back_to_the_default(self):
        self.assertEqual(term.colour("chartreuse-ish", "abcdef"), "abcdef")
        self.assertEqual(term.colour("BrightRed", "abcdef"), term.NAMED["brightred"])

    @unittest.skipIf(term.pyte is None, "pyte is not installed")
    def test_a_real_pyte_screen_renders_colour_and_position(self):
        screen = term.pyte.Screen(10, 2)
        stream = term.pyte.Stream(screen)
        stream.feed("\x1b[2;3H\x1b[1;38;2;52;211;153mok\x1b[0m")
        runs = term.screen_runs(screen.buffer[1], 10)
        self.assertEqual(runs[0], ("  ", term.DEFAULT_FG, term.DEFAULT_BG, False))
        self.assertEqual(runs[1], ("ok", "34d399", term.DEFAULT_BG, True))
        stream.feed("\x1b[?1000h\x1b[?1006h")
        self.assertIn(term.MOUSE_CLICKS, screen.mode)
        self.assertIn(term.MOUSE_SGR, screen.mode)


class KeyTests(unittest.TestCase):
    def test_keys_a_tui_needs(self):
        k = term.key_sequence
        self.assertEqual(k("Up", ""), "\x1b[A")
        self.assertEqual(k("Prior", ""), "\x1b[5~")
        self.assertEqual(k("Return", "\r"), "\r")
        self.assertEqual(k("BackSpace", "\x08"), "\x7f")
        self.assertEqual(k("Escape", "\x1b"), "\x1b")
        self.assertEqual(k("F1", ""), "\x1bOP")
        self.assertEqual(k("q", "q"), "q")
        self.assertEqual(k("2", "2"), "2")

    def test_modifiers(self):
        k = term.key_sequence
        self.assertEqual(k("Tab", "\t", term.SHIFT), "\x1b[Z")
        self.assertEqual(k("c", "\x03", term.CONTROL), "\x03")
        self.assertEqual(k("C", "", term.CONTROL | term.SHIFT), "\x03")
        self.assertEqual(k("x", "x", term.ALT), "\x1bx")

    def test_a_bare_modifier_sends_nothing(self):
        self.assertEqual(term.key_sequence("Shift_L", ""), "")
        self.assertEqual(term.key_sequence("Control_L", "", term.CONTROL), "")


class MouseAndSizeTests(unittest.TestCase):
    def test_sgr_reports_are_one_based(self):
        self.assertEqual(term.mouse_sequence(0, 0, 0), "\x1b[<0;1;1M")
        self.assertEqual(term.mouse_sequence(0, 9, 4, pressed=False), "\x1b[<0;10;5m")
        self.assertEqual(term.mouse_sequence(65, 2, 3), "\x1b[<65;3;4M")

    def test_old_x10_reports_and_their_limit(self):
        self.assertEqual(term.mouse_sequence(0, 0, 0, sgr=False), "\x1b[M !!")
        self.assertEqual(term.mouse_sequence(0, 0, 0, pressed=False, sgr=False), "\x1b[M#!!")
        self.assertEqual(term.mouse_sequence(0, 300, 0, sgr=False), "")

    def test_a_font_with_one_cell_block_glyphs_wins(self):
        self.assertEqual(term.pick_font(["Arial", "Consolas", "Cascadia Mono"]),
                         ("Cascadia Mono", 10))
        self.assertEqual(term.pick_font(["Consolas"]), ("Consolas", 10))
        self.assertEqual(term.pick_font([]), ("Courier New", 10))

    def test_grid_size_fits_the_widget_with_a_floor(self):
        self.assertEqual(term.grid_size(800, 450, 8, 15), (30, 100))
        self.assertEqual(term.grid_size(10, 10, 8, 15), (10, 40))


PLAN_STOP = {"ok": True, "plan": {"state": "ready", "owner_id": "own1", "instance_id": "inst1"}}
PLAN_RESTART = {"ok": True, "state": "ready", "current_owner_id": "own1",
                "current_instance_id": "inst1", "current_source_sha256": "aa",
                "candidate_source_sha256": "aa", "source_switch": False,
                "source_migration": False}


class StrataPureTests(unittest.TestCase):
    def test_every_call_is_json_with_the_profile(self):
        argv = strata.runtime_argv(r"C:\S", "launch-x", "stop", "--plan")
        self.assertEqual(argv[1:], ["strata.py", "runtime", "stop", "--profile", "launch-x",
                                    "--plan", "--json"])
        self.assertTrue(argv[0].endswith(str(Path("python") / "python.exe")))

    def test_parse_reply_skips_log_lines_and_rejects_junk(self):
        self.assertEqual(strata.parse_reply('note\n{"ok": true}'), {"ok": True})
        self.assertIsNone(strata.parse_reply("Traceback ..."))
        self.assertIsNone(strata.parse_reply("[1, 2]"))

    def test_summaries_never_show_ids_or_tokens(self):
        reply = dict(PLAN_RESTART, token="secret", api_url="http://127.0.0.1:8080",
                     readiness_state="generation_ready")
        line = strata.summarise(reply)
        self.assertEqual(line, "ready, generation_ready, http://127.0.0.1:8080")
        self.assertNotIn("secret", strata.summarise(PLAN_STOP) + line)
        self.assertNotIn("own1", strata.summarise(PLAN_STOP))
        self.assertEqual(strata.summarise({"ok": False, "error": {"message": "busy"}}),
                         "failed: busy")

    def test_stop_uses_the_plan_ids(self):
        self.assertEqual(strata.stop_args(PLAN_STOP),
                         ["--if-owner-id", "own1", "--if-instance-id", "inst1"])
        self.assertIsNone(strata.stop_args({"ok": True, "plan": {"state": "stopped"}}))

    def test_restart_uses_the_plan_ids_and_waits_for_the_model(self):
        args, reason = strata.restart_args(PLAN_RESTART)
        self.assertEqual(reason, "")
        self.assertEqual(args, ["--if-owner-id", "own1", "--if-instance-id", "inst1",
                                "--startup-wait", "600"])

    def test_an_edited_launch_file_is_allowed_with_its_checksum(self):
        plan = dict(PLAN_RESTART, candidate_source_sha256="bb", source_migration=True)
        args, _ = strata.restart_args(plan)
        self.assertEqual(args[-3:], ["--allow-source-migration", "--if-source-sha256", "bb"])

    def test_restart_refuses_a_source_switch_or_nothing_running(self):
        self.assertIsNone(strata.restart_args(dict(PLAN_RESTART, source_switch=True))[0])
        args, reason = strata.restart_args({"ok": True, "state": "stopped"})
        self.assertIsNone(args)
        self.assertIn("Start", reason)
        self.assertIsNone(strata.restart_args({"ok": False, "error": "x"})[0])


class StrataControlTests(unittest.TestCase):
    def make(self, replies):
        self.calls, self.said = [], []
        replies = list(replies)

        def run(argv):
            self.calls.append(argv[3:])
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        return strata.StrataControl(r"C:\S", "launch-x", self.said.append, run=run)

    def test_stop_plans_then_stops_only_that_instance(self):
        control = self.make([PLAN_STOP, {"ok": True, "state": "stopped"}])
        self.assertTrue(control.stop())
        self.assertIn("--plan", self.calls[0])
        self.assertEqual(self.calls[1][:1], ["stop"])
        self.assertIn("own1", self.calls[1])
        self.assertIn("inst1", self.calls[1])

    def test_stop_when_nothing_runs_does_not_call_stop_again(self):
        control = self.make([{"ok": True, "plan": {"state": "stopped"}}])
        self.assertTrue(control.stop())
        self.assertEqual(len(self.calls), 1)
        self.assertIn("not running", self.said[-1])

    def test_restart_refused_never_runs_the_restart(self):
        control = self.make([dict(PLAN_RESTART, source_switch=True)])
        self.assertFalse(control.restart())
        self.assertEqual(len(self.calls), 1)
        self.assertIn("refused", self.said[-1])

    def test_restart_runs_with_the_guards(self):
        control = self.make([PLAN_RESTART, {"ok": True, "state": "ready"}])
        self.assertTrue(control.restart())
        self.assertNotIn("--plan", self.calls[1])
        self.assertIn("--startup-wait", self.calls[1])

    def test_a_timeout_or_missing_python_is_a_message_not_a_crash(self):
        control = self.make([subprocess.TimeoutExpired("x", 1)])
        self.assertFalse(control.start())
        self.assertIn("no answer", self.said[-1])
        control = self.make([FileNotFoundError("python.exe")])
        self.assertFalse(control.start())
        self.assertIn("could not run Strata", self.said[-1])


if __name__ == "__main__":
    unittest.main()
