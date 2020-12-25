import discord
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed
import asyncio

import random
from config import config

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx, user:discord.Member):
        """Kicks the specified user from the server"""
        await ctx.author.guild.kick(user)
        await ctx.send(f"Kicked <@{user.id}>")

    # Move out of points.
    @commands.command()
    async def speak(self, ctx, *, message: str):  #### INPUT
        role = discord.utils.get(ctx.guild.roles, name="Admin")
        channel = self.bot.get_channel(584936481924055050)
        if role in ctx.author.roles:
            msg = ctx.message
            await channel.send(message)
            await msg.delete()
        else:
            msg = await ctx.send(
                "You do not have permission to use this command!")
            await asyncio.sleep(3)
            await msg.delete()

    # @commands.command()
    # @commands.has_permissions(ban_members=True)
    # @commands.bot_has_permissions(ban_members=True)
    # async def ban(self, ctx, user:discord.Member):
    #     ds = int(time.time())
    #     banned = True
    #     memberName = user.name
    #     memberDiscrim = user.discriminator
    #     query = f'''INSERT INTO BANNEDUSERS (memberName, ds, banned, memberDiscrim) VALUES ('{memberName}', {ds}, {banned}, {str(memberDiscrim)})'''
    #     cursor.execute(query)
    #     conn.commit()
    #     await ctx.author.guild.ban(user)
    #     await ctx.send(f"Banned <@{user.id}>")

    # @bot.command()
    # @commands.has_permissions(ban_members= True)
    # @commands.bot_has_permissions(ban_members=True)
    # async def unban(ctx, *, member):
    #     color = discord.Colour(14885911)
    #     try:
    #         memberName, memberDiscrim = member.split('#')
    #         query = f'''
    #         with banned_users as (select bannedUsers.memberName, bannedUsers.ds, bannedUsers.banned, bannedUsers.memberDiscrim, row_number() over(partition by bannedUsers.memberName order by bannedUsers.ds DESC) rnk
    #         from bannedUsers
    #         where bannedUsers.memberName='{memberName}')

    #         select memberName, memberDiscrim
    #         from banned_users
    #         where rnk = 1
    #         and banned = 1
    #         '''
    #         cursor.execute(query)
    #         queryResult = cursor.fetchall()
    #         banned_users = await ctx.guild.bans()

    #         for ban_entry in banned_users:
    #             user = ban_entry.user
    #             for result in queryResult:
    #                 if(user.name, user.discriminator) == (result[0], str(result[1])):
    #                     await ctx.guild.unban(user)
    #         ds = int(time.time())
    #         banned = False
    #         query = f'''INSERT INTO BANNEDUSERS(memberName, ds, banned, memberDiscrim) VALUES ('{memberName}', {ds}, {banned}, '{memberDiscrim}')'''
    #         cursor.execute(query)
    #         conn.commit()
    #         embed = discord.Embed(title="Unbanned", description=f"<@{member}>", color=color)
    #         await ctx.send(embed=embed)
    #     except ValueError:
    #         embed = discord.Embed(title= "Please Enter Name Correctly", description= "name#1234, do not mention user", color = color)
    #         await ctx.send(embed = embed)

    ## Deletes messages when given a number of messages
    @commands.command()
    @commands.has_permissions(manage_messages=True)
    async def purge(self, ctx, number):
        msgs = [] #Empty list to put all the messages in the log
        msgsToDelete = int(number) + 1 #Converting the amount of messages to delete to an integer
        async for message in ctx.message.channel.history(limit=msgsToDelete):
            msgs.append(message)
        await ctx.message.channel.delete_messages(msgs)


# conn = sqlite3.connect("bannedUsers.db")
# cursor = conn.cursor()
# cursor.execute(
#     '''
#         CREATE TABLE IF NOT EXISTS bannedUsers (
#                         `memberName` TEXT NOT NULL,
#                         `ds` INTEGER NOT NULL,
#                         `banned` BOOL NOT NULL,
#                         `memberDiscrim` TEXT NOT NULL
#                     )
#         '''
# )
def setup(bot):
    bot.add_cog(Moderation(bot))