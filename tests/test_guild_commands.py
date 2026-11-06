"""Tests for command scope: /email_user lives in one guild only.

Run from the repo root:  python -m unittest discover -s tests -v
No network, Discord token, or bot login is used: sync() is stubbed
out and only the local command tree is inspected.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock
from unittest.mock import AsyncMock

import discord
from discord.ext import commands

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs import audiobooks

PRIVATE_GUILD = discord.Object(id=454792201633792001)
OTHER_GUILD = discord.Object(id=123456789012345678)


class CommandScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.bot = commands.Bot(command_prefix=".", intents=discord.Intents.none())
        self.bot.tree.sync = AsyncMock(return_value=[])
        self.config = config = {"db_path": os.path.join(self.tmp.name, "a.db")}
        for patcher in (mock.patch.object(audiobooks, "_audiobooks_config", return_value=config),
                        mock.patch.object(audiobooks.Audiobooks, "cog_load", AsyncMock())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def names(self, guild=None):
        return [c.name for c in self.bot.tree.get_commands(guild=guild)]

    async def test_email_user_is_only_in_the_private_guild(self):
        await audiobooks.setup(self.bot)
        self.assertEqual(self.names(PRIVATE_GUILD), ["email_user"])
        self.assertEqual(self.names(OTHER_GUILD), [])
        self.assertEqual(self.names(), [])

    async def test_config_guild_id_overrides_the_default(self):
        self.config["guild_id"] = OTHER_GUILD.id
        await audiobooks.setup(self.bot)
        self.assertEqual(self.names(OTHER_GUILD), ["email_user"])
        self.assertEqual(self.names(PRIVATE_GUILD), [])
        self.assertEqual(self.names(), [])

    async def test_setup_syncs_the_private_guild_after_adding_commands(self):
        seen = []

        async def sync(guild=None):
            seen.append((guild.id, self.names(PRIVATE_GUILD)))
            return []

        self.bot.tree.sync = sync
        await audiobooks.setup(self.bot)
        self.assertEqual(seen, [(PRIVATE_GUILD.id, ["email_user"])])

    async def test_reload_binds_the_command_to_the_new_cog(self):
        await audiobooks.setup(self.bot)
        first = self.bot.get_cog("Audiobooks")
        await self.bot.remove_cog("Audiobooks")
        self.assertEqual(self.names(PRIVATE_GUILD), [])
        await audiobooks.setup(self.bot)
        command = self.bot.tree.get_command("email_user", guild=PRIVATE_GUILD)
        self.assertIsNot(command.binding, first)
        self.assertIs(command.binding, self.bot.get_cog("Audiobooks"))

    async def test_failed_sync_does_not_stop_the_cog(self):
        self.bot.tree.sync = AsyncMock(side_effect=RuntimeError("offline"))
        await audiobooks.setup(self.bot)
        self.assertIsNotNone(self.bot.get_cog("Audiobooks"))


if __name__ == "__main__":
    unittest.main()
