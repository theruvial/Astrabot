
import discord
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed
import asyncio

import random
from config import config

class House(commands.Cog):
    @bot.command()
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(ctx, user:discord.Member):
        """Kicks the specified user from the server"""
        await ctx.author.guild.kick(user)
        await ctx.send(f"Kicked <@{user.id}>")

    @bot.command()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(ctx, user:discord.Member):
        ds = int(time.time())
        banned = True
        memberName = user.name
        memberDiscrim = user.discriminator
        query = f'''INSERT INTO BANNEDUSERS (memberName, ds, banned, memberDiscrim) VALUES ('{memberName}', {ds}, {banned}, {str(memberDiscrim)})'''
        cursor.execute(query)
        conn.commit()
        await ctx.author.guild.ban(user)
        await ctx.send(f"Banned <@{user.id}>")

    @bot.command()
    @commands.has_permissions(ban_members= True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(ctx, *, member):
        color = discord.Colour(14885911)
        try:
            memberName, memberDiscrim = member.split('#')
            query = f'''
            with banned_users as (select bannedUsers.memberName, bannedUsers.ds, bannedUsers.banned, bannedUsers.memberDiscrim, row_number() over(partition by bannedUsers.memberName order by bannedUsers.ds DESC) rnk
            from bannedUsers
            where bannedUsers.memberName='{memberName}')
        
            select memberName, memberDiscrim
            from banned_users
            where rnk = 1
            and banned = 1
            '''
            cursor.execute(query)
            queryResult = cursor.fetchall()
            banned_users = await ctx.guild.bans()

            for ban_entry in banned_users:
                user = ban_entry.user
                for result in queryResult:
                    if(user.name, user.discriminator) == (result[0], str(result[1])):
                        await ctx.guild.unban(user)
            ds = int(time.time())
            banned = False
            query = f'''INSERT INTO BANNEDUSERS(memberName, ds, banned, memberDiscrim) VALUES ('{memberName}', {ds}, {banned}, '{memberDiscrim}')'''
            cursor.execute(query)
            conn.commit()
            embed = discord.Embed(title="Unbanned", description=f"<@{member}>", color=color)
            await ctx.send(embed=embed)
        except ValueError:
            embed = discord.Embed(title= "Please Enter Name Correctly", description= "name#1234, do not mention user", color = color)
            await ctx.send(embed = embed)

    ## Deletes messages when given a number of messages
    @bot.command()
    @commands.has_permissions(manage_messages=True)
    async def purge(ctx, number):
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
