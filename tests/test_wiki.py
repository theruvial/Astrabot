"""Tests for the /wiki cog.

Run from the repo root:  python -m unittest discover -s tests -v
No network, Discord token, or bot needed: the command is exercised
with a fake interaction that records what it was sent.
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cogs.wiki as wiki_mod
from cogs.wiki import Wiki, api_config, wiki_file_path


class FakeResponse:
    def __init__(self):
        self.sent = []

    async def send_message(self, content=None, file=None, ephemeral=False):
        self.sent.append({"content": content, "file": file, "ephemeral": ephemeral})


class FakeInteraction:
    def __init__(self):
        self.response = FakeResponse()


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class WikiTests(unittest.TestCase):
    def test_wiki_file_is_the_standalone_file(self):
        path = wiki_file_path()
        self.assertTrue(path.is_file(), "cogs/wiki.html is missing")
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("api.theruvial.com", text)
        self.assertNotIn("fetch(", text)
        self.assertIn("Aboard the Astra", text)

    def test_wiki_command_sends_the_file(self):
        cog = Wiki(bot=None)
        interaction = FakeInteraction()
        # Call the underlying callback (app_commands wraps it).
        run(cog.wiki.callback(cog, interaction))
        self.assertEqual(len(interaction.response.sent), 1)
        sent = interaction.response.sent[0]
        self.assertEqual(sent["content"], "Aboard the Astra — Mechanics Wiki")
        self.assertEqual(sent["file"].filename, "astra-wiki.html")
        self.assertFalse(sent["ephemeral"])

    def test_wiki_command_uses_backend_when_key_configured(self):
        async def fake_fetch(api_url, api_key):
            self.assertEqual(api_key, "testkey123")
            return b"<html>fresh from backend</html>"

        with patch.dict(os.environ, {"WIKI_API_KEY": "testkey123"}), patch.object(
            wiki_mod, "fetch_backend_html", side_effect=fake_fetch
        ):
            cog = Wiki(bot=None)
            interaction = FakeInteraction()
            run(cog.wiki.callback(cog, interaction))
            sent = interaction.response.sent[0]
            self.assertEqual(sent["file"].filename, "astra-wiki.html")
            self.assertEqual(sent["file"].fp.read(), b"<html>fresh from backend</html>")

    def test_wiki_command_falls_back_to_bundled_file_when_backend_fails(self):
        with patch.dict(os.environ, {"WIKI_API_KEY": "testkey123"}), patch.object(
            wiki_mod, "fetch_backend_html", AsyncMock(return_value=None)
        ):
            cog = Wiki(bot=None)
            interaction = FakeInteraction()
            run(cog.wiki.callback(cog, interaction))
            sent = interaction.response.sent[0]
            self.assertEqual(sent["file"].filename, "astra-wiki.html")
            self.assertFalse(sent["ephemeral"])

    def test_api_config_defaults_to_loopback_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            url, key = api_config()
            self.assertEqual(url, "http://127.0.0.1:13382")
            self.assertIsNone(key)

    def test_wiki_command_errors_ephemerally_when_file_missing(self):
        real = wiki_mod.WIKI_FILE
        wiki_mod.WIKI_FILE = real.parent / "does-not-exist.html"
        try:
            cog = Wiki(bot=None)
            interaction = FakeInteraction()
            # A WIKI_API_KEY in the environment makes the cog fetch from the
            # live backend first, which would answer and skip this path.
            with patch.dict(os.environ, {}, clear=True):
                run(cog.wiki.callback(cog, interaction))
            sent = interaction.response.sent[0]
            self.assertTrue(sent["ephemeral"])
            self.assertIn("not on the bot host", sent["content"])
        finally:
            wiki_mod.WIKI_FILE = real


if __name__ == "__main__":
    unittest.main()
