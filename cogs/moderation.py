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

        # if isinstance(error, commands.DisabledCommand):
        #     await ctx.send(f'{ctx.command} has been disabled.')

        # elif isinstance(error, commands.NoPrivateMessage):
        #     try:
        #         await ctx.author.send(
        #             f'{ctx.command} can not be used in Private Messages.')
        #     except discord.HTTPException:
        #         pass

        # # For this error example we check to see where it came from...
        # elif isinstance(error, commands.BadArgument):
        #     if ctx.command.qualified_name == 'tag list':  # Check if the command being invoked is 'tag list'
        #         await ctx.send(
        #             'I could not find that member. Please try again.')

        # else:
        #     # All other Errors not returned come here. And we can just print the default TraceBack.
        #     print('Ignoring exception in command {}:'.format(ctx.command),
        #           file=sys.stderr)
        #     traceback.print_exception(type(error),
        #                               error,
        #                               error.__traceback__,
        #                               file=sys.stderr)

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