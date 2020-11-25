## https://discordpy.readthedocs.io/en/latest/ext/commands/commands.html

#import config.json as config

import discord
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed
import json
import random
from config import config
from taskConfig import config as taskConfig
import sqlite3
from sqlite3 import Error
import random
import asyncio
import time
import schedule
import threading
# import tweepy

startup_extensions = ['cogs.tasks', 'cogs.roles','cogs.points']
## Define BOT
intents = discord.Intents().all()
bot = commands.Bot(command_prefix=config['prefix'], intents = intents) ## THIS LINE CLASS CLIENT




################################# BOT STUFF

conn = sqlite3.connect("bannedUsers.db")
cursor = conn.cursor()
cursor.execute(
    '''
        CREATE TABLE IF NOT EXISTS bannedUsers (
                        `memberName` TEXT NOT NULL,
                        `ds` INTEGER NOT NULL,
                        `banned` BOOL NOT NULL,
                        `memberDiscrim` TEXT NOT NULL
                    )
        '''
)





@bot.event ## <--- DECORATOR. PUT INSIDE OF BOT.
async def on_ready():
    print('Logged in as')
    print(bot.user.name)
    print(bot.user.id)

## Example: $add 3 4 Returns: 7
@bot.command()
async def add(ctx, left:float, right: float): #### INPUT
    await ctx.send(left + right) ### OUTPUT

@bot.command()
async def subtract(ctx, left:float, right:float):
    await ctx.send(left - right)

@bot.command()
async def multiply(ctx, left: float, right: float):
    await ctx.send(left * right)

@bot.command()
async def divide(ctx, left: float, right: float):
    await ctx.send(left / right)


### Input: $hello
### Output: @brian Hello!
@bot.command()
async def hello(ctx):

    greeting_list = [
    "Hello",
    "Beware",
    "Hello Minion",
    "Look out",
    "is gay"
    ]

    await ctx.send(f"{ctx.author.mention} {random.choice(greeting_list)}!")

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





## Dice rolling. Start with d6 and make more robust later.
@bot.command()
async def roll(ctx, *args):
    for i in args:
        dice = i.lower().split("d")
        if len(dice) == 2:
            count = int(dice[0])
            sides = int(dice[1])
            amount = 0
            for num in range(count):
                amount += random.randint(1,sides)
            await ctx.send(amount)

## Deletes messages when given a number of messages
@bot.command()
@commands.has_permissions(manage_messages=True)
async def purge(ctx, number):
    msgs = [] #Empty list to put all the messages in the log
    msgsToDelete = int(number) + 1 #Converting the amount of messages to delete to an integer
    async for message in ctx.message.channel.history(limit=msgsToDelete):
        msgs.append(message)
    await ctx.message.channel.delete_messages(msgs)


if __name__ == "__main__":
    for extension in startup_extensions:
        try:
            bot.load_extension(extension)
        except Exception as e:
            exc = '{}: {}'.format(type(e).__name__, e)
            print('Failed to load extension {}\n{}'.format(extension, exc))
    bot.run(config['token'])
