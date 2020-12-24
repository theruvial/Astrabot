import discord
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed

import asyncio


import random
import sqlite3
import time
from config import config

class Test(commands.Cog):
    def __init__(self, bot, cursor):
        self.bot = bot
        self.cursor = cursor

    def fetch_current_exp(self, discord_id: int) -> int:
        sql = f'''
            Select sum(exp.exp) from exp
            where discord_id = {discord_id}
            '''
        self.db_cursor.execute(sql)
        result = self.db_cursor.fetchone()
        if result is None:
            return 0
        else:
            return result[0]


def setup(bot):
    bot.add_cog(Points(bot))
