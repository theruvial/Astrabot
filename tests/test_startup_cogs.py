"""Tests for choosing the startup cogs from config.json.

Run from the repo root:  python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from startup_cogs import DEFAULT_COGS, extensions_from_config


class StartupCogsTests(unittest.TestCase):
    def test_the_list_in_config_decides_and_keeps_its_order(self):
        self.assertEqual(extensions_from_config({"cogs": ["wiki", "tasks"]}),
                         ["cogs.wiki", "cogs.tasks"])

    def test_short_and_full_names_are_the_same(self):
        self.assertEqual(extensions_from_config({"cogs": ["tasks", "cogs.tasks", " clip "]}),
                         ["cogs.tasks", "cogs.clip"])

    def test_no_key_uses_the_defaults(self):
        self.assertEqual(extensions_from_config({}), [f"cogs.{n}" for n in DEFAULT_COGS])

    def test_an_empty_list_loads_nothing(self):
        self.assertEqual(extensions_from_config({"cogs": []}), [])

    def test_a_non_list_falls_back_to_the_defaults(self):
        self.assertEqual(extensions_from_config({"cogs": "tasks"}),
                         [f"cogs.{n}" for n in DEFAULT_COGS])

    def test_bad_entries_are_skipped_not_fatal(self):
        bad = ["", "../evil", "os.system", "cogs.", 5, None, "a b", "ok"]
        with self.assertLogs("startup_cogs", level="WARNING"):
            self.assertEqual(extensions_from_config({"cogs": bad}), ["cogs.ok"])

    def test_the_default_names_are_real_cog_files(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in DEFAULT_COGS:
            self.assertTrue(os.path.isfile(os.path.join(root, "cogs", f"{name}.py")), name)

    def test_the_example_config_lists_only_real_cogs(self):
        import json
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "config.example.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertIn("cogs", cfg)
        for ext in extensions_from_config(cfg):
            self.assertTrue(os.path.isfile(os.path.join(root, *ext.split(".")) + ".py"), ext)


if __name__ == "__main__":
    unittest.main()
