import discord
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed
import asyncio

import random
import time

class Gambling(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def check_valid_bucks(self, discord_id: int, bucks: int = 1) -> bool:
        sql = f'''
            select max(ds) from exp
            where discord_id  = {discord_id}
            and type = 'msg'
            Limit 1
            '''
        last_exp_ds = await self.bot.pg_con.fetchval(sql)
        return True

    ## add or remove bucks
    async def add_bucks(self,member, points: int, type_value: str = 'generic') -> None:
        ds  = int(time.time())
        pts = int(points)
        sql_bucks = f'''INSERT INTO bucks(ds,discord_id,bucks,type)
                  VALUES({ds},{member.id},{pts},'{type_value}')              
                   '''
        await self.bot.pg_con.execute(sql_bucks)

    @commands.command(pass_context = True, aliases = ['slot'])
    async def slots(self, ctx, bet: int = 5):
        valid_bucks = await self.check_valid_bucks(ctx.author.id, bet)
        if not valid_bucks:
            await ctx.send(f"You can't afford that!")
        else:
            await self.add_bucks(ctx.author,bet * -1, type_value = 'slots')
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
                payout = symbols_dict[list(set_result)[0]] * bet
                await self.add_bucks(ctx.author, payout, type_value='slots')
                payout_str = f"You won {payout}!"
            elif len(set_result) == 0:
                payout = symbols_dict[':question:'] * bet
                await self.add_bucks(ctx.author, payout, type_value='slots')
                payout_str = f"THE JACKPOT {payout}!!!!!!!!"
            else:
                payout_str = f"Tough luck."
            flattened_result = " ".join(spin_result)
            await ctx.send(f"{flattened_result}\n {payout_str}")

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
