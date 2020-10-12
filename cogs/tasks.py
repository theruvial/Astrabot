from asyncio.windows_events import NULL
import discord
from discord.ext import commands
from discord.utils import get
# from discord_webhook import DiscordWebhook, DiscordEmbed
from discord import Webhook, AsyncWebhookAdapter
import aiohttp
import asyncio
import sqlite3
from sqlite3 import Error
import time
import random
from taskConfig import config


class Tasks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self.conn = sqlite3.connect("tasks.db")
        self.cursor = self.conn.cursor()
        self.cursor.execute(
            '''
        CREATE TABLE IF NOT EXISTS tasks (
                        `isActive` bool not null,
                        `discordID` int not null,
                        `ds` int not null,
                        `taskName` Text not null,
                        `description` Text not null,
                        `assignedBy` int
                    )
        '''
        )

    def getUsername(self, discID):
        for discordUser in config[str(discID)]:
            if discID == discordUser:
                return discordUser["name"]

    @commands.command(pass_context=True, aliases=['active'])
    @commands.has_role('Team Member')
    async def getTasks(self, ctx, user: discord.Member = None):
        if user is None:
            user = ctx.author.user.id
        color = discord.Colour(14885911)
        query = f'''
        with user_tasks as (select tasks.taskName, tasks.isActive, tasks.ds, row_number() over(partition by tasks.taskName order by tasks.ds DESC) rnk
        from tasks
        where tasks.discordID = {user.id})

        select user_tasks.taskName
        from user_tasks
        where rnk = 1
        and isActive = 1
        '''

        self.cursor.execute(query)
        queryResult = self.cursor.fetchall()            

        if(queryResult == []):
            await ctx.send("No active tasks.")
        else:
            for result in queryResult:
                embed = discord.Embed(title = result[0], color = color)                
                await ctx.send(embed = embed)

        
            

    @commands.command(pass_context=True, aliases=['assign', 'a'])
    @commands.has_role('Team Member')
    async def assignTask(self, ctx, user: discord.Member, taskName=None, *, taskDescription=None):
        color = discord.Colour(14885911)
        if(taskName == None):
            embed = discord.Embed(
                title="Please enter a task name!", color=color)
            await ctx.send(embed=embed)
            return
        elif(taskDescription == None):
            embed = discord.Embed(
                title="Please enter a description!", color=color)
            await ctx.send(embed=embed)
            return

        isActive = True
        discordID = int(user.id)
        ds = int(time.time())
        query = f'''INSERT INTO TASKS (isActive, discordID, ds, taskName, description, assignedBy) VALUES ({isActive},{discordID},{ds},'{taskName}', '{taskDescription}', '{ctx.author.id}')'''

        self.cursor.execute(query)
        self.conn.commit()
        await ctx.send(f"{taskName} assigned to {user.mention}")

        if(isActive == True):
            currentStatus = "Active"
            async with aiohttp.ClientSession() as session:
                webhook = Webhook.from_url(
                    config[str(discordID)]["webhook"], adapter=AsyncWebhookAdapter(session))
                embed = discord.Embed(
                    title=taskName, description=taskDescription, color=color)
                embed.set_author(name=config[str(discordID)]["name"])
                embed.add_field(name="Time Assigned", value=time.asctime(
                    time.localtime(ds)), inline=False)
                embed.add_field(
                    name="Status", value=currentStatus, inline=False)
                embed.add_field(name="Assigned By", value=config[str(ctx.author.id)]["name"], inline=False)
                await webhook.send(embed=embed)

    @ commands.command(pass_context=True, aliases=['o'])
    @ commands.has_role('Team Member')
    async def open(self, ctx, taskName, *, taskDescription):
        color = discord.Colour(14885911)
        if(taskName == None):
            embed = discord.Embed(
                title="Please enter a task name!", color=color)
            await ctx.send(embed=embed)
            return
        elif(taskDescription == None):
            embed = discord.Embed(
                title="Please enter a description!", color=color)
            await ctx.send(embed=embed)
            return

        isActive = True
        discordID = int(ctx.author.id)
        ds = int(time.time())
        query = f'''INSERT INTO TASKS (isActive, discordID, ds, taskName, description, assignedBy) VALUES ({isActive},{discordID},{ds},'{taskName}', '{taskDescription}', '{ctx.author.id}')'''

        self.cursor.execute(query)
        self.conn.commit()
        await ctx.send(f"{taskName} assigned to {ctx.author.mention}")

        if(isActive == True):
            currentStatus = "Active"
            async with aiohttp.ClientSession() as session:
                webhook = Webhook.from_url(
                    config[str(discordID)]["webhook"], adapter=AsyncWebhookAdapter(session))
                embed = discord.Embed(
                    title=taskName, description=taskDescription, color=color)
                embed.set_author(name=config[str(discordID)]["name"])
                embed.add_field(name="Time Assigned", value=time.asctime(
                    time.localtime(ds)), inline=False)
                embed.add_field(
                    name="Status", value=currentStatus, inline=False)
                embed.add_field(name="Assigned By", value = config[str(discordID)]["name"], inline = False)
                await webhook.send(embed=embed)




    @ commands.command(pass_context=True, aliases=['close', 'c'])
    @ commands.has_role('Team Member')
    async def complete(self, ctx, user: discord.Member = None, taskName=None, *, taskDescription=None):
        color = discord.Colour(14885911)
        if(taskName == None):
            embed = discord.Embed(
                title="Please enter a task name!", color=color)
            await ctx.send(embed=embed)
            return
        elif(taskDescription == None):
            embed = discord.Embed(
                title="Please enter a description!", color=color)
            await ctx.send(embed=embed)
            return
        isActive = False
        discordID = int(user.id)
        ds = int(time.time())
        query = f'''INSERT INTO TASKS (isActive, discordID, ds, taskName, description, assignedBy) VALUES ({isActive}, {discordID}, {ds}, '{taskName}', '{taskDescription}', '{ctx.author}')'''

        self.cursor.execute(query)
        self.conn.commit()
        await ctx.send(f"{taskName} completed")

    # Shows task embed when you ask for it in whatever channel you are in

    @ commands.command(pass_context=True, aliases=['t'])
    @ commands.has_role('Team Member')
    async def task(self, ctx, taskName=None):
        if(taskName == None):
            color = discord.Colour(14885911)
            embed = discord.Embed(
                title="Please enter a task name!", color=color)
            await ctx.send(embed=embed)
            return

        query = f'''
        with user_tasks as (select tasks.taskName, tasks.discordID, tasks.isActive, tasks.ds , tasks.description, tasks.assignedBy, row_number() over(partition by tasks.taskName order by tasks.ds DESC) rnk
        from tasks
        where tasks.taskName = '{taskName}')

        select taskName, discordID, ds, isActive, description, assignedBy
        from user_tasks
        where rnk = 1
        '''

        self.cursor.execute(query)
        queryResult = self.cursor.fetchall()
        for result in queryResult:
            selectedTaskName = result[0]
            discordID = result[1]
            localTime = result[2]
            if(result[3] == 1):
                isActive = 'Open'
            else:
                isActive = 'Closed'
            taskDescription = result[4]            
            color = discord.Colour(14885911)
            embed = discord.Embed(title=selectedTaskName,
                                  description=taskDescription, color=color)
            embed.set_author(name=config[str(discordID)]["name"])
            # embed.add_field(name = "Assigned User", value = , inline = False)
            embed.add_field(name="Time Assigned", value=time.asctime(
                time.localtime(localTime)), inline=False)
            embed.add_field(name="Status", value=isActive, inline=False)
            embed.add_field(name="Assigned By",
                            value=config[str(result[5])]["name"], inline=False)
            # discordMention = .mention

            # print(f"TaskName: {selectedTaskName} \n Assigned User: {discordName.mention} \n Time Assigned: {time.localtime(localTime)} \n {isActive}")
            await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(Tasks(bot))


# Set active to dos (use mentions)
# retrieve active to dos
# complete to dos by checkmarking
# channel with all open to dos,
# channel with completed to dos
