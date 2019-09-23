## https://discordpy.readthedocs.io/en/latest/ext/commands/commands.html

#import config.json as config

import discord
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed
import json
import random
from config import config
import tweepy
import youtube_dl
import os


startup_extensions = ['cogs.house']
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
    print('Bot logged in as:' + bot.user.name + "\n")

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


##Play Music

@bot.command(pass_context = True, aliases = ['s','S','STOP'])
async def stop(ctx):
    channel = ctx.message.author.voice.channel
    voice = get(bot.voice_clients, guild = ctx.guild)
    if voice and voice.is_connected():
        await voice.disconnect()
    else:
        await ctx.send(bot.user.name + f"is not currently connected to {channel}")

@bot.command(pass_context = True, aliases = ['p', 'P', 'PLAY'])
async def play(ctx, url: str):
    voice = get(bot.voice_clients, guild = ctx.guild)
    channel = ctx.message.author.voice.channel

    if voice and voice.is_connected():
        await voice.move_to(channel)
    else:
        voice = await channel.connect()

    song_there = os.path.isfile('song.mp3')
    try:
        if song_there:
            os.remove('song.mp3')
            print('Removed old song file')
    except PermissionError:
        print('Cannot remove song file because it is in use')
        await ctx.send('ERROR: Music Currently playing')
        return
    await ctx.send('One Moment Please')

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec':'mp3',
            'preferredquality':'192',
        }],
    }

    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        print('Downloading audio now\n')
        ydl.download([url])

    for file in os.listdir('./'):
        if file.endswith('.mp3'):
            name = file
            print(f"Renamed File: {file}\n")
            os.rename(file, 'song.mp3')

    voice.play(discord.FFmpegPCMAudio('song.mp3'), after = lambda e: print(f"{name} has finished playing"))
    voice.source = discord.PCMVolumeTransformer(voice.source)
    voice.source.volume = 0.5

    nname = name.rsplit('-', 2)
    await ctx.send(f"Playing: {nname}")
    print('Playing Music\n')


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





if __name__ == "__main__":
    for extension in startup_extensions:
        try:
            bot.load_extension(extension)
        except Exception as e:
            exc = '{}: {}'.format(type(e).__name__, e)
            print('Failed to load extension {}\n{}'.format(extension, exc))
    bot.run(config['token'])


#####
