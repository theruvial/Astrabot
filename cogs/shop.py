import discord
from discord.ext import commands
from discord.utils import get
import asyncio
import time
import random
from config import config

class Shop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # @commands.command(pass_context=True, aliases=["add"])
    # async def addShopItem(self, ctx, value: int, name, *, description):
    #     channel = self.bot.get_channel(config["botShop"])
    #     embed = discord.Embed(title=name,
    #                           description="".join(description),
    #                           color=discord.Colour(3665255))
    #     embed.add_field(name="Cost", value=f"{value}", inline=False)
    #     message = await channel.send(embed=embed)
    #     await message.add_reaction(u"\U0001F4B5")

    async def check_valid_bucks(self, discord_id: int, bet: int = 1) -> bool:
        sql = f'''
            select sum(bucks) from bucks
            where discord_id  = {discord_id}                        
            '''
        queryResult = await self.bot.pg_con.fetchval(sql)
        return queryResult - bet >= 0

    async def changeBucks(self, discord_id: int, value: int):
        ds= int(time.time())
        sql = f'''Insert into bucks(ds, discord_id, bucks, type)
        Values({ds}, {discord_id}, {value}, 'shop')
        '''
        await self.bot.pg_con.execute(sql)


    # Does an interesting thing for each premium role
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        guild = self.bot.get_channel(config["botShop"]).guild
        channel = message.channel
        workChannel = guild.get_channel(config["workChannel"])        
        if message.channel in await guild.fetch_channels():
            member = message.author
            memberRoles = member.roles
            if message.channel != workChannel:
                for role in memberRoles:
                    if role.name == "Raging Barbarian":
                        if message.content.isupper():
                            return
                        else:
                            await message.delete()
                            msg = await channel.send("SORRY. REAL BARBARIANS TALK IN ALL CAPS.")
                            await asyncio.sleep(2)
                            await msg.delete()
                            ## delete their message and warn them to use uppers
                    elif role.name == "Forest Nymph":
                        flowerList = [
                            u"\U0001F33A", u"\U0001F338", u"\U0001F337", u"\U0001F339",
                            u"\U0001F33B", u"\U0001F33C"
                        ]
                        await message.add_reaction(random.choice(flowerList))

                        # react with flowers

                    elif role.name == "White Knight":
                        substring = "m'lady"
                        if substring in message.content:
                            msg = await channel.send(f"{member.mention} tips their fedora.")
                            await asyncio.sleep(2)
                            await msg.delete()

                        # if message ends with m'lady
                        # send *user tips fedora*






    @commands.command(pass_context=True, aliases=['addRole', 'shopRole'])
    async def addPaidRole(self, ctx, value: int, role: discord.Role, *, description):
        adminRole = discord.utils.get(ctx.guild.roles, name="Admin")
        if adminRole in ctx.author.roles:
            channel = self.bot.get_channel(config["botShop"])
            embed = discord.Embed(title="Role",
                                description=role.mention,
                                color=discord.Colour(3665255))
            embed.add_field(name="Role Description",
                            value=f"{description}",
                            inline=False)
            embed.add_field(name="Cost", value=f"{value}", inline=False)
            message = await channel.send(embed=embed)
            await message.add_reaction(u"\U0001F4B5")
        else:
            msg = await ctx.send("You do not have permission to use this command!")
            await asyncio.sleep(3)
            await msg.delete()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        user = self.bot.get_user(payload.user_id)
        if (payload.channel_id != config["botShop"] or user == self.bot.user):
            return
        channel = self.bot.get_channel(config["botShop"])
        guild = self.bot.get_channel(config["botShop"]).guild
        member = guild.get_member(payload.user_id)
        message = await channel.fetch_message(payload.message_id)
        fetchedEmbed = message.embeds[0]
        if(fetchedEmbed.title == "Role"):
            roleStr = fetchedEmbed.description
            roleID = int(roleStr.split("&")[1].split(">")[0])
            role = guild.get_role(roleID)
            itemValue = int(fetchedEmbed.fields[1].value)
            valid_bucks = await self.check_valid_bucks(payload.user_id, itemValue)
            if not valid_bucks:
                msg = await channel.send("You can't afford that!")
                await message.remove_reaction(payload.emoji, user)
                await asyncio.sleep(2)
                await msg.delete()
            else:
                if not role in member.roles:
                    await self.changeBucks(payload.user_id, -itemValue)
                    await member.add_roles(role)

                else:
                    msg = await channel.send("You already have this role!")
                    await message.remove_reaction(payload.emoji, user)
                    await asyncio.sleep(2)
                    await msg.delete()


    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        user = self.bot.get_user(payload.user_id)
        if (payload.channel_id != config["botShop"] or user == self.bot.user):
            return
        channel = self.bot.get_channel(config["botShop"])
        guild = self.bot.get_channel(config["botShop"]).guild
        member = guild.get_member(payload.user_id)
        message = await channel.fetch_message(payload.message_id)
        fetchedEmbed = message.embeds[0]
        if(fetchedEmbed.title == "Role"):
            roleStr = fetchedEmbed.description
            roleID = int(roleStr.split("&")[1].split(">")[0])
            role = guild.get_role(roleID)
            itemValue = int(fetchedEmbed.fields[1].value)
            if role in member.roles:
                await self.changeBucks(payload.user_id, itemValue)
                await member.remove_roles(role)
                dmChannel = await member.create_dm()
                await dmChannel.send(f"You've been refunded {itemValue} astrabucks, and the role {role.name} has been removed")





def setup(bot):
    bot.add_cog(Shop(bot))