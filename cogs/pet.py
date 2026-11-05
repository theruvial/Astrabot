## this is for pokemon battles
import discord
from discord import app_commands
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
        cursor = self.bot.db.cursor()
        sql_hp = '''CREATE TABLE IF NOT EXISTS fct_pet_hp(
                                discord_id BIGINT NOT NULL,
                                cur_hp INTEGER NOT NULL,
                                ds BIGINT NOT NULL
                                )
                '''
        cursor.execute(sql_hp)
        sql_xp = '''CREATE TABLE IF NOT EXISTS fct_pet_xp(
                                discord_id BIGINT NOT NULL,
                                xp BIGINT NOT NULL,
                                ds BIGINT NOT NULL
                                )
                '''
        cursor.execute(sql_xp)
        sql_stats = '''CREATE TABLE IF NOT EXISTS fct_pet_stats(
                                discord_id BIGINT NOT NULL,
                                ds BIGINT NOT NULL,
                                vitality INTEGER NOT NULL,
                                attack INTEGER NOT NULL,
                                defense INTEGER NOT NULL,
                                speed INTEGER NOT NULL
                                )
                '''
        cursor.execute(sql_stats)
        sql_static = '''CREATE TABLE IF NOT EXISTS dim_pet(
                                name TEXT NOT NULL,
                                type TEXT NOT NULL
                                )
                '''
        cursor.execute(sql_static)
        self.bot.db.commit()

        print("Pets cog ready!")

    # ## pokemon heals con at midnight

    # def xp_to_level(self, xp:int) -> int:
    #     if xp==0:
    #         return 0
    #     return int(math.log(xp,1.8)//1)

    # def damage(self, level:int, attack: int) -> int:
    #     return (level+1) * attack

    # ## check row count
    # async def check_if_has_pet(self, interaction: discord.Interaction, user: discord.Member) -> bool:
    #     cursor = self.bot.db.cursor()
    #     cursor.execute(
    #         "SELECT COALESCE(SUM(bucks), 0) FROM bucks WHERE discord_id = ?",
    #         (user.id,),
    #     )
    #     queryResult = cursor.fetchone()[0]
    #     return queryResult >= 0

    # async def check_if_pet_has_hp(self, interaction: discord.Interaction, user: discord.Member) -> bool:
    #     cursor = self.bot.db.cursor()
    #     cursor.execute(
    #         "SELECT COALESCE(SUM(cur_hp), 0) FROM fct_pet_hp WHERE discord_id = ?",
    #         (user.id,),
    #     )
    #     queryResult = cursor.fetchone()[0]
    #     return queryResult > 0

    # ## Offer a choice of 3 pokemon.
    # @app_commands.command(name="adopt")
    # async def adopt(self, interaction: discord.Interaction, user: discord.Member):
    #     return

    # ## attacks another player
    # @app_commands.command(name="attack")
    # async def attack(self, interaction: discord.Interaction, user: discord.Member):
    #     level = 0
    #     damage = (level + 1) * attack
    #     return

    # ## fights random pokemon


async def setup(bot):
    await bot.add_cog(Pet(bot))
