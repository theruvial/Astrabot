## WE NEED TO PASS ctx
## We being rate limited and runnign into racetime errors
## replace

import discord
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed

import asyncio
import asyncpg


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
        #self.db = sqlite3.connect('thi.db')
        #self.db_cursor = self.db.cursor()
        #self.db_cursor.execute(
        #'''
        #CREATE TABLE IF NOT EXISTS exp (
        #                `ds` INTEGER NOT NULL,
        #                `discord_id` INTEGER NOT NULL,
        #                `exp` INTEGER NOT NULL,
        #                `type` TEXT NOT NULL
        #            )
        #'''
        #)
        #self.db_cursor.execute(
        #'''
        #CREATE TABLE IF NOT EXISTS bucks (
        #                `ds` INTEGER NOT NULL,
        #                `discord_id` INTEGER NOT NULL,
        #                `bucks` INTEGER NOT NULL,
        #                `type` TEXT NOT NULL
        #            )
        #'''
        #)

    #def fetch_current_exp(self, discord_id: int) -> int:
    #    self.db_cursor.execute(sql)
    #    result = self.db_cursor.fetchone()
    #    if result is None:
    #        return 0
    #    else:
    #        return result[0]

    #def fetch_current_bucks(self, discord_id: int) -> int:
    #    sql = f'''
    #        Select sum(bucks.bucks) from bucks
    #        where discord_id = {discord_id}
    #        '''
    #    self.db_cursor.execute(sql)
    #    result = self.db_cursor.fetchone()
    #    if result is None:
    #        return 0
    #    else:
    #        return result[0]

    def check_last_exp_ds(self, discord_id: int, type_value: str) -> int:
        sql = f'''
            select max(ds) from exp
            where discord_id  = {discord_id}
            and type = '{type_value}'
            Limit 1
            '''
        self.bot.pg_con.execute(sql)
        result = self.bot.pg_con.fetchone()
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
    async def add_points(self,member, points: int, type_value: str = 'generic') -> None:
        ds  = int(time.time())
        #urrent_pts = self.fetch_current_pts(member.id)
        pts = int(points) #+ int(current_pts) # need to add their previous pt value here
        sql_bucks = f'''INSERT INTO bucks(ds,discord_id,bucks,type)
                  VALUES({ds},{member.id},{pts},'{type_value}')              
                   '''
        sql_exp = f'''INSERT INTO exp(ds,discord_id,exp,type)
                  VALUES({ds},{member.id},{pts},'{type_value}')                  
                   '''
        await self.bot.pg_con.execute(sql_bucks)
        await self.bot.pg_con.execute(sql_exp)

    async def add_bucks(self, member, points: int, type_value: str = 'generic') -> None:
        ds = int(time.time())
        pts = int(points)
        sql_bucks = f'''INSERT INTO bucks(ds,discord_id,bucks,type)
                  VALUES({ds},{member.id},{pts},'{type_value}')              
                   '''
        await self.bot.pg_con.execute(sql_bucks)

    @commands.command(pass_context=True, aliases=['bucks', 'points','pts'])
    async def exp(self, ctx, user:discord.Member = None): #### INPUT
        if user is None:
            user = ctx.author
        # self.check_last_record()
        discord_id = user.id
        sql_exp = f'''
            Select sum(exp.exp) from exp
            where discord_id = {discord_id}
            '''
        sql_buck = f'''
            Select sum(bucks.bucks) from bucks
            where discord_id = {discord_id}
            '''
        current_exp = await self.bot.pg_con.fetchval(sql_exp)
        current_bucks = await self.bot.pg_con.fetchval(sql_buck)
        embed = discord.Embed(title=f"{user.name}")        
        embed.add_field(name=":small_orange_diamond: Experience", value = current_exp, inline=False)
        embed.add_field(name=":dollar: Astrabucks", value = current_bucks, inline=False)
        await ctx.send(embed = embed)

    @commands.command()
    async def award(self, ctx, user:discord.Member, points): #### INPUT
        role = discord.utils.get(ctx.guild.roles, name="Admin")
        if role in ctx.author.roles:
            await self.add_bucks(user,points, type_value = 'award')
            await ctx.send(f"{ctx.author.mention} has awarded {user.mention} {points} pts")
        else:
            msg = await ctx.send("You do not have permission to use this command!")
            await asyncio.sleep(3)
            await msg.delete()

## tricky towers
    @commands.Cog.listener()
    async def on_message(self, message):
        discord_id = message.author.id
        sql = f'''
            select max(ds) from exp
            where discord_id  = {discord_id}
            and type = 'msg'
            Limit 1
            '''
        last_exp_ds = await self.bot.pg_con.fetchval(sql)
        if not last_exp_ds: last_exp_ds = 0
        if int(time.time()) >= int(last_exp_ds) + 60:
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
