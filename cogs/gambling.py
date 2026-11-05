import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed
import asyncio
import random
import time


class Gambling(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def check_valid_bucks(self, discord_id: int, bet: int = 1) -> bool:
        if bet < 0:
            print("bet less than 0")
            return False
        else:
            cursor = self.bot.db.cursor()
            cursor.execute(
                "SELECT COALESCE(SUM(bucks), 0) FROM bucks WHERE discord_id = ?",
                (discord_id,),
            )
            queryResult = cursor.fetchone()[0]
            return queryResult - bet >= 0

    ## add or remove bucks
    async def add_bucks(self, member, points: int, type_value: str = "generic") -> None:
        # print("test")
        ds = int(time.time())
        pts = int(points)
        cursor = self.bot.db.cursor()
        cursor.execute(
            "INSERT INTO bucks (ds, discord_id, bucks, type) VALUES (?, ?, ?, ?)",
            (ds, member.id, pts, type_value),
        )
        self.bot.db.commit()

    async def add_to_jackpot(self, discord_id: int, amount: int) -> None:
        # print("adding to jackpot")
        ds = int(time.time())
        cursor = self.bot.db.cursor()
        cursor.execute(
            "INSERT INTO jackpot (ds, discord_id, bucks, type) VALUES (?, ?, ?, 'addition')",
            (ds, discord_id, amount),
        )
        self.bot.db.commit()

    async def win_jackpot(self, discord_id: int):
        cursor = self.bot.db.cursor()
        cursor.execute("SELECT COALESCE(SUM(bucks), 0) FROM jackpot")
        balance = cursor.fetchone()[0]
        return balance / 2

    async def clear_jackpot(self, discord_id: int, bucks: int):
        ds = int(time.time())
        cursor = self.bot.db.cursor()
        cursor.execute(
            "INSERT INTO jackpot (ds, discord_id, bucks, type) VALUES (?, ?, ?, 'payout')",
            (ds, discord_id, int(bucks * -1)),
        )
        self.bot.db.commit()

    @app_commands.command(name="giftbucks", description="Gift Astrabucks to another member")
    async def giftBucks(
        self, interaction: discord.Interaction, member: discord.Member, amount: int
    ):
        valid_bucks = await self.check_valid_bucks(interaction.user.id, amount)
        if amount < 0:
            await interaction.response.send_message("You cannot gift a negative amount!")
        else:
            if not valid_bucks:
                await interaction.response.send_message("You can't afford that!")
            else:
                await self.add_bucks(interaction.user, -amount, type_value="gift")
                await self.add_bucks(member, amount, type_value="gift")
                await interaction.response.send_message(
                    f"{interaction.user.mention} has gifted {amount} to {member.mention}!!"
                )

    @app_commands.command(name="jackpot", description="Check the current available jackpot")
    async def checkCurrentAvailableJackpot(self, interaction: discord.Interaction):
        cursor = self.bot.db.cursor()
        cursor.execute("SELECT COALESCE(SUM(bucks), 0) FROM jackpot")
        balance = cursor.fetchone()[0]
        await interaction.response.send_message(
            f"Current jackpot is valued at {balance / 2}!"
        )

    @app_commands.command(name="pool", description="Check the total jackpot pool")
    async def checkTotalJackpot(self, interaction: discord.Interaction):
        adminRole = discord.utils.get(interaction.guild.roles, name="Admin")
        if adminRole in interaction.user.roles:
            cursor = self.bot.db.cursor()
            cursor.execute("SELECT COALESCE(SUM(bucks), 0) FROM jackpot")
            balance = cursor.fetchone()[0]
            await interaction.response.send_message(
                f"Current total pool is valued at {balance}!"
            )
        else:
            await interaction.response.send_message(
                "You do not have permission to use this command!", ephemeral=True
            )

    @app_commands.command(name="slots", description="Play the slot machine")
    async def slots(self, interaction: discord.Interaction, bet: int = 5):
        valid_bucks = await self.check_valid_bucks(interaction.user.id, bet)
        if bet < 0:
            await interaction.response.send_message("You cannot bet a negative amount!!")
        else:
            if not valid_bucks:
                await interaction.response.send_message("You can't afford that!")
            else:
                await self.add_bucks(interaction.user, bet * -1, type_value="slots")
                symbols_dict = {
                    ":banana:": 2,
                    ":strawberry:": 3,
                    ":avocado:": 4,
                    ":watermelon:": 5,
                    ":mango:": 8,
                    ":question:": 50,
                }
                loser_messages = [
                    "Yikes.",
                    f"How much money do you plan on losing today {interaction.user.mention}?",
                    "Tough luck.",
                    "You have a problem.",
                    "Unlucky.",
                    "Unlucky in the ducky",
                    "Unlucky in the trucky.",
                    "Unlucky in the dump trucky.",
                    "Hahahahaha you lose!",
                    "Kathi doesn't like losers.",
                    "Thanks for your donation to Metronome's egirl fund.",
                    "You'll get it next time!",
                    "Don't give up!",
                    "So close!",
                    "Almost!",
                    "https://www.helpguide.org/articles/addictions/gambling-addiction-and-problem-gambling.htm",
                ]

                spin_result = [
                    random.choice(list(symbols_dict.keys())) for _ in range(3)
                ]
                set_result = {*spin_result}
                # print(set_result)  #convert to set
                if ":question:" in set_result:
                    set_result.remove(":question:")
                if len(set_result) == 1:
                    payout = symbols_dict[list(set_result)[0]] * bet
                    await self.add_bucks(interaction.user, payout, type_value="slots")
                    winner_messages = [
                        f"How long did it take you to win this {payout} {interaction.user.mention}?",
                        f"Great job {interaction.user.mention} you won {payout}!!!!!",
                        f"You've successfully hacked Metronome's bank account {interaction.user.mention}, you've come away with ${payout}",
                        f"{interaction.user.mention} you won {payout}!!!!",
                        f"You've taken {payout} from Metronome's E-girl fund {interaction.user.mention}, good job!",
                    ]
                    payout_str = random.choice(winner_messages)
                elif len(set_result) == 0:
                    payout = await self.win_jackpot(interaction.user.id)
                    await self.clear_jackpot(interaction.user.id, int(payout))
                    payout += symbols_dict[":question:"] * bet
                    await self.add_bucks(interaction.user, int(payout), type_value="slots")
                    payout_str = f"{interaction.user.mention} WON THE JACKPOT {payout}!!!!!!!!"
                else:
                    await self.add_to_jackpot(interaction.user.id, bet)
                    payout_str = random.choice(loser_messages)
                flattened_result = " ".join(spin_result)
                await interaction.response.send_message(f"{flattened_result}\n {payout_str}")

    # def blackjack(bet: int = 1) -> int:
    #     suits = ["a","b","c","d"] # replace with emojis of suits
    #     you =  [deck.pop(), deck.pop()]

    #     def evaluate(hand) -> int:
    #         ## if in values[:] return 10
    #         ## else return index number
    #         ## special case for A
    #         return hand


async def setup(bot):
    await bot.add_cog(Gambling(bot))
