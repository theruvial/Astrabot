"""Tests for the self-update watcher cog.

Run from the repo root:  python -m unittest discover -s tests -v
The mapping tests need no git. The pull/reload test builds a real
local git repo + bare remote in a temp dir and is skipped if git
is not available. No network, Discord token, or GitHub API is used:
the watcher works by git fetch/pull on the host checkout.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs.selfupdate import SelfUpdate, map_changed_to_extensions


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


class MappingTests(unittest.TestCase):
    def test_helper_file_maps_to_its_cog(self):
        files = {
            "cogs.audiobooks": ["cogs/audiobooks.py", "cogs/audiobooks_db.py",
                                "cogs/audiobooks_web.py"],
            "cogs.clip": ["cogs/clip.py"],
        }
        self.assertEqual(
            map_changed_to_extensions(["cogs/audiobooks_db.py"], files),
            {"cogs.audiobooks"})

    def test_unrelated_files_map_to_nothing(self):
        files = {"cogs.audiobooks": ["cogs/audiobooks.py"]}
        self.assertEqual(
            map_changed_to_extensions(
                ["bot.py", "config.json", "README.md", "cogs/other.py"],
                files),
            set())


class FakeBot:
    def __init__(self, extensions):
        self.extensions = extensions
        self.config = {}
        self.reloaded = []

    async def reload_extension(self, name):
        self.reloaded.append(name)

    async def is_owner(self, user):
        return False


@unittest.skipUnless(shutil.which("git"), "git not available")
class PullReloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.remote = os.path.join(self.tmp, "remote.git")
        subprocess.run(["git", "init", "--bare", self.remote],
                       check=True, capture_output=True)
        self.repo = os.path.join(self.tmp, "bot")
        subprocess.run(["git", "clone", self.remote, self.repo],
                       check=True, capture_output=True)
        git("config", "user.email", "test@example.com", cwd=self.repo)
        git("config", "user.name", "Test", cwd=self.repo)
        os.makedirs(os.path.join(self.repo, "cogs"))
        with open(os.path.join(self.repo, "cogs", "audiobooks.py"), "w") as f:
            f.write("# v1\n")
        with open(os.path.join(self.repo, "cogs", "audiobooks_db.py"), "w") as f:
            f.write("# db v1\n")
        git("add", ".", cwd=self.repo)
        git("commit", "-m", "initial", cwd=self.repo)
        git("push", "origin", "HEAD:master", cwd=self.repo)
        # A second clone makes the upstream change.
        self.other = os.path.join(self.tmp, "other")
        subprocess.run(["git", "clone", self.remote, self.other],
                       check=True, capture_output=True)
        git("config", "user.email", "test@example.com", cwd=self.other)
        git("config", "user.name", "Test", cwd=self.other)

        module = types.ModuleType("cogs.audiobooks")
        module.__file__ = os.path.join(self.repo, "cogs", "audiobooks.py")
        sys.modules["cogs.audiobooks"] = module
        self._module = module
        self.bot = FakeBot({"cogs.audiobooks": module})
        self.cog = SelfUpdate(self.bot, config_override={})
        # resolve(): on Windows the temp folder has a short 8.3 name (THERUV~1) that
        # the cog's own resolved paths would not match.
        self.cog.repo_root = __import__("pathlib").Path(self.repo).resolve()

    def tearDown(self):
        sys.modules.pop("cogs.audiobooks", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_cog_change_pulls_nothing(self):
        reloaded, status = asyncio.run(self.cog.check_and_update())
        self.assertEqual(status, "no_cog_changes")
        self.assertEqual(reloaded, set())
        self.assertEqual(self.bot.reloaded, [])

    def test_cog_change_pulls_and_reloads_only_that_cog(self):
        with open(os.path.join(self.other, "cogs", "audiobooks_db.py"), "w") as f:
            f.write("# db v2\n")
        git("add", ".", cwd=self.other)
        git("commit", "-m", "db change", cwd=self.other)
        git("push", "origin", "HEAD:master", cwd=self.other)
        reloaded, status = asyncio.run(self.cog.check_and_update())
        self.assertEqual(status, "updated")
        self.assertEqual(reloaded, {"cogs.audiobooks"})
        self.assertEqual(self.bot.reloaded, ["cogs.audiobooks"])
        with open(os.path.join(self.repo, "cogs", "audiobooks_db.py")) as f:
            self.assertEqual(f.read(), "# db v2\n")

    def test_non_cog_change_pulls_nothing(self):
        with open(os.path.join(self.other, "README.md"), "w") as f:
            f.write("docs\n")
        git("add", ".", cwd=self.other)
        git("commit", "-m", "docs", cwd=self.other)
        git("push", "origin", "HEAD:master", cwd=self.other)
        reloaded, status = asyncio.run(self.cog.check_and_update())
        self.assertEqual(status, "no_cog_changes")
        self.assertEqual(self.bot.reloaded, [])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "README.md")))


if __name__ == "__main__":
    unittest.main()
