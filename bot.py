## https://discordpy.readthedocs.io/en/latest/ext/commands/commands.html

#import config.json as config

import discord
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed
import json
import random
from config import config
from taskConfig import config as taskConfig
import random
import time
import schedule
import threading
import asyncio
import asyncpg
# import tweepy

startup_extensions = [
    'cogs.roles', 'cogs.points', 'cogs.moderation', 'cogs.gambling',
    'cogs.tasks', 'cogs.shop', 'cogs.pet'
]
intents = discord.Intents.all()

class Thi(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=config["prefix"],
            case_insensitive=True,
            description=config["description"],
            intents=intents,
            pm_help=None,
            help_attrs=dict(hidden=True),
            fetch_offline_members=False,
            heartbeat_timeout=150.0,
        )

        self.token = config['token']  ## replace with our token

        for extension in startup_extensions:
            try:
                self.load_extension(extension)
            except Exception as e:
                print(
                    f"Failed to load extension {extension}\n{type(e).__name__}: {e}"
                )

    async def on_ready(self):
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.listening,
                                      name="Metronome rant about girls"))
        print(
            f"""
            Username: {self.user.name}
            ID: {self.user.id}
            -----------------------
        """
        )

    async def create_db_pool(self):
        self.pg_con = await asyncpg.create_pool(
            host=config["dbHost"],
            port=config["dbPort"],
            user=config["dbUser"],
            database=config["db"],
            password=config["dbPass"]
        )

    def run(self):
        super().run(self.token, reconnect=True)


if __name__ == "__main__":
    client = Thi()
    client.loop.run_until_complete(client.create_db_pool())
    client.run()