#from asyncio.windows_events import NULL
import discord
from discord.ext import commands
from discord.utils import get
import asyncio
import sqlite3
from sqlite3 import Error
import time
import random
from taskConfig import config


class Tasks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.openColor = discord.Colour(3665255)
        self.closedColor = discord.Colour(4804175)

        self.conn = sqlite3.connect("thi.db")
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
                embed = discord.Embed(title=result[0], color=color)
                await ctx.send(embed=embed)




    @commands.command(pass_context=True, aliases=['assign', 'a'])
    @commands.has_role('Team Member')
    async def assignTask(self, ctx, user: discord.Member, taskName=None, *, taskDescription=None):
        color = self.openColor
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
  
        currentStatus = "Open"
        embed = discord.Embed(
            title=taskName, description=taskDescription, color=color)
        embed.set_author(name=config[str(discordID)]["name"])
        embed.add_field(name="Time Assigned", value=time.asctime(
            time.localtime(ds)), inline=False)
        embed.add_field(
            name="Status", value=currentStatus, inline=False)
        embed.add_field(name="Assigned By", value=config[str(ctx.author.id)]["name"], inline=False)
        channel = self.bot.get_channel(config[str(discordID)]["channel_id"])
        msg = await channel.send(embed=embed)
        await msg.add_reaction(u"\u2705")




    @ commands.command(pass_context=True, aliases=['o'])
    @ commands.has_role('Team Member')
    async def open(self, ctx, taskName, *, taskDescription):
        color = self.openColor
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

        currentStatus = "Open"
        embed = discord.Embed(
            title=taskName, description=taskDescription, color=color)
        embed.set_author(name=config[str(discordID)]["name"])
        embed.add_field(name="Time Assigned", value=time.asctime(
            time.localtime(ds)), inline=False)
        embed.add_field(
            name="Status", value=currentStatus, inline=False)
        embed.add_field(name="Assigned By", value=config[str(
            discordID)]["name"], inline=False)
        channel = self.bot.get_channel(config[str(discordID)]["channel_id"])
        msg = await channel.send(embed = embed)
        await msg.add_reaction(u"\u2705")




    @ commands.command(pass_context=True, aliases=['close', 'c'])
    @ commands.has_role('Team Member')
    async def complete(self, ctx, taskName):
        color = self.closedColor
        if(taskName == None):
            embed = discord.Embed(
                title="Please enter a task name!", color=color)
            await ctx.send(embed=embed)
            return

        query = f'''
        with user_tasks as (select tasks.taskName, tasks.discordID, tasks.ds , tasks.description, row_number() over(partition by tasks.taskName order by tasks.ds DESC) rnk
        from tasks
        where tasks.taskName = '{taskName}')

        select taskName, discordID, ds, description
        from user_tasks
        where rnk = 1
        '''
        self.cursor.execute(query)
        queryResult = self.cursor.fetchall()

        for result in queryResult:
            selectedTaskName = result[0]
            discordID = result[1]
            localTime = result[2]
            isActive = False            
            taskDescription = result[3]
            query = f'''INSERT INTO TASKS (isActive, discordID, ds, taskName, description, assignedBy) VALUES ({isActive}, {discordID}, {localTime}, '{selectedTaskName}', '{taskDescription}', '{ctx.author}')'''
    
            self.cursor.execute(query)
            self.conn.commit()

            completeMessages = [
                f"https://imgur.com/dRehYPL \n **{taskName}** completed, Good job!"]
            msg = await ctx.send(random.choice(completeMessages))

            embed = discord.Embed(
                title=taskName, description=taskDescription, color=color)
            embed.set_author(name=config[str(discordID)]["name"])
            embed.add_field(name="Time Completed", value=time.asctime(
                time.localtime(localTime)), inline=False)
            embed.add_field(name="Status", value="Closed", inline=False)
            embed.add_field(name="Closed By",
                            value=config[str(ctx.author.id)]["name"])
            
            channel = self.bot.get_channel(config["completed"]["channel_id"])
            await channel.send(embed = embed)
                
            await asyncio.sleep(10)
            await msg.delete()

           


        

        
    # Shows task embed when you ask for it in whatever channel you are in

    @ commands.command(pass_context=True, aliases=['t'])
    @ commands.has_role('Team Member')
    async def task(self, ctx, taskName=None):
        if(taskName == None):
            color = self.closedColor
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
                color = self.openColor
            else:
                isActive = 'Closed'
                color = self.closedColor
            taskDescription = result[4]

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



    @commands.command(pass_context = True, aliases = ['u'])
    @commands.has_role("Team Member")
    async def update(self, ctx, taskName, *, updatedTaskDescription):
        # Pulls relevant information out of tasks.db
        query = f'''
        with user_tasks as (select tasks.taskName, tasks.discordID, tasks.isActive, row_number() over(partition by tasks.taskName order by tasks.ds DESC) rnk
        from tasks
        where tasks.taskName = '{taskName}')

        select taskName, discordID, isActive
        from user_tasks        
        where rnk = 1
        and isActive = 1       
        '''
        self.cursor.execute(query)
        queryResult = self.cursor.fetchall()
        
        
        # Uses information from previous query and command arguments to update task in database
        for result in queryResult:
            selectedTaskName = result[0]
            assignedTo = result[1]
            isActive = True
            ds = int(time.time())
            updatedBy = ctx.author.id
            query = f'''INSERT INTO TASKS (isActive, discordID, ds, taskName, description, assignedBy) VALUES ({isActive}, {assignedTo}, {ds}, '{selectedTaskName}', '{updatedTaskDescription}', '{updatedBy}')'''
            self.cursor.execute(query)
            self.conn.commit()            

            # Sends updated task to assigned user
            embed = discord.Embed(title=taskName, description=updatedTaskDescription, color=self.openColor)
            embed.set_author(name=config[str(assignedTo)]["name"])
            embed.add_field(name="Update Time", value=time.asctime(
                time.localtime(ds)), inline=False)
            embed.add_field(
                name="Status", value="Open", inline=False)
            embed.add_field(name="Updated by",
                            value=config[str(ctx.author.id)]["name"])

            channel = self.bot.get_channel(
                config[str(assignedTo)]["channel_id"])
            
            # Sends message and reacts with a checkmark so Team Member can complete it at a later date
            msg = await channel.send(embed=embed)
            await msg.add_reaction(u"\u2705")


    # Listens for reactions on tasks even after the bot has been restarted
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, reaction):
        user = self.bot.get_user(reaction.user_id)
        channel = self.bot.get_channel(reaction.channel_id)
        message = await channel.fetch_message(reaction.message_id)
        if(user != self.bot.user and message.author == self.bot.user and len(message.embeds) > 0):
            taskName = message.embeds[0].title
            query = f'''
            with user_tasks as (select tasks.taskName, tasks.discordID, tasks.ds , tasks.description, row_number() over(partition by tasks.taskName order by tasks.ds DESC) rnk
            from tasks
            where tasks.taskName = '{taskName}')

            select taskName, discordID, ds, description
            from user_tasks
            where rnk = 1
            '''
            self.cursor.execute(query)
            queryResult = self.cursor.fetchall()

            for result in queryResult:
                selectedTaskName = result[0]
                discordID = result[1]
                localTime = result[2]
                isActive = False
                taskDescription = result[3]
                query = f'''INSERT INTO TASKS (isActive, discordID, ds, taskName, description, assignedBy) VALUES ({isActive}, {discordID}, {localTime}, '{selectedTaskName}', '{taskDescription}', '{user}')'''

                self.cursor.execute(query)
                self.conn.commit()

                completeMessages = [
                    f"https://imgur.com/dRehYPL \n **{taskName}** completed, Good job!"]
                msg = await channel.send(random.choice(completeMessages))

                embed = discord.Embed(
                    title=taskName, description=taskDescription, color=self.openColor)
                embed.set_author(name=config[str(discordID)]["name"])
                embed.add_field(name="Time Completed", value=time.asctime(
                    time.localtime(localTime)), inline=False)
                embed.add_field(
                    name="Status", value="Closed", inline=False)
                embed.add_field(name="Closed By",
                                value=config[str(user.id)]["name"])

                channel = self.bot.get_channel(config["completed"]["channel_id"])
                await channel.send(embed=embed)
                await message.delete()
                await asyncio.sleep(10)
                await msg.delete()




def setup(bot):
    bot.add_cog(Tasks(bot))
