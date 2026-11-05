"""Astrabot cog: /wiki — send the Aboard the Astra mechanics wiki.

The wiki is a single self-contained HTML file (``cogs/wiki.html``,
built by ``Tools/wiki/build_standalone.py`` in the Astra repo — CSS,
JS and mechanics data inlined, no hosting, no backend). This cog
just serves that file up as a Discord attachment when someone runs
``/wiki``.

To update the wiki: rebuild the standalone file in the Astra repo
and copy it over ``cogs/wiki.html`` on the bot host, then restart
the bot (or reload this cog). No config needed.

Enable it by adding ``"cogs.wiki"`` to ``startup_extensions`` in the
host's ``bot.py``. It is deliberately NOT added to the staging copy
here: the host's ``bot.py`` has local changes that a pushed copy
would overwrite.
"""

from __future__ import annotations

from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

WIKI_FILE = Path(__file__).resolve().parent / "wiki.html"


def wiki_file_path() -> Path:
    return WIKI_FILE


class Wiki(commands.Cog):
    """Send the mechanics wiki HTML file on /wiki."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="wiki",
        description="Send the Aboard the Astra mechanics wiki (standalone HTML file)",
    )
    async def wiki(self, interaction: discord.Interaction):
        path = wiki_file_path()
        if not path.is_file():
            await interaction.response.send_message(
                "The wiki file is not on the bot host yet — "
                "copy the latest `astra-wiki.html` to `cogs/wiki.html` "
                "and restart the bot.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            content="Aboard the Astra — Mechanics Wiki",
            file=discord.File(path, filename="astra-wiki.html"),
        )


async def setup(bot):
    await bot.add_cog(Wiki(bot))
