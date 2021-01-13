## this is for pokemon battles
import discord
from discord.ext import commands
from discord.utils import get
import asyncio
import time
import random
import math
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
defense
"""

class Pet(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


    @commands.Cog.listener()
    async def on_ready(self):
        sql_hp = '''Create table if not exists fct_pet_hp(
                                discord_id bigint not null,
                                cur_hp integer not null,
                                ds bigint not null
                                )
                '''
        await self.bot.pg_con.execute(sql_hp)
        sql_xp = '''Create table if not exists fct_pet_xp(
                                discord_id bigint not null, 
                                xp bigint not null,
                                ds bigint not null
                                )
                '''
        await self.bot.pg_con.execute(sql_xp)
        sql_stats = '''Create table if not exists fct_pet_stats(
                                discord_id bigint not null, 
                                ds bigint not null,
                                vitality integer not null,
                                attack integer not null,
                                defense integer not null,
                                speed integer not null
                                )
                '''
        await self.bot.pg_con.execute(sql_stats)
        sql_static = '''Create table if not exists dim_pet(
                                name text not null,
                                type text not null
                                )
                '''
        await self.bot.pg_con.execute(sql_static)

        print("Pets cog ready!")

    # ## pokemon heals con at midnight

    # def xp_to_level(self, xp:int) -> int:
    #     if xp==0:
    #         return 0
    #     return int(math.log(xp,1.8)//1)

    # def damage(self, level:int, attack: int) -> int:
    #     return (level+1) * attack

    # ## check row count
    # async def check_if_has_pet(self, ctx, user:discord.Member) -> bool:
    #     sql = f'''
    #     select sum(bucks) from bucks
    #     where discord_id  = {discord_id}
    #     '''
    #     queryResult = await self.bot.pg_con.fetchval(sql)
    #     return queryResult - bet >= 0

    # async def check_if_pet_has_hp(self, ctx, user:discord.Member) -> bool:
    #     sql = f'''
    #     select sum(curHP) from fct_pets
    #     where discord_id  = {discord_id}
    #     '''
    #     queryResult = await self.bot.pg_con.fetchval(sql)
    #     return queryResult - bet >= 0

    # ## Offer a choice of 3 pokemon.
    # @commands.command()
    # async def adopt(self, ctx, user:discord.Member):
    #     return

    # ## attacks another player
    # @commands.command()
    # async def attack(self, ctx, user:discord.Member):
    #     level = 0
    #     damage = (level +1) * attack
    #     return

    # ## fights random pokemon

def setup(bot):
    bot.add_cog(Pet(bot))