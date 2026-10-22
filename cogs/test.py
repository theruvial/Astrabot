import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed

import asyncio
import random
import sqlite3
import time
from config import config


class Test(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def fetch_current_exp(self, discord_id: int) -> int:
        sql = '''
            SELECT COALESCE(SUM(exp.exp), 0) FROM exp
            WHERE discord_id = ?
        '''
        cursor = self.bot.db.cursor()
        cursor.execute(sql, (discord_id,))
        result = cursor.fetchone()
        if result is None or result[0] is None:
            return 0
        else:
            return result[0]


async def setup(bot):
    await bot.add_cog(Test(bot))
