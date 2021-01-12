import discord
from discord.ext import commands
from discord.utils import get
import asyncio
import time
import random
from config import config

class Shop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(pass_context = True, aliases = ["add"])
    async def addShopItem(self, value, name, *, description):
        

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):












def setup(bot):
    bot.add_cog(Shop(bot))