import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed
import asyncio
import random
from pathlib import Path
from config import config

# astra_console.py writes a cog name here to reload it in the running
# bot. A file rather than an HTTP route: the audiobook site binds
# 0.0.0.0, so a control route could be reached from outside the host.
# The watcher lives in this cog because a watcher inside the cog being
# reloaded would cancel itself partway through.
RELOAD_FLAG = Path(__file__).resolve().parent.parent / "logs" / "reload_cog.flag"


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="kick", description="Kicks the specified user from the server")
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.checks.bot_has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, user: discord.Member):
        """Kicks the specified user from the server"""
        await interaction.guild.kick(user)
        await interaction.response.send_message(f"Kicked <@{user.id}>")

    @app_commands.command(name="streamlive", description="Set the bot presence to streaming live")
    async def streamLive(self, interaction: discord.Interaction):
        role = discord.utils.get(interaction.guild.roles, name="Admin")
        if role in interaction.user.roles:
            await self.bot.change_presence(
                activity=discord.Streaming(name="My Stream", url="https://twitch.tv/playastra")
            )
            await interaction.response.send_message("Stream status set to live!", ephemeral=True)
        else:
            await interaction.response.send_message(
                "You do not have permission to use this command!", ephemeral=True
            )

    @app_commands.command(name="streamended", description="Reset the bot presence after a stream ends")
    async def streamEnded(self, interaction: discord.Interaction):
        role = discord.utils.get(interaction.guild.roles, name="Admin")
        if role in interaction.user.roles:
            await self.bot.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.listening,
                    name="Metronome rant about girls",
                )
            )
            await interaction.response.send_message("Stream status ended!", ephemeral=True)
        else:
            await interaction.response.send_message(
                "You do not have permission to use this command!", ephemeral=True
            )

    # Move out of points.
    @app_commands.command(name="speak", description="Make the bot send a message")
    async def speak(self, interaction: discord.Interaction, message: str):  #### INPUT
        role = discord.utils.get(interaction.guild.roles, name="Admin")
        channel = self.bot.get_channel(584936481924055050)
        if role in interaction.user.roles:
            if channel:
                await channel.send(message)
            await interaction.response.send_message("Message sent!", ephemeral=True)
        else:
            await interaction.response.send_message(
                "You do not have permission to use this command!", ephemeral=True
            )

    async def cog_load(self):
        self._reload_watcher = asyncio.create_task(self._watch_reload_flag())

    async def cog_unload(self):
        self._reload_watcher.cancel()

    async def _watch_reload_flag(self):
        while True:
            await asyncio.sleep(2)
            try:
                await self._check_reload_flag()
            except Exception as e:
                print(f"Console reload failed: {type(e).__name__}: {e}")

    async def _check_reload_flag(self):
        if not RELOAD_FLAG.exists():
            return
        cog_name = RELOAD_FLAG.read_text(encoding="utf-8").strip()
        RELOAD_FLAG.unlink()
        await self.bot.reload_extension(f"cogs.{cog_name}")
        print(f"{cog_name} reloaded from the console")

    @app_commands.command(name="reloadcog", description="Reload a cog extension")
    async def reloadCog(self, interaction: discord.Interaction, cog_name: str):
        role = discord.utils.get(interaction.guild.roles, name="Admin")
        if role in interaction.user.roles:
            try:
                await self.bot.unload_extension(f"cogs.{cog_name}")
                await self.bot.load_extension(f"cogs.{cog_name}")
                await self.bot.tree.sync()
                print(f"{cog_name} successfully reloaded!")
                await interaction.response.send_message(f"{cog_name} successfully reloaded!")
            except commands.ExtensionNotLoaded:
                await interaction.response.send_message("This cog is not loaded", ephemeral=True)
        else:
            await interaction.response.send_message(
                "You do not have permission to use this command!", ephemeral=True
            )

    # Error catching listener
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        """The event triggered when an error is raised while invoking a command.
        Parameters
        ------------
        ctx: commands.Context
            The context used for command invocation.
        error: commands.CommandError
            The Exception raised.
        """

        # This prevents any commands with local handlers being handled here in on_command_error.
        if hasattr(ctx.command, "on_error"):
            return

        # This prevents any cogs with an overwritten cog_command_error being handled here.
        cog = ctx.cog
        if cog:
            if cog._get_overridden_method(cog.cog_command_error) is not None:
                return

        ignored = (commands.CommandNotFound,)

        # Allows us to check for original exceptions raised and sent to CommandInvokeError.
        # If nothing is found. We keep the exception passed to on_command_error.
        error = getattr(error, "original", error)

        # Anything in ignored will return and prevent anything happening.
        if isinstance(error, ignored):
            msg = await ctx.send(f"Invalid Command {ctx.message.content}")
            await ctx.message.delete()
            await asyncio.sleep(5)
            await msg.delete()
            return

    ## Deletes messages when given a number of messages
    @app_commands.command(name="purge", description="Deletes a given number of messages")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, number: int):
        await interaction.response.defer(ephemeral=True)
        msgs = [
            message
            async for message in interaction.channel.history(limit=int(number))
        ]
        await interaction.channel.delete_messages(msgs)
        await interaction.followup.send(f"Deleted {len(msgs)} messages.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Moderation(bot))
