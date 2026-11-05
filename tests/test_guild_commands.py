"""Tests for per-guild command syncing (audiobooks-only guilds).

Run from the repo root:  python -m unittest discover -s tests -v
No network, Discord token, or bot login is used: sync() is stubbed
out and only the local command tree is inspected.
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock

import discord
from discord import app_commands

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import AUDIOBOOKS_ONLY_GUILD_IDS, Astrabot, commands_for_guild

AUDIOBOOKS_GUILD_ID = 454792201633792001
OTHER_GUILD_ID = 123456789012345678


def _cmd(name):
    async def _callback(interaction):
        pass

    return app_commands.Command(name=name, description=name, callback=_callback)


class FakeCog:
    def __init__(self, commands):
        self._commands = commands

    def get_app_commands(self):
        return list(self._commands)


class FakeBot:
    def __init__(self, cog_commands, all_commands):
        self._cog = FakeCog(cog_commands)
        self._all = all_commands

    def get_cog(self, name):
        return self._cog if name == "Audiobooks" else None

    @property
    def tree(self):
        return self

    def get_commands(self):
        return list(self._all)


class CommandsForGuildTests(unittest.TestCase):
    def test_audiobooks_guild_gets_only_audiobooks_commands(self):
        email_user = _cmd("email_user")
        bot = FakeBot([email_user], [_cmd("kick"), email_user, _cmd("wiki")])
        self.assertEqual(
            [c.name for c in commands_for_guild(bot, AUDIOBOOKS_GUILD_ID)],
            ["email_user"],
        )

    def test_other_guild_gets_all_commands(self):
        bot = FakeBot([_cmd("email_user")],
                      [_cmd("kick"), _cmd("email_user"), _cmd("wiki")])
        self.assertEqual(
            [c.name for c in commands_for_guild(bot, OTHER_GUILD_ID)],
            ["kick", "email_user", "wiki"],
        )

    def test_audiobooks_guild_is_the_configured_one(self):
        self.assertIn(AUDIOBOOKS_GUILD_ID, AUDIOBOOKS_ONLY_GUILD_IDS)


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class SyncGuildTests(unittest.TestCase):
    def test_sync_guild_filters_commands_in_the_tree(self):
        bot = Astrabot()
        kick, email_user, wiki = _cmd("kick"), _cmd("email_user"), _cmd("wiki")
        for command in (kick, email_user, wiki):
            bot.tree.add_command(command)
        bot._audiobooks_cog = FakeCog([email_user])
        bot.get_cog = lambda name: bot._audiobooks_cog if name == "Audiobooks" else None
        # Stub the network call: sync() just reports the guild tree.
        async def _fake_sync(guild):
            return list(bot.tree.get_commands(guild=guild))

        bot.tree.sync = AsyncMock(side_effect=_fake_sync)
        audiobooks_guild = discord.Object(id=AUDIOBOOKS_GUILD_ID)
        other_guild = discord.Object(id=OTHER_GUILD_ID)
        run(bot._sync_guild(audiobooks_guild))
        run(bot._sync_guild(other_guild))
        self.assertEqual(
            [c.name for c in bot.tree.get_commands(guild=audiobooks_guild)],
            ["email_user"],
        )
        self.assertEqual(
            sorted(c.name for c in bot.tree.get_commands(guild=other_guild)),
            ["email_user", "kick", "wiki"],
        )


if __name__ == "__main__":
    unittest.main()
