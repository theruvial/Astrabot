## WE NEED TO PASS ctx
## We being rate limited and runnign into racetime errors
## replace

import discord
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
        self.ranks = ['Minion [ I ]', 'Henchman [ II ]',
                    'Villain [ III ]', 'Corrupted [ IV ]',
                    'Warlock [ V ]', 'Dark Knight [ VI ]',
                    'Reaper [ VII ]', 'Lich [ VIII ]',
                    'Demon Lord [ IX ]' ]
        # db_path = os.path.join(os.path.abspath(os.getcwd()), DB_NAME + ".db")
        self.db = sqlite3.connect('thi.db')
        self.db_cursor = self.db.cursor()
        self.db_cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS points (
                        `ds` INTEGER NOT NULL,
                        `discord_id` INTEGER NOT NULL,
                        `pts` INTEGER NOT NULL,
                        `type` TEXT NOT NULL
                    )
        '''
                                )

    def fetch_current_pts(self, discord_id):
        sql = f'''
            Select pts from points
            where ds = (select max(ds) from points where discord_id  = {discord_id})
            and discord_id = {discord_id}
            Limit 1
            '''
        self.db_cursor.execute(sql)
        result = self.db_cursor.fetchone()
        if result is None:
            return 0
        else:
            return result[0]

    def check_last_point_ds(self, discord_id, type_value):
        sql = f'''
            select max(ds) from points
            where discord_id  = {discord_id}
            and type = '{type_value}'
            Limit 1
            '''
        self.db_cursor.execute(sql)
        result = self.db_cursor.fetchone()
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
    async def add_points(self,member, points, type_value = 'generic'):
        ds  = int(time.time())
        current_pts = self.fetch_current_pts(member.id)
        pts = int(current_pts) + int(points) # need to add their previous pt value here
        sql = f''' INSERT INTO points(ds,discord_id,pts,type)
                  VALUES({ds},{member.id},{pts},'{type_value}') '''
        self.db_cursor.execute(sql)
        self.db.commit()

        # await self.promote(current_pts, pts, member)


    @commands.command()
    async def points(self, ctx, user:discord.Member = None): #### INPUT
        if user is None:
            user = ctx.author
        # self.check_last_record()
        current_pts = self.fetch_current_pts(user.id)
        await ctx.send(f"{current_pts}")


    @commands.command()
    @commands.has_role('Admin')
    async def award(self, ctx, user:discord.Member, points): #### INPUT
        await self.add_points(user,points, type_value = 'award')
        await ctx.send(f"{ctx.author.mention} has awarded {user.mention} {points} pts")

    # Move out of points.
    @commands.command()
    @commands.has_role('Admin')
    async def speak(self, ctx, *, message: str): #### INPUT
        channel = self.bot.get_channel(614971333473730572)
        msg = ctx.message
        await channel.send(message)
        await msg.delete()
## tricky towers
    @commands.Cog.listener()
    async def on_message(self, message):
        if int(time.time()) >= int(self.check_last_point_ds(message.author.id, 'msg')) + 60:
            await self.add_points(message.author,1,'msg')


    def int_to_Roman(self, num):
        val = [
            1000, 900, 500, 400,
            100, 90, 50, 40,
            10, 9, 5, 4,
            1
            ]
        syb = [
            "M", "CM", "D", "CD",
            "C", "XC", "L", "XL",
            "X", "IX", "V", "IV",
            "I"
            ]
        roman_num = ''
        i = 0
        while  num > 0:
            for _ in range(num // val[i]):
                roman_num += syb[i]
                num -= val[i]
            i += 1
        return '[ ' + roman_num +  ' ]'


    def pts_to_rank(self, pts):
        pts_list = [1,50,200,500]

        def check_exponential(pts, expo = 0):
            if pts >= 1000 * (2**expo):
                return check_exponential(pts, expo+1)
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



def setup(bot):
    bot.add_cog(Points(bot))
