import discord
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed
import asyncio

import random
from config import config

class House(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        ##bundle this into one dict later
        self.role_dict = {
                    "be": "Black Eagles",
                    "bl": "Blue Lions",
                    "gd": "Golden Deer"
                }
        self.wh_dict = {
                "be": config['be_webhook'],
                "bl": config['bl_webhook'],
                "gd": config['gd_webhook']
                }
        self.channel_dict = {
                "be": "black-eagles",
                "bl": "blue-lions",
                "gd": "golden-deer"
                }

    ## Officer only command to invite a user to their house.
    @commands.command()
    @commands.has_role('Officer')
    async def assign(self, ctx, user:discord.Member): #### INPUT
        #see which house the user belongs to
        #this is dumb. I should write a check function, with elif

        if "Golden Deer" in [role.name for role in ctx.author.roles]:
            house_role = get(ctx.author.guild.roles, name = "Golden Deer")
        elif "Blue Lions"  in [role.name for role in ctx.author.roles]:
            house_role = get(ctx.author.guild.roles, name = "Blue Lions")
        elif "Black Eagles" in [role.name for role in ctx.author.roles]:
            house_role = get(ctx.author.guild.roles, name = "Black Eagles")
        else:
            await ctx.send(f"You must be a part of a house to invite {user.nick}!")
            return

        await user.add_roles(house_role)


    ## Request to join a house. Message waiting for
    ## officer approval sent to faction chat
    @commands.command()
    async def apply(self, ctx, house, message=""): #### INPUT
    ##Check if user is not in a house already
        for applicant_role in self.role_dict.values():
            if applicant_role in [role.name for role in ctx.author.roles]:
                await ctx.send(f"{ctx.author.mention} is already a part of a house!")
                return
        ##Chceck if user is officer and reaction is checkmark or x
        def check(reaction, user):
            return "Officer" in [role.name for role in user.roles] and str(reaction.emoji) in (u"\u2705", u"\u274C")

        if house.lower() in self.wh_dict.keys():
            officer_role = get(ctx.author.guild.roles, name = "Officer")
            channel = get(ctx.author.guild.text_channels, name = self.channel_dict[house.lower()])
            msg = await channel.send(f"{officer_role.mention}! {ctx.author.mention} has applied to join the {self.role_dict[house.lower()]} House! Approve?")
            await msg.add_reaction(u"\u2705") #yes
            await msg.add_reaction(u"\u274C") #no

            try:
                reaction, user = await self.bot.wait_for('reaction_add', check=check)
            except asyncio.TimeoutError:
                await msg.delete()
            else:
                if str(reaction.emoji) == u"\u2705": ### Yes
                    house_role = get(ctx.author.guild.roles, name = self.role_dict[house.lower()])
                    webhook = DiscordWebhook(url=self.wh_dict[house.lower()], content = f"Welcome {ctx.author.mention} to the {self.role_dict[house.lower()]} House!")
                    webhook.execute()
                    await ctx.author.add_roles(house_role)
                    await msg.delete()
                else: ### No
                    webhook = DiscordWebhook(url=self.wh_dict[house.lower()], content = f"{ctx.author.mention} has been rejected!")
                    webhook.execute()
                    await msg.delete()

        else:
            await ctx.send("Invalid house name.")

    # @bot.command()
    # async def house(ctx):
    #     house_list = [
    #     "Black Eagles",
    #     "Blue Lions",
    #     "Golden Deer"
    #     ]
    #
    #     for house in house_list:
    #         if house in [role.name for role in ctx.author.roles]:
    #             await ctx.send(f"{ctx.author.mention} is already a part of {house}!")
    #             return
    #
    #     random_house = random.choice(house_list)
    #     role = get(ctx.author.guild.roles, name = random_house)
    #     await ctx.author.add_roles(role)
    #     await ctx.send(f"{ctx.author.mention} is now a part of {random_house}")
    #

    ## Broadcast to houses. Need officer rank.
    @commands.command()
    async def broadcast(self, ctx, hwouse, message): #### INPUT
        '''
        Webhook broadcasts a message to house channels.
        House: be, bl, gd, all

        #I JUST TYPED THIS
        !broadcast bl "This a message"

        # THE COMPUTER AUTOMATICALLY DOES THIS
        house = "bl"
        message = "This is a message"
        '''



        if house.lower() == 'all':
            for key in dict:
                role = get(ctx.author.guild.roles, name = self.role_dict[key])
                webhook = DiscordWebhook(url=self.wh_dict[key], content = f"{role.mention} {message}")
                webhook.execute()
        elif house.lower() in self.wh_dict.keys():
            role = get(ctx.author.guild.roles, name = self.role_dict[house.lower()])
            webhook = DiscordWebhook(url=self.wh_dict[house.lower()], content = f"{role.mention} {message}")
            webhook.execute()
        else:
            await ctx.send("Invalid house")

    # @bot.command()
    # @commands.has_permissions(kick_members=True)
    # async def kick(ctx, user:discord.Member):
    #     """Kicks the specified user from the server"""
    #     await ctx.guild.kick(user)
def setup(bot):
    bot.add_cog(House(bot))
