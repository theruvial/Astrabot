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


    @commands.command(pass_context=True, aliases=['reload'])
    async def reloadCog(self, ctx, cogName):
        role = discord.utils.get(ctx.guild.roles, name="Admin")
        if role in ctx.author.roles:
            try:
                self.bot.unload_extension(f"cogs.{cogName}")
                self.bot.load_extension(f"cogs.{cogName}")
                print(f"{cogName} successfully reloaded!")
                await ctx.send(f"{cogName} successfully reloaded!")
            except commands.ExtensionNotLoaded:
                await ctx.send("This cog is not loaded")
        else:
            msg = await ctx.send(
                "You do not have permission to use this command!")
            await asyncio.sleep(3)
            await msg.delete()

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
        if hasattr(ctx.command, 'on_error'):
            return

        # This prevents any cogs with an overwritten cog_command_error being handled here.
        cog = ctx.cog
        if cog:
            if cog._get_overridden_method(cog.cog_command_error) is not None:
                return

        ignored = (commands.CommandNotFound, )

        # Allows us to check for original exceptions raised and sent to CommandInvokeError.
        # If nothing is found. We keep the exception passed to on_command_error.
        error = getattr(error, 'original', error)

        # Anything in ignored will return and prevent anything happening.
        if isinstance(error, ignored):
            msg = await ctx.send(f"Invalid Command {ctx.message.content}")
            await ctx.message.delete()
            await asyncio.sleep(5)
            await msg.delete()
            return

    ## Deletes messages when given a number of messages
    @commands.command()
    @commands.has_permissions(manage_messages=True)
    async def purge(self, ctx, number):
        msgs = [] #Empty list to put all the messages in the log
        msgsToDelete = int(number) + 1 #Converting the amount of messages to delete to an integer
        async for message in ctx.message.channel.history(limit=msgsToDelete):
            msgs.append(message)
        await ctx.message.channel.delete_messages(msgs)

def setup(bot):
    bot.add_cog(Moderation(bot))