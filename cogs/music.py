import discord
from discord.ext import commands
from discord.utils import get
import youtube_dl
import os
import shutil




class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queues = {}

    ##Play Music
    @commands.command(pass_context = True, aliases = ['l','L','LEAVE'])
    async def leave(self, ctx):
        channel = ctx.message.author.voice.channel
        guild = ctx.message.guild
        voice = get(self.bot.voice_clients, guild = ctx.guild)
        if voice and voice.is_connected():
            await voice.disconnect()
        else:
            await ctx.send(self.bot.user.name + f"is not currently connected to {channel}")


    @commands.command(pass_context = True, aliases = ['p', 'P', 'PLAY'])
    async def play(self, ctx, url: str):
        voice = get(self.bot.voice_clients, guild = ctx.guild)
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

    @commands.command(pass_context = True, aliases = ['PAUSE'])
    async def pause(self, ctx):
        voice = get(self.bot.voice_clients, guild = ctx.guild)

        if voice and voice.is_playing():
            voice.pause()
            await ctx.send('Audio Paused')
        else:
            await ctx.send('Music not playing')

    @commands.command(pass_context = True, aliases = ['r', 'res', 'RESUME'])
    async def resume(self, ctx):
        voice = get(self.bot.voice_clients, guild = ctx.guild)

        if voice and voice.is_paused():
            voice.resume()
            await ctx.send('Resume Playing')
        else:
            await ctx.send('Music Not Paused')


    @commands.command(pass_context = True, aliases = ['STOP', 's'])
    async def stop(self, ctx):
        voice = get(self.bot.voice_clients, guild = ctx.guild)

        self.queues.clear()

        if voice and voice.is_playing():
            voice.stop()
            await ctx.send('Audio Stopped')
        else:
            await ctx.send('Audio not playing')




def setup(bot):
    bot.add_cog(Music(bot))
