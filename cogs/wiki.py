"""Astrabot cog: /wiki — send the Aboard the Astra mechanics wiki.

The wiki is a single self-contained HTML file, rebuilt by
``Tools/wiki/build_standalone.py`` in the Astra repo (CSS, JS and
mechanics data inlined). This cog fetches the freshly regenerated
file from the wiki backend on every ``/wiki`` run, and falls back
to the bundled copy (``cogs/wiki.html``) when the backend cannot be
reached.

Backend access (Astrabot-only): the backend at ``WIKI_API_URL``
(default ``http://127.0.0.1:13382``) requires the shared-secret
header ``X-Astra-Wiki-Key`` on every request. Set ``WIKI_API_KEY``
to the same value on the bot host and the backend host (env var,
never committed). With no key configured, the cog just serves the
bundled file.
"""

from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

WIKI_FILE = Path(__file__).resolve().parent / "wiki.html"

DEFAULT_API_URL = "http://127.0.0.1:13382"
STANDALONE_PATH = "/wiki/astra-wiki.html"


def wiki_file_path() -> Path:
    return WIKI_FILE


def api_config() -> tuple[str, str | None]:
    """(backend base URL, shared-secret API key or None)."""
    return (
        os.environ.get("WIKI_API_URL", DEFAULT_API_URL).rstrip("/"),
        os.environ.get("WIKI_API_KEY") or None,
    )


async def fetch_backend_html(api_url: str, api_key: str) -> bytes | None:
    """Fetch the regenerated standalone wiki from the backend.

    Returns the file bytes, or None if the backend cannot be reached
    or refuses the request (bad key, non-200 status, timeout).
    """
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{api_url}{STANDALONE_PATH}",
                headers={"X-Astra-Wiki-Key": api_key},
            ) as response:
                if response.status != 200:
                    return None
                return await response.read()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None


class Wiki(commands.Cog):
    """Send the mechanics wiki HTML file on /wiki."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="wiki",
        description="Send the Aboard the Astra mechanics wiki (standalone HTML file)",
    )
    async def wiki(self, interaction: discord.Interaction):
        api_url, api_key = api_config()
        data = None
        if api_key:
            data = await fetch_backend_html(api_url, api_key)
        if data is not None:
            await interaction.response.send_message(
                content="Aboard the Astra — Mechanics Wiki",
                file=discord.File(io.BytesIO(data), filename="astra-wiki.html"),
            )
            return
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
