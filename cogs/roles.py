import discord
from discord import app_commands
from discord.colour import Color
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed
import asyncio
import random
import sqlite3
import time
from config import config


class Roles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.openColor = discord.Colour(3665255)
        self.closedColor = discord.Colour(4804175)

    @commands.Cog.listener()
    async def on_ready(self):
        print("ReactionRoles ready.")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = self.bot.get_channel(config["roles"]).guild
        crewmate = guild.get_role(config["crewmate"])
        updates = guild.get_role(config["updates"])
        await member.add_roles(crewmate)
        await member.add_roles(updates)
        channel = guild.get_channel(config["welcomeChannel"])
        rolesChannel = guild.get_channel(config["roles"])
        rolesMessage = await rolesChannel.fetch_message(config["rolesMessageID"])
        updatesEmoji = self.bot.get_emoji(config["updatesEmojiID"])
        await rolesMessage.add_reaction(updatesEmoji)
        msg = await channel.send(f"Welcome {member.mention}!!")
        await asyncio.sleep(10)
        await msg.delete()

    # Adds role based on reaction, has the reverse effect for the updates role
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        user = self.bot.get_user(payload.user_id)
        if payload.channel_id != config["roles"] or user == self.bot.user:
            return

        if payload.emoji.id != config["updatesEmojiID"]:
            emoji = self.bot.get_emoji(payload.emoji.id)
            emoji_id = emoji.id if emoji else payload.emoji.id
            query = '''
                WITH temp_roles AS (
                    SELECT emojiID, roleID, ROW_NUMBER() OVER (PARTITION BY emojiID ORDER BY emojiID DESC) AS rnk
                    FROM roles
                    WHERE emojiID = ?
                )
                SELECT roleID
                FROM temp_roles
                WHERE rnk = 1
            '''
            cursor = self.bot.db.cursor()
            cursor.execute(query, (emoji_id,))
            queryResult = cursor.fetchall()

            for result in queryResult:
                roleID = result[0]
                guild = self.bot.get_channel(config["roles"]).guild
                role = guild.get_role(roleID)
                member = guild.get_member(user.id)
                if member and role:
                    await member.add_roles(role)
                cursor.execute('SELECT emojiID, roleID, roleDescription FROM roles')
                all_roles = cursor.fetchall()
                for row in all_roles:
                    role = guild.get_role(row[1])
                    emoji = self.bot.get_emoji(row[0])

        if payload.emoji.id == config["updatesEmojiID"]:
            guild = self.bot.get_channel(config["roles"]).guild
            updatesRole = guild.get_role(config["updates"])
            member = guild.get_member(user.id)
            if member and updatesRole:
                await member.remove_roles(updatesRole)

    # Removes role based on reaction, has the reverse effect for the updates role
    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        user = self.bot.get_user(payload.user_id)
        if payload.channel_id != config["roles"] or user == self.bot.user:
            return

        if payload.emoji.id != config["updatesEmojiID"]:
            emoji = self.bot.get_emoji(payload.emoji.id)
            emoji_id = emoji.id if emoji else payload.emoji.id
            query = '''
                WITH temp_roles AS (
                    SELECT emojiID, roleID, ROW_NUMBER() OVER (PARTITION BY emojiID ORDER BY emojiID DESC) AS rnk
                    FROM roles
                    WHERE emojiID = ?
                )
                SELECT roleID
                FROM temp_roles
                WHERE rnk = 1
            '''
            cursor = self.bot.db.cursor()
            cursor.execute(query, (emoji_id,))
            queryResult = cursor.fetchall()

            for result in queryResult:
                roleID = result[0]
                guild = self.bot.get_channel(config["roles"]).guild
                role = guild.get_role(roleID)
                member = guild.get_member(user.id)
                if member and role:
                    await member.remove_roles(role)

        if payload.emoji.id == config["updatesEmojiID"]:
            guild = self.bot.get_channel(config["roles"]).guild
            updatesRole = guild.get_role(config["updates"])
            member = guild.get_member(user.id)
            if member and updatesRole:
                await member.add_roles(updatesRole)

    @app_commands.command(name="roleall", description="Add a role to all applicable members")
    async def roleAll(self, interaction: discord.Interaction, role: discord.Role):
        adminRole = discord.utils.get(interaction.guild.roles, name="Admin")
        if adminRole in interaction.user.roles:
            await interaction.response.defer()
            guild = self.bot.get_channel(config["roles"]).guild
            listMembers = [m async for m in guild.fetch_members(limit=None)]
            for i in listMembers:
                if role not in i.roles:
                    await i.add_roles(role)
                    print(f"Role added to {i.name}")
                else:
                    print(f"{i.name} already has this role!")
            print("Role added to all applicable members")
            await interaction.followup.send("Role added to all applicable members")
        else:
            await interaction.response.send_message(
                "You do not have permission to use this command!", ephemeral=True
            )

    # @commands.command()
    # async def temp(self, ctx, reaction: discord.Emoji):
    #     adminRole = discord.utils.get(ctx.guild.roles, name="Admin")
    #     if adminRole in ctx.author.roles:
    #         channel = self.bot.get_channel(config["roles"])
    #         embed = discord.Embed(
    #             title = "Updates Role Removal",
    #             description = "React to this to remove the updates role from yourself",
    #             color = discord.Colour(3665255))
    #         message = await channel.send(embed = embed)
    #         await message.add_reaction(reaction)

    @app_commands.command(name="maprole", description="Map a reaction emoji to a role")
    @app_commands.checks.has_role("Team Member")
    async def mapRole(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        reaction: str,
        role_description: str,
    ):
        partial_emoji = discord.PartialEmoji.from_str(reaction.strip())
        emoji_id = partial_emoji.id
        if emoji_id is None:
            if reaction.strip().isdigit():
                emoji_id = int(reaction.strip())
            else:
                found = discord.utils.get(self.bot.emojis, name=reaction.strip(": "))
                if found:
                    emoji_id = found.id

        if emoji_id is None:
            await interaction.response.send_message(
                "Please provide a valid custom emoji.", ephemeral=True
            )
            return

        cursor = self.bot.db.cursor()
        cursor.execute(
            "INSERT INTO roles (emojiID, roleID, roleDescription) VALUES (?, ?, ?)",
            (emoji_id, role.id, role_description),
        )
        self.bot.db.commit()
        await interaction.response.send_message(
            f"Mapped {reaction} to {role.mention}.", ephemeral=True
        )

    @app_commands.command(name="roles", description="Display the reaction roles embed")
    @app_commands.checks.has_role("Team Member")
    async def roles(self, interaction: discord.Interaction):
        channel = self.bot.get_channel(config["roles"])
        cursor = self.bot.db.cursor()
        cursor.execute("SELECT emojiID, roleID, roleDescription FROM roles")
        queryResult = cursor.fetchall()

        emojis = []
        roleIDs = []
        for result in queryResult:
            emojis.append(result[0])
            roleIDs.append(result[1])

        roles = []
        for roleID in roleIDs:
            guild = self.bot.get_channel(config["roles"]).guild
            role_obj = guild.get_role(roleID)
            if role_obj:
                roles.append(role_obj.mention)

        embed = discord.Embed(title="Roles", description=" ".join(roles))
        message = await channel.send(embed=embed)
        for emojiID in emojis:
            emoji = self.bot.get_emoji(emojiID)
            if emoji:
                await message.add_reaction(emoji)

        await interaction.response.send_message(
            "Roles message sent!", ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(Roles(bot))
