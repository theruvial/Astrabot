import discord
from discord import app_commands
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
            "gd": "Golden Deer",
        }
        self.wh_dict = {
            "be": config.get("be_webhook"),
            "bl": config.get("bl_webhook"),
            "gd": config.get("gd_webhook"),
        }
        self.channel_dict = {
            "be": "black-eagles",
            "bl": "blue-lions",
            "gd": "golden-deer",
        }

    ## Officer only command to invite a user to their house.
    @app_commands.command(name="assign", description="Invite a user to your house (Officer only)")
    @app_commands.checks.has_role("Officer")
    async def assign(self, interaction: discord.Interaction, user: discord.Member):  #### INPUT
        # see which house the user belongs to
        # this is dumb. I should write a check function, with elif
        author_role_names = [role.name for role in interaction.user.roles]
        if "Golden Deer" in author_role_names:
            house_role = get(interaction.guild.roles, name="Golden Deer")
        elif "Blue Lions" in author_role_names:
            house_role = get(interaction.guild.roles, name="Blue Lions")
        elif "Black Eagles" in author_role_names:
            house_role = get(interaction.guild.roles, name="Black Eagles")
        else:
            await interaction.response.send_message(
                f"You must be a part of a house to invite {user.display_name}!"
            )
            return

        await user.add_roles(house_role)
        await interaction.response.send_message(
            f"Assigned {user.mention} to {house_role.name}!"
        )

    ## Request to join a house. Message waiting for
    ## officer approval sent to faction chat
    @app_commands.command(name="apply", description="Apply to join a house")
    async def apply(
        self, interaction: discord.Interaction, house: str, message: str = ""
    ):  #### INPUT
        ##Check if user is not in a house already
        for applicant_role in self.role_dict.values():
            if applicant_role in [role.name for role in interaction.user.roles]:
                await interaction.response.send_message(
                    f"{interaction.user.mention} is already a part of a house!"
                )
                return

        ##Chceck if user is officer and reaction is checkmark or x
        def check(reaction, user):
            return (
                hasattr(user, "roles")
                and "Officer" in [role.name for role in user.roles]
                and str(reaction.emoji) in ("\u2705", "\u274C")
            )

        if house.lower() in self.wh_dict.keys():
            await interaction.response.send_message(
                f"Application submitted for {self.role_dict[house.lower()]}!",
                ephemeral=True,
            )
            officer_role = get(interaction.guild.roles, name="Officer")
            channel = get(
                interaction.guild.text_channels,
                name=self.channel_dict[house.lower()],
            )
            msg = await channel.send(
                f"{officer_role.mention}! {interaction.user.mention} has applied to join the {self.role_dict[house.lower()]} House! Approve?"
            )
            await msg.add_reaction("\u2705")  # yes
            await msg.add_reaction("\u274C")  # no

            try:
                reaction, user = await self.bot.wait_for(
                    "reaction_add", check=check
                )
            except asyncio.TimeoutError:
                await msg.delete()
            else:
                if str(reaction.emoji) == "\u2705":  ### Yes
                    house_role = get(
                        interaction.guild.roles,
                        name=self.role_dict[house.lower()],
                    )
                    webhook = DiscordWebhook(
                        url=self.wh_dict[house.lower()],
                        content=f"Welcome {interaction.user.mention} to the {self.role_dict[house.lower()]} House!",
                    )
                    webhook.execute()
                    await interaction.user.add_roles(house_role)
                    await msg.delete()
                else:  ### No
                    webhook = DiscordWebhook(
                        url=self.wh_dict[house.lower()],
                        content=f"{interaction.user.mention} has been rejected!",
                    )
                    webhook.execute()
                    await msg.delete()
        else:
            await interaction.response.send_message("Invalid house name.")

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
    @app_commands.command(name="broadcast", description="Broadcast a message to house channels")
    async def broadcast(
        self, interaction: discord.Interaction, house: str, message: str
    ):  #### INPUT
        '''
        Webhook broadcasts a message to house channels.
        House: be, bl, gd, all

        #I JUST TYPED THIS
        !broadcast bl "This a message"

        # THE COMPUTER AUTOMATICALLY DOES THIS
        house = "bl"
        message = "This is a message"
        '''

        if house.lower() == "all":
            for key in self.wh_dict:
                role = get(interaction.guild.roles, name=self.role_dict[key])
                webhook = DiscordWebhook(
                    url=self.wh_dict[key], content=f"{role.mention} {message}"
                )
                webhook.execute()
            await interaction.response.send_message("Broadcast sent to all houses!", ephemeral=True)
        elif house.lower() in self.wh_dict.keys():
            role = get(
                interaction.guild.roles, name=self.role_dict[house.lower()]
            )
            webhook = DiscordWebhook(
                url=self.wh_dict[house.lower()],
                content=f"{role.mention} {message}",
            )
            webhook.execute()
            await interaction.response.send_message("Broadcast sent!", ephemeral=True)
        else:
            await interaction.response.send_message("Invalid house")


async def setup(bot):
    await bot.add_cog(House(bot))
