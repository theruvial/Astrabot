from asyncio.windows_events import NULL
import discord
from discord import mentions
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

        self.bot = bot
        self.conn = sqlite3.connect("thi.db")
        self.cursor = self.conn.cursor()
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS roles (
                        `emojiID` INTEGER NOT NULL,
                        `roleID` INTEGER NOT NULL,
                        `roleDescription` TEXT NOT NULL                       
                    )
        ''')

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"ReactionRoles ready.")




    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = self.bot.get_channel(config["roles"]).guild
        role = guild.get_role(config["defaultRole"])
        member.add_roles(role)
        channel = guild.get_channel(config["welcomeChannel"])
        embed = discord.embed(
            title = "Welcome!",
            description = member.mention,
            Color = discord.Colour(3665255))
        msg = await channel.send(embed = embed)
        await asyncio.sleep(10)
        await msg.delete()



    # Adds role based on reaction
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        user = self.bot.get_user(payload.user_id)
        if (payload.channel_id != config["roles"] or user == self.bot.user):
            return

        emoji = self.bot.get_emoji(payload.emoji.id)
        query = f'''
            with temp_roles as (select roles.emojiID, roles.roleID, row_number() over(partition by roles.emojiID order by roles.emojiID DESC) rnk
            from roles
            where roles.emojiID = '{emoji.id}')

            select roleID
            from temp_roles
            where rnk = 1
            '''
        self.cursor.execute(query)
        queryResult = self.cursor.fetchall()

        for result in queryResult:
            roleID = result[0]
            guild = self.bot.get_channel(config["roles"]).guild
            role = guild.get_role(roleID)
            member = guild.get_member(user.id)
            # print(type(role))
            await member.add_roles(role)


    # Removes role based on reaction
    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        user = self.bot.get_user(payload.user_id)
        if (payload.channel_id != config["roles"] or user == self.bot.user):
            return

        emoji = self.bot.get_emoji(payload.emoji.id)
        query = f'''
            with temp_roles as (select roles.emojiID, roles.roleID, row_number() over(partition by roles.emojiID order by roles.emojiID DESC) rnk
            from roles
            where roles.emojiID = '{emoji.id}')

            select roleID
            from temp_roles
            where rnk = 1
            '''
        self.cursor.execute(query)
        queryResult = self.cursor.fetchall()

        for result in queryResult:
            roleID = result[0]
            guild = self.bot.get_channel(config["roles"]).guild
            role = guild.get_role(roleID)
            member = guild.get_member(user.id)
            # print(type(role))
            await member.remove_roles(role)

    @commands.command(pass_context=True, aliases=['ra', 'all'])
    @commands.has_role('Admin')
    async def roleAll(self, ctx, role: discord.Role):
        guild = self.bot.get_channel(config["roles"]).guild
        listMembers = await guild.fetch_members().flatten()        
        for i in listMembers:
            if not role in i.roles:
                await i.add_roles(role)
                print(f"Role added to {i.name}")
            else:
                print(f"{i.name} already has this role!")
            # await asyncio.sleep(1)
        print("Role added to all applicable members")
            

    @commands.command(pass_context=True, aliases=['map', 'm'])
    @commands.has_role('Team Member')
    async def mapRole(self, ctx, role: discord.Role, reaction: discord.Emoji, *, roleDescription):
        query = f'''INSERT INTO ROLES (emojiID, roleID, roleDescription) VALUES ({reaction.id},{role.id},'{roleDescription}')'''

        self.cursor.execute(query)
        self.conn.commit()

        channel = self.bot.get_channel(config["roles"])
        embed = discord.Embed(
            title="Role",
            description=role.mention,
            color=discord.Colour(3665255))
        embed.add_field(
            name = "Color",
            value = roleDescription,
            inline = False
        )
        message = await channel.send(embed = embed)
        await message.add_reaction(reaction)


def setup(bot):
    bot.add_cog(Roles(bot))
