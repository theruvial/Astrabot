## this is for pokemon battles 
import discord
from discord.ext import commands
from discord.utils import get
import asyncio
import time
import random
from config import config

## pets are defined by
"""
Pets
---
name
type
curHp
maxHp -- derived from con?
movesKnown
attack
speed
constitution
"""

class Pet(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    ## pokemon heals con at midnight

    @commands.command()
    async def attack(self, ctx, user:discord.Member): 
        return