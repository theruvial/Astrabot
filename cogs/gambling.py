import discord
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed
import asyncio

import random
from config import config

class Gambling(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # def check_valid_bucks(bucks: int) -> bool:
    #     ## runs a query to see
    #     return True

    @commands.command(pass_context = True, aliases = ['slot'])
    async def slots(self, ctx, bet: int = 5):
    # def slots(self, bet: int = 1) -> int:
        ## replace with emojis of fruits or astra stuff
        symbols_dict = {
            ":banana:": 4,
            ":strawberry:": 4,
            ":avocado:": 5,
            ":mango:": 10,
            ":question:": 100,
        }
        spin_result = [random.choice(list(symbols_dict.keys())) for _ in range(3)]
        set_result = {*spin_result} #convert to set
        if ":question:" in set_result:
            set_result.remove(":question:")
        if len(set_result) == 1:
            payout = f"You won {symbols_dict[list(set_result)[0]] * bet}!"
        elif len(set_result) == 0:
            payout = f"THE JACKPOT {symbols_dict[':question:'] * bet}!!!!!!!!"
        else:
            payout = f"Tough luck."
        flattened_result = " ".join(spin_result)
        await ctx.send(f"{flattened_result}\n {payout}")

    # def blackjack(bet: int = 1) -> int:
    #     suits = ["a","b","c","d"] # replace with emojis of suits
    #     values = ["A","K"] # replace with emojis of values
    #     deck = [suit + value for suit in suits for value in values]
    #     random.shuffle(deck)

    #     dealer = [deck.pop(), deck.pop()]
    #     you =  [deck.pop(), deck.pop()]

    #     def evaluate(hand) -> int:
    #         ## if in values[:] return 10
    #         ## else return index number
    #         ## special case for A
    #         return hand
def setup(bot):
    bot.add_cog(Gambling(bot))
