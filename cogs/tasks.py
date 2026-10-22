import discord
from discord import app_commands
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

    @commands.Cog.listener()
    async def on_ready(self):
        sql = '''CREATE TABLE IF NOT EXISTS tasks(
                        isActive BOOLEAN NOT NULL,
                        discordID BIGINT NOT NULL,
                        ds BIGINT NOT NULL,
                        taskName TEXT NOT NULL,
                        description TEXT NOT NULL,
                        assignedBy BIGINT
        )
        '''
        cursor = self.bot.db.cursor()
        cursor.execute(sql)
        self.bot.db.commit()
        print("Task Cog Ready!")

    @app_commands.command(name="gettasks", description="Get active tasks for a user")
    @app_commands.checks.has_role("Team Member")
    async def getTasks(
        self, interaction: discord.Interaction, user: discord.Member = None
    ):
        if user is None:
            user = interaction.user
        color = discord.Colour(14885911)
        query = '''
        WITH user_tasks AS (
            SELECT taskName, isActive, ds,
                   ROW_NUMBER() OVER (PARTITION BY taskName ORDER BY ds DESC) AS rnk
            FROM tasks
            WHERE discordID = ?
        )
        SELECT taskName
        FROM user_tasks
        WHERE rnk = 1
        AND isActive = 1
        '''

        cursor = self.bot.db.cursor()
        cursor.execute(query, (user.id,))
        queryResult = cursor.fetchall()

        if not queryResult:
            await interaction.response.send_message("No active tasks.")
        else:
            embeds = [
                discord.Embed(title=result[0], color=color)
                for result in queryResult
            ]
            await interaction.response.send_message(embeds=embeds)

    @app_commands.command(name="assigntask", description="Assign a task to a team member")
    @app_commands.checks.has_role("Team Member")
    async def assignTask(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        task_name: str,
        task_description: str,
    ):
        color = self.openColor
        if not task_name:
            embed = discord.Embed(title="Please enter a task name!", color=color)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        elif not task_description:
            embed = discord.Embed(title="Please enter a description!", color=color)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        isActive = True
        discordID = int(user.id)
        ds = int(time.time())
        query = '''
            INSERT INTO tasks (isActive, discordID, ds, taskName, description, assignedBy)
            VALUES (?, ?, ?, ?, ?, ?)
        '''

        cursor = self.bot.db.cursor()
        cursor.execute(
            query,
            (
                isActive,
                discordID,
                ds,
                task_name,
                task_description,
                interaction.user.id,
            ),
        )
        self.bot.db.commit()
        await interaction.response.send_message(
            f"{task_name} assigned to {user.mention}"
        )

        currentStatus = "Open"
        embed = discord.Embed(
            title=task_name, description=task_description, color=color
        )
        embed.set_author(name=config[str(discordID)]["name"])
        embed.add_field(
            name="Time Assigned",
            value=time.asctime(time.localtime(ds)),
            inline=False,
        )
        embed.add_field(name="Status", value=currentStatus, inline=False)
        embed.add_field(
            name="Assigned By",
            value=config[str(interaction.user.id)]["name"],
            inline=False,
        )
        channel = self.bot.get_channel(config[str(discordID)]["channel_id"])
        msg = await channel.send(embed=embed)
        await msg.add_reaction("\u2705")

    @app_commands.command(name="open", description="Open a new task for yourself")
    @app_commands.checks.has_role("Team Member")
    async def open(
        self,
        interaction: discord.Interaction,
        task_name: str,
        task_description: str,
    ):
        color = self.openColor
        if not task_name:
            embed = discord.Embed(title="Please enter a task name!", color=color)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        elif not task_description:
            embed = discord.Embed(title="Please enter a description!", color=color)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        isActive = True
        discordID = int(interaction.user.id)
        ds = int(time.time())
        query = '''
            INSERT INTO tasks (isActive, discordID, ds, taskName, description, assignedBy)
            VALUES (?, ?, ?, ?, ?, ?)
        '''

        cursor = self.bot.db.cursor()
        cursor.execute(
            query,
            (
                isActive,
                discordID,
                ds,
                task_name,
                task_description,
                interaction.user.id,
            ),
        )
        self.bot.db.commit()
        await interaction.response.send_message(
            f"{task_name} assigned to {interaction.user.mention}"
        )

        currentStatus = "Open"
        embed = discord.Embed(
            title=task_name, description=task_description, color=color
        )
        embed.set_author(name=config[str(discordID)]["name"])
        embed.add_field(
            name="Time Assigned",
            value=time.asctime(time.localtime(ds)),
            inline=False,
        )
        embed.add_field(name="Status", value=currentStatus, inline=False)
        embed.add_field(
            name="Assigned By",
            value=config[str(discordID)]["name"],
            inline=False,
        )
        channel = self.bot.get_channel(config[str(discordID)]["channel_id"])
        msg = await channel.send(embed=embed)
        await msg.add_reaction("\u2705")

    @app_commands.command(name="complete", description="Mark a task as completed")
    @app_commands.checks.has_role("Team Member")
    async def complete(self, interaction: discord.Interaction, task_name: str):
        color = self.closedColor
        if not task_name:
            embed = discord.Embed(title="Please enter a task name!", color=color)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        query = '''
        WITH user_tasks AS (
            SELECT taskName, discordID, ds, description,
                   ROW_NUMBER() OVER (PARTITION BY taskName ORDER BY ds DESC) AS rnk
            FROM tasks
            WHERE taskName = ?
        )
        SELECT taskName, discordID, ds, description
        FROM user_tasks
        WHERE rnk = 1
        '''
        cursor = self.bot.db.cursor()
        cursor.execute(query, (task_name,))
        queryResult = cursor.fetchall()

        if not queryResult:
            await interaction.response.send_message(
                f"No task found with name **{task_name}**.", ephemeral=True
            )
            return

        for result in queryResult:
            selectedTaskName = result[0]
            discordID = result[1]
            localTime = result[2]
            isActive = False
            taskDescription = result[3]
            insert_query = '''
                INSERT INTO tasks (isActive, discordID, ds, taskName, description, assignedBy)
                VALUES (?, ?, ?, ?, ?, ?)
            '''
            cursor.execute(
                insert_query,
                (
                    isActive,
                    discordID,
                    localTime,
                    selectedTaskName,
                    taskDescription,
                    interaction.user.id,
                ),
            )
        self.bot.db.commit()

        completeMessages = [
            f"https://imgur.com/dRehYPL \n **{task_name}** completed, Good job!"
        ]
        await interaction.response.send_message(random.choice(completeMessages))

        for result in queryResult:
            discordID = result[1]
            localTime = result[2]
            taskDescription = result[3]
            embed = discord.Embed(
                title=task_name, description=taskDescription, color=color
            )
            embed.set_author(name=config[str(discordID)]["name"])
            embed.add_field(
                name="Time Completed",
                value=time.asctime(time.localtime(localTime)),
                inline=False,
            )
            embed.add_field(name="Status", value="Closed", inline=False)
            embed.add_field(
                name="Closed By",
                value=config[str(interaction.user.id)]["name"],
            )

            channel = self.bot.get_channel(config["completed"]["channel_id"])
            await channel.send(embed=embed)

        await asyncio.sleep(10)
        await interaction.delete_original_response()

    # Shows task embed when you ask for it in whatever channel you are in
    @app_commands.command(name="task", description="Show information for a specific task")
    @app_commands.checks.has_role("Team Member")
    async def task(self, interaction: discord.Interaction, task_name: str):
        if not task_name:
            color = self.closedColor
            embed = discord.Embed(title="Please enter a task name!", color=color)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        query = '''
        WITH user_tasks AS (
            SELECT taskName, discordID, isActive, ds, description, assignedBy,
                   ROW_NUMBER() OVER (PARTITION BY taskName ORDER BY ds DESC) AS rnk
            FROM tasks
            WHERE taskName = ?
        )
        SELECT taskName, discordID, ds, isActive, description, assignedBy
        FROM user_tasks
        WHERE rnk = 1
        '''

        cursor = self.bot.db.cursor()
        cursor.execute(query, (task_name,))
        queryResult = cursor.fetchall()
        if not queryResult:
            await interaction.response.send_message(
                f"No task found with name **{task_name}**.", ephemeral=True
            )
            return

        embeds = []
        for result in queryResult:
            selectedTaskName = result[0]
            discordID = result[1]
            localTime = result[2]
            if result[3] == 1:
                isActive = "Open"
                color = self.openColor
            else:
                isActive = "Closed"
                color = self.closedColor
            taskDescription = result[4]

            embed = discord.Embed(
                title=selectedTaskName,
                description=taskDescription,
                color=color,
            )
            embed.set_author(name=config[str(discordID)]["name"])
            embed.add_field(
                name="Time Assigned",
                value=time.asctime(time.localtime(localTime)),
                inline=False,
            )
            embed.add_field(name="Status", value=isActive, inline=False)
            embed.add_field(
                name="Assigned By",
                value=config[str(result[5])]["name"],
                inline=False,
            )
            embeds.append(embed)

        await interaction.response.send_message(embeds=embeds)

    @app_commands.command(name="update", description="Update an active task's description")
    @app_commands.checks.has_role("Team Member")
    async def update(
        self,
        interaction: discord.Interaction,
        task_name: str,
        updated_task_description: str,
    ):
        # Pulls relevant information out of tasks.db
        query = '''
        WITH user_tasks AS (
            SELECT taskName, discordID, isActive,
                   ROW_NUMBER() OVER (PARTITION BY taskName ORDER BY ds DESC) AS rnk
            FROM tasks
            WHERE taskName = ?
        )
        SELECT taskName, discordID, isActive
        FROM user_tasks
        WHERE rnk = 1
        AND isActive = 1
        '''
        cursor = self.bot.db.cursor()
        cursor.execute(query, (task_name,))
        queryResult = cursor.fetchall()

        if not queryResult:
            await interaction.response.send_message(
                f"No active task found with name **{task_name}**.", ephemeral=True
            )
            return

        # Uses information from previous query and command arguments to update task in database
        for result in queryResult:
            selectedTaskName = result[0]
            assignedTo = result[1]
            isActive = True
            ds = int(time.time())
            updatedBy = interaction.user.id
            insert_query = '''
                INSERT INTO tasks (isActive, discordID, ds, taskName, description, assignedBy)
                VALUES (?, ?, ?, ?, ?, ?)
            '''
            cursor.execute(
                insert_query,
                (
                    isActive,
                    assignedTo,
                    ds,
                    selectedTaskName,
                    updated_task_description,
                    updatedBy,
                ),
            )
            self.bot.db.commit()

            # Sends updated task to assigned user
            embed = discord.Embed(
                title=task_name,
                description=updated_task_description,
                color=self.openColor,
            )
            embed.set_author(name=config[str(assignedTo)]["name"])
            embed.add_field(
                name="Update Time",
                value=time.asctime(time.localtime(ds)),
                inline=False,
            )
            embed.add_field(name="Status", value="Open", inline=False)
            embed.add_field(
                name="Updated by",
                value=config[str(interaction.user.id)]["name"],
            )

            channel = self.bot.get_channel(config[str(assignedTo)]["channel_id"])

            # Sends message and reacts with a checkmark so Team Member can complete it at a later date
            msg = await channel.send(embed=embed)
            await msg.add_reaction("\u2705")

        await interaction.response.send_message(
            f"Updated task **{task_name}**.", ephemeral=True
        )

    # Listens for reactions on tasks even after the bot has been restarted
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, reaction: discord.RawReactionActionEvent):
        user = self.bot.get_user(reaction.user_id)
        channel = self.bot.get_channel(reaction.channel_id)
        if not channel:
            return
        message = await channel.fetch_message(reaction.message_id)
        if (
            user != self.bot.user
            and message.author == self.bot.user
            and len(message.embeds) > 0
        ):
            taskName = message.embeds[0].title
            query = '''
            WITH user_tasks AS (
                SELECT taskName, discordID, ds, description,
                       ROW_NUMBER() OVER (PARTITION BY taskName ORDER BY ds DESC) AS rnk
                FROM tasks
                WHERE taskName = ?
            )
            SELECT taskName, discordID, ds, description
            FROM user_tasks
            WHERE rnk = 1
            '''
            cursor = self.bot.db.cursor()
            cursor.execute(query, (taskName,))
            queryResult = cursor.fetchall()

            for result in queryResult:
                selectedTaskName = result[0]
                discordID = result[1]
                localTime = result[2]
                isActive = False
                taskDescription = result[3]
                insert_query = '''
                    INSERT INTO tasks (isActive, discordID, ds, taskName, description, assignedBy)
                    VALUES (?, ?, ?, ?, ?, ?)
                '''
                cursor.execute(
                    insert_query,
                    (
                        isActive,
                        discordID,
                        localTime,
                        selectedTaskName,
                        taskDescription,
                        user.id,
                    ),
                )
                self.bot.db.commit()

                completeMessages = [
                    f"https://imgur.com/dRehYPL \n **{taskName}** completed, Good job!"
                ]
                msg = await channel.send(random.choice(completeMessages))

                embed = discord.Embed(
                    title=taskName,
                    description=taskDescription,
                    color=self.openColor,
                )
                embed.set_author(name=config[str(discordID)]["name"])
                embed.add_field(
                    name="Time Completed",
                    value=time.asctime(time.localtime(localTime)),
                    inline=False,
                )
                embed.add_field(name="Status", value="Closed", inline=False)
                embed.add_field(
                    name="Closed By",
                    value=config[str(user.id)]["name"],
                )

                completed_channel = self.bot.get_channel(
                    config["completed"]["channel_id"]
                )
                await completed_channel.send(embed=embed)
                await message.delete()
                await asyncio.sleep(10)
                await msg.delete()


async def setup(bot):
    await bot.add_cog(Tasks(bot))
