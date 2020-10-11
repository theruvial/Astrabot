import discord
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed
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
                        `taskName` Text not null
                    )
        '''
                                )

         
    
    @commands.command(pass_context = True, aliases = ['active'])
    @commands.has_role('Team Member')
    async def getTasks(self, ctx, user:discord.Member):
        

        print("getting tasks?")
        query = f'''
        with user_tasks as (select tasks.taskName, tasks.isactive, tasks.ds, row_number() over(partition by tasks.taskName order by tasks.ds DESC) rnk
        from tasks
        where tasks.discordID = {user.id})

        select user_tasks.taskName
        from user_tasks
        where rnk = 1
        and isActive = 1
        '''

        self.cursor.execute(query)
        queryResult = self.cursor.fetchall()

        for result in queryResult:
            print(result)       
            await ctx.send(result)        
        
    @commands.command(pass_context= True, aliases = ['createTask','open'])
    @commands.has_role('Team Member')
    async def task(self, ctx, user:discord.Member, taskName):
        isActive = True
        discordID = int(user.id)
        ds = int(time.time())
        query = f'''INSERT INTO TASKS (isActive, discordID, ds, taskName) VALUES ({isActive},{discordID},{ds},'{taskName}')'''       

        self.cursor.execute(query)
        self.conn.commit()
        await ctx.send(f"{taskName} assigned to {user.mention}")        

    @commands.command(pass_context=  True, aliases = ['close'])
    @commands.has_role('Team Member')
    async def completeTask(self, ctx, user:discord.Member, taskName):
        isActive = False
        discordID = int(user.id)       
        ds = int(time.time())
        query = f'''INSERT INTO TASKS (isActive, discordID, ds, taskName) VALUES ({isActive},{discordID},{ds},'{taskName}')'''

        self.cursor.execute(query)
        self.conn.commit()
        await ctx.send(f"{taskName} completed")      


    #@commands.command(pass_context=True, aliases=['CT'])
    #@commands.has_role('Team Member')
    #async def CompleteTask(self, ctx):
#
    #@commands.command(pass_context=True, aliases=['UOT'])
    #@commands.has_role('Team Member')
    #async def UpdateOpenTasks(self, ctx):
    #    
    #@commands.command(pass_context=True, aliases=['UCT'])
    #@commands.has_role('Team Member')
    #async def UpdateCompletedTasks(self, ctx):
    #   
        

def setup(bot):
    bot.add_cog(Tasks(bot))



# Set active to dos (use mentions)
# retrieve active to dos
# complete to dos by checkmarking
# channel with all open to dos,
# channel with completed to dos


