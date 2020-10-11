## https://discordpy.readthedocs.io/en/latest/ext/commands/commands.html

#import config.json as config

import discord
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed
import json
import random
from config import config
# import tweepy

startup_extensions = ['cogs.tasks']
## Define BOT
bot = commands.Bot(command_prefix=config['prefix']) ## THIS LINE CLASS CLIENT

### TWITTER StreamListener



# class MyStreamListener(tweepy.StreamListener):
#     def on_status(self, status):
#         webhook = DiscordWebhook(url=config['code_webhook'], content = f"{status.text}")
#         webhook.execute()
#
#
# tw_auth = tweepy.OAuthHandler(config['tw_consumer_key'], config['tw_consumer_secret'])
# tw_auth.set_access_token(config['tw_access_key'],config['tw_access_secret'])
#
# tw_api = tweepy.API(tw_auth)
#
# myStreamListener = MyStreamListener()
# myStream = tweepy.Stream(auth = tw_api.auth, listener=myStreamListener)
#
#
# follow_list = ["python_tip","timmay237"]
# follow_id_list = [str(tw_api.get_user(username).id) for username in follow_list]
# myStream.filter(follow=follow_id_list, is_async=True)


################################# BOT STUFF


@bot.event ## <--- DECORATOR. PUT INSIDE OF BOT.
async def on_ready():
    print('Logged in as')
    print(bot.user.name)
    print(bot.user.id)

## Example: $add 3 4 Returns: 7
@bot.command()
async def add(ctx, left:int, right: int): #### INPUT
    await ctx.send(left + right) ### OUTPUT



### Input: $hello
### Output: @brian Hello!
@bot.command()
async def hello(ctx):

    greeting_list = [
    "Hello!",
    "Beware!",
    "Hello Minion.",
    "Look out",
    "is gay."
    ]

    await ctx.send(f"{ctx.author.mention} {random.choice(greeting_list)}!")

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, user:discord.Member):
    """Kicks the specified user from the server"""
    await ctx.guild.kick(user)


## Dice rolling. Start with d6 and make more robust later.
@bot.command()
async def roll(ctx, *args):
    for i in args:
        dice = i.lower().split("d")
        if len(dice) == 2:
            count = int(dice[0])
            sides = int(dice[1])
            amount = 0
            for num in range(count):
                amount += random.randint(1,sides)
            await ctx.send(amount)

## Deletes messages when given a number of messages
@bot.command()
@commands.has_permissions(manage_messages=True)
async def purge(ctx, number):
    mgs = [] #Empty list to put all the messages in the log
    number = int(number) #Converting the amount of messages to delete to an integer
    async for message in ctx.message.channel.history(limit=number):
        mgs.append(message)
    await ctx.message.channel.delete_messages(mgs)



if __name__ == "__main__":
    for extension in startup_extensions:
        try:
            bot.load_extension(extension)
        except Exception as e:
            exc = '{}: {}'.format(type(e).__name__, e)
            print('Failed to load extension {}\n{}'.format(extension, exc))
    bot.run(config['token'])


#####
