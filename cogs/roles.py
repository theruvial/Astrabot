from asyncio.windows_events import NULL
import discord
from discord import mentions
from discord.colour import Color
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed
import asyncio
import asyncpg
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
        print(f"ReactionRoles ready.")




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
        await rolesMessage.add_reaction(updatesEmoji, member)
        msg = await channel.send(f"Welcome {member.mention}!!")
        await asyncio.sleep(10)
        await msg.delete()



    # Adds role based on reaction, has the reverse effect for the updates role
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        user = self.bot.get_user(payload.user_id)       
        if (payload.channel_id != config["roles"] or user == self.bot.user):
            return

        if(payload.emoji.id != config["updatesEmojiID"]):
            emoji = self.bot.get_emoji(payload.emoji.id)
            query = f'''
                with temp_roles as (select roles."emojiID", roles."roleID", row_number() over(partition by roles."emojiID" order by roles."emojiID" DESC) rnk
                from roles
                where roles."emojiID" = '{emoji.id}')

                select "roleID"
                from temp_roles
                where rnk = 1
                '''
            await self.bot.pg_con.execute(query)
            queryResult = await self.bot.pg_con.fetch(query)

            for result in queryResult:
                roleID = result[0]
                guild = self.bot.get_channel(config["roles"]).guild
                role = guild.get_role(roleID)
                member = guild.get_member(user.id)
                await member.add_roles(role)
                query = '''select * from roles'''
                await self.bot.pg_con.execute(query)
                queryResult = await self.bot.pg_con.fetch(query)
                for result in queryResult:
                    role = guild.get_role(result[1])
                    emoji = self.bot.get_emoji(result[0])
        
        if(payload.emoji.id == config["updatesEmojiID"]):                        
            guild = self.bot.get_channel(config["roles"]).guild
            updatesRole = guild.get_role(config["updates"])
            member = guild.get_member(user.id)
            await member.remove_roles(updatesRole)


    # Removes role based on reaction, has the reverse effect for the updates role
    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        user = self.bot.get_user(payload.user_id)
        if (payload.channel_id != config["roles"] or user == self.bot.user):
            return
        
        if(payload.emoji.id != config["updatesEmojiID"]):
            emoji = self.bot.get_emoji(payload.emoji.id)
            query = f'''
                with temp_roles as (select roles."emojiID", roles."roleID", row_number() over(partition by roles."emojiID" order by roles."emojiID" DESC) rnk
                from roles
                where roles."emojiID" = '{emoji.id}')

                select "roleID"
                from temp_roles
                where rnk = 1
                '''
            await self.bot.pg_con.execute(query)
            queryResult = await self.bot.pg_con.fetch(query)

            for result in queryResult:
                roleID = result[0]
                guild = self.bot.get_channel(config["roles"]).guild
                role = guild.get_role(roleID)
                member = guild.get_member(user.id)                
                await member.remove_roles(role)

        if (payload.emoji.id == config["updatesEmojiID"]):            
            guild = self.bot.get_channel(config["roles"]).guild
            updatesRole = guild.get_role(config["updates"])
            member = guild.get_member(user.id)
            await member.add_roles(updatesRole)

    @commands.command(pass_context=True, aliases=['ra', 'all'])
    async def roleAll(self, ctx, role: discord.Role):
        adminRole = discord.utils.get(ctx.guild.roles, name="Admin")
        if adminRole in ctx.author.roles:
            guild = self.bot.get_channel(config["roles"]).guild
            listMembers = await guild.fetch_members().flatten()
            for i in listMembers:
                if not role in i.roles:
                    await i.add_roles(role)
                    print(f"Role added to {i.name}")
                else:
                    print(f"{i.name} already has this role!")            
            print("Role added to all applicable members")
        else:
            msg = await ctx.send("You do not have permission to use this command!")
            await asyncio.sleep(3)
            await msg.delete()

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


    @commands.command(pass_context=True, aliases=['map', 'm'])
    @commands.has_role('Team Member')
    async def mapRole(self, ctx, role: discord.Role, reaction: discord.Emoji, *, roleDescription):
        query = f'''INSERT INTO ROLES ("emojiID", "roleID", "roleDescription") VALUES ({reaction.id},{role.id},'{roleDescription}')'''
        await self.bot.pg_con.execute(query)
        

    @commands.command(pass_context=True, aliases=['r','role'])
    @commands.has_role('Team Member')
    async def roles(self, ctx):
        channel = self.bot.get_channel(config["roles"])
        query = '''select * from roles'''

        await self.bot.pg_con.execute(query)
        queryResult = await self.bot.pg_con.fetch(query)
        emojis = []
        roleIDs = []
        for result in queryResult:
            emojis.append(result[0])
            roleIDs.append(result[1])

        roles = []
        for roleID in roleIDs:
            guild = self.bot.get_channel(config["roles"]).guild
            roles.append(guild.get_role(roleID).mention)


        embed = discord.Embed(title="Roles", description=' '.join(roles))
        message = await channel.send(embed = embed)
        for emojiID in emojis:
            emoji = self.bot.get_emoji(emojiID)
            await message.add_reaction(emoji)



def setup(bot):
    bot.add_cog(Roles(bot))
