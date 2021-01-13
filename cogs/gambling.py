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

    async def check_valid_bucks(self, discord_id: int, bet: int = 1) -> bool:
        if bet < 0:
            print("bet less than 0")
            return
        else:
            sql = f'''
                select sum(bucks) from bucks
                where discord_id  = {discord_id}                        
                '''
            queryResult = await self.bot.pg_con.fetchval(sql)
            return queryResult - bet >= 0

    ## add or remove bucks
    async def add_bucks(self, member, points: int, type_value: str = 'generic') -> None:
        # print("test")
        ds = int(time.time())
        pts = int(points)
        sql_bucks = f'''INSERT INTO bucks(ds,discord_id,bucks,type)
                  VALUES({ds},{member.id},{pts},'{type_value}')              
                   '''
        await self.bot.pg_con.execute(sql_bucks)

    async def add_to_jackpot(self, discord_id:int, amount:int) -> int:
        # print("adding to jackpot")
        ds = int(time.time())
        add_to_jackpot_sql = f'''
            INSERT INTO jackpot(ds,discord_id,bucks, type)
            VALUES ({ds}, {discord_id}, {amount}, 'addition')            
            '''

        await self.bot.pg_con.execute(add_to_jackpot_sql)

    async def win_jackpot(self, discord_id:int):
        jackpot_balance_sql = "Select sum(bucks) from jackpot"
        balance = await self.bot.pg_con.fetchval(jackpot_balance_sql)
        return balance /2

    async def clear_jackpot(self, discord_id:int, bucks: int):
        ds = int(time.time())
        clear_jackpot_sql = f'''
            INSERT INTO jackpot(ds,discord_id,bucks, type)
            VALUES ({ds}, {discord_id}, {bucks*-1}, 'payout')            
            '''
        await self.bot.pg_con.execute(clear_jackpot_sql)


    @commands.command(pass_context= True, aliases=['g', 'gift'])
    async def giftBucks(self, ctx, member: discord.Member, amount: int):
        valid_bucks = await self.check_valid_bucks(ctx.author.id, amount)
        if amount < 0:
            await ctx.send("You cannot gift a negative amount!")
        else:
            if not valid_bucks:
                await ctx.send("You can't afford that!")
            else:                
                await self.add_bucks(ctx.author, -amount, type_value='gift')                
                await self.add_bucks(member, amount, type_value='gift')
                await ctx.send(f"{ctx.author.mention} has gifted {amount} to {member.mention}!!")


    @commands.command(pass_context=True, aliases=['jackpot', 'j'])
    async def checkCurrentAvailableJackpot(self, ctx):
        jackpot_sql = '''Select sum(bucks) from jackpot'''
        balance = await self.bot.pg_con.fetchval(jackpot_sql)
        await ctx.send(f"Current jackpot is valued at {balance/2}!")

    @commands.command(pass_context=True, aliases=['pool', 'p'])
    async def checkTotalJackpot(self, ctx):
        adminRole = discord.utils.get(ctx.guild.roles, name="Admin")
        if adminRole in ctx.author.roles:
            jackpot_sql = '''Select sum(bucks) from jackpot'''
            balance = await self.bot.pg_con.fetchval(jackpot_sql)
            await ctx.send(f"Current total pool is valued at {balance}!")
        else:
            msg = await ctx.send("You do not have permission to use this command!")
            await asyncio.sleep(3)
            await msg.delete()

    @commands.command(pass_context=True, aliases=['slot'])
    async def slots(self, ctx, bet: int = 5):
        valid_bucks = await self.check_valid_bucks(ctx.author.id, bet)
        if bet < 0:
            await ctx.send("You cannot bet a negative amount!!")
        else:
            if not valid_bucks:
                await ctx.send(f"You can't afford that!")
            else:
                await self.add_bucks(ctx.author, bet * -1, type_value='slots')
                symbols_dict = {
                    ":banana:": 2,
                    ":strawberry:": 3,
                    ":avocado:": 4,
                    ":watermelon:": 5,
                    ":mango:": 8,
                    ":question:": 50,
                }
                loser_messages = [
                    f"Yikes.",
                    f"How much money do you plan on losing today {ctx.author.mention}?",
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
                    "https://www.helpguide.org/articles/addictions/gambling-addiction-and-problem-gambling.htm"
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
                    await self.add_bucks(ctx.author, payout, type_value='slots')
                    winner_messages = [
                        f"How long did it take you to win this {payout} {ctx.author.mention}?",
                        f"Great job {ctx.author.mention} you won {payout}!!!!!",
                        f"You've successfully hacked Metronome's bank account {ctx.author.mention}, you've come away with ${payout}",
                        f"{ctx.author.mention} you won {payout}!!!!",
                        f"You've taken {payout} from Metronome's E-girl fund {ctx.author.mention}, good job!"
                    ]
                    payout_str = random.choice(winner_messages)
                elif len(set_result) == 0:
                    payout = self.win_jackpot(ctx.discord_id)
                    self.clear_jackpot(ctx.author.id, payout)
                    payout += symbols_dict[":question:"] * bet
                    await self.add_bucks(ctx.author, payout, type_value='slots')
                    payout_str = f"{ctx.author.mention} WON THE JACKPOT {payout}!!!!!!!!"
                else:
                    await self.add_to_jackpot(ctx.author.id,bet)
                    payout_str = random.choice(loser_messages)
                flattened_result = " ".join(spin_result)
                await ctx.send(f"{flattened_result}\n {payout_str}")

    # def blackjack(bet: int = 1) -> int:
    #     suits = ["a","b","c","d"] # replace with emojis of suits
    #     you =  [deck.pop(), deck.pop()]


    #     def evaluate(hand) -> int:
    #         ## if in values[:] return 10
    #         ## else return index number
    #         ## special case for A
    #         return hand
def setup(bot):
    bot.add_cog(Gambling(bot))
