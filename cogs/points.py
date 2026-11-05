## WE NEED TO PASS ctx
## We being rate limited and runnign into racetime errors
## replace

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


class Points(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # self.guild = self.bot.get_guild(614971333473730570)
        self.ranks = [
            "Minion [ I ]",
            "Henchman [ II ]",
            "Villain [ III ]",
            "Corrupted [ IV ]",
            "Warlock [ V ]",
            "Dark Knight [ VI ]",
            "Reaper [ VII ]",
            "Lich [ VIII ]",
            "Demon Lord [ IX ]",
        ]

    def check_last_exp_ds(self, discord_id: int, type_value: str) -> int:
        sql = '''
            SELECT MAX(ds) FROM exp
            WHERE discord_id = ?
            AND type = ?
            LIMIT 1
        '''
        cursor = self.bot.db.cursor()
        cursor.execute(sql, (discord_id, type_value))
        result = cursor.fetchone()
        if result is None:
            return 0
        elif result[0] is None:
            return 0
        else:
            return result[0]

    # async def get_guild_roles(self):
    #     self.guild = self.bot.get_guild(614971333473730570)
    #     await return

    ## Instead of passing discord id can we pass the member
    async def add_points(self, member, points: int, type_value: str = "generic") -> None:
        ds = int(time.time())
        pts = int(points)  # + int(current_pts) # need to add their previous pt value here
        cursor = self.bot.db.cursor()
        cursor.execute(
            "INSERT INTO bucks (ds, discord_id, bucks, type) VALUES (?, ?, ?, ?)",
            (ds, member.id, pts, type_value),
        )
        cursor.execute(
            "INSERT INTO exp (ds, discord_id, exp, type) VALUES (?, ?, ?, ?)",
            (ds, member.id, pts, type_value),
        )
        self.bot.db.commit()

    async def add_bucks(self, member, points: int, type_value: str = "generic") -> None:
        ds = int(time.time())
        pts = int(points)
        cursor = self.bot.db.cursor()
        cursor.execute(
            "INSERT INTO bucks (ds, discord_id, bucks, type) VALUES (?, ?, ?, ?)",
            (ds, member.id, pts, type_value),
        )
        self.bot.db.commit()

    @app_commands.command(name="exp", description="Check a user's experience and Astrabucks")
    async def exp(self, interaction: discord.Interaction, user: discord.Member = None):  #### INPUT
        if user is None:
            user = interaction.user
        # self.check_last_record()
        discord_id = user.id
        cursor = self.bot.db.cursor()
        cursor.execute(
            "SELECT COALESCE(SUM(exp.exp), 0) FROM exp WHERE discord_id = ?",
            (discord_id,),
        )
        current_exp = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COALESCE(SUM(bucks.bucks), 0) FROM bucks WHERE discord_id = ?",
            (discord_id,),
        )
        current_bucks = cursor.fetchone()[0]

        embed = discord.Embed(title=f"{user.name}")
        embed.add_field(
            name=":small_orange_diamond: Experience", value=current_exp, inline=False
        )
        embed.add_field(
            name=":dollar: Astrabucks", value=current_bucks, inline=False
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="award", description="Award Astrabucks to a user")
    async def award(
        self, interaction: discord.Interaction, user: discord.Member, points: int
    ):  #### INPUT
        role = discord.utils.get(interaction.guild.roles, name="Admin")
        if role in interaction.user.roles:
            await self.add_bucks(user, points, type_value="award")
            await interaction.response.send_message(
                f"{interaction.user.mention} has awarded {user.mention} {points} pts"
            )
        else:
            await interaction.response.send_message(
                "You do not have permission to use this command!", ephemeral=True
            )

    ## tricky towers
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        discord_id = message.author.id
        last_exp_ds = self.check_last_exp_ds(discord_id, "msg")
        if int(time.time()) >= int(last_exp_ds) + 60:
            await self.add_points(message.author, 1, "msg")

    def int_to_Roman(self, num):
        val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
        syb = ["M", "CM", "D", "CD", "C", "XC", "L", "XL", "X", "IX", "V", "IV", "I"]
        roman_num = ""
        i = 0
        while num > 0:
            for _ in range(num // val[i]):
                roman_num += syb[i]
                num -= val[i]
            i += 1
        return "[ " + roman_num + " ]"

    def pts_to_rank(self, pts):
        pts_list = [1, 50, 200, 500]

        def check_exponential(pts, expo=0):
            if pts >= 1000 * (2 ** expo):
                return check_exponential(pts, expo + 1)
            else:
                return expo + 4

        # recursively check higher exponentials
        if pts >= 500:
            return check_exponential(pts)
        # this is the problem?
        rank = 0
        for value in pts_list:
            if pts < value:
                return rank
            rank += 1
        return 0


## 0 Nothing
## 1-49 minion
## 50-199 Henchman
## 200-499
## 500 - 999

# def get_role_reg_ex(self, roman_num, member):
#     # find clean way to grab guild role_list?
#     ## Add guild to loading
#     for role in member.guild.roles:
#         if role.name.endswith(roman_num):
#             return role
#     else:
#         return None

# async def promote(self,old_pts,new_pts,member):
#     await self.bot.wait_until_ready()

#     old_rank = self.pts_to_rank(old_pts)
#     new_rank = self.pts_to_rank(new_pts)
#     if old_rank != new_rank and new_rank <= len(self.ranks):
#         # remove old rank
#         if old_rank > 0:
#             old_role = get(member.guild.roles, name = self.ranks[old_rank-1])
#             await member.remove_roles(old_role)

#         new_role = get(member.guild.roles, name = self.ranks[new_rank-1])


#         quotes = [f"{member.mention} is now a {new_role.name.split(' [')[0]}. Slightly less pathetic. Still disgusting.",
#                   f"Pathetic. {member.mention} is now a {new_role.name.split(' [')[0]}.",
#                   f"A new {new_role.name.split(' [')[0]}. Not bad {member.mention}.",
#                   f"{member.mention} rise now as a {new_role.name.split(' [')[0]}!",
#                   f"{member.mention}. A {new_role.name.split(' [')[0]}? They grow up so fast.",
#                   f"{member.mention} is now a {new_role.name.split(' [')[0]}... they'll always be a minion in my heart.",
#                   f"*cackle* {member.mention} has ranked up.",
#                   f"{member.mention} have you been working out? You look like a {new_role.name.split(' [')[0]}."]

#         await member.add_roles(new_role)
#         channel = self.bot.get_channel(614971333473730572)


#         await channel.send(random.choice(quotes))


async def setup(bot):
    await bot.add_cog(Points(bot))
