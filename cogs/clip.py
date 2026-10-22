import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import get
import youtube_dl
import os.path
from pydub import AudioSegment
from pathlib import Path


class Clip(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def colonSplit(self, timestamp_start, timestamp_end):
        if ":" in timestamp_start:
            minute_sec = timestamp_start.split(":")
            min_val = int(minute_sec[0]) * 60
            sec = int(minute_sec[1])
            start = min_val + sec
        else:
            start = int(timestamp_start)

        if ":" in timestamp_end:
            sec_minute = timestamp_end.split(":")
            min_val = int(sec_minute[0]) * 60
            sec = int(sec_minute[1])
            end = min_val + sec
        else:
            end = int(timestamp_end)

        # Convert from seconds to milliseconds
        start = start * 1000
        end = end * 1000
        return start, end

    @app_commands.command(name="clip", description="Download and play an audio clip from a URL")
    async def clip(
        self,
        interaction: discord.Interaction,
        url: str,
        timestamp_start: str,
        timestamp_end: str,
    ):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "You must be in a voice channel to use this command.", ephemeral=True
            )
            return

        await interaction.response.defer()
        # defines the bots voice client
        voice = get(self.bot.voice_clients, guild=interaction.guild)
        channel = interaction.user.voice.channel
        # connects to the voice channel the command user is in
        if voice and voice.is_connected():
            await voice.move_to(channel)
        else:
            voice = await channel.connect()

        # checks to see if an audio file is present
        song_there = os.path.isfile("audio.wav")
        try:
            if song_there:
                os.remove("audio.wav")
                print("Removed old song file")
        except PermissionError:
            print("Cannot remove song file because it is in use")
            await interaction.followup.send("ERROR: Clip Currently playing")
            return

        # downloads and extracts audio from a url based on these options
        ydl_opts = {
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "wav",
                    "preferredquality": "192",
                }
            ],
        }

        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            print("Trying to download clip now...\n")
            try:
                ydl.download([url])
            except youtube_dl.DownloadError:
                await interaction.followup.send("Please enter a valid URL.")
                return
            await interaction.followup.send("One moment please...")

        # renames audio file to song.wav
        for file in os.listdir("./"):
            if file.endswith(".wav"):
                print(f"Renamed File: {file}\n")
                os.rename(file, "audio.wav")

        # splits audio file at requested timestamps
        audio_file = "audio.wav"
        audio = AudioSegment.from_wav(audio_file)
        start, end = self.colonSplit(timestamp_start, timestamp_end)

        print("split at [{}:{}] ms".format(start, end))
        audio_processed = audio[start:end]
        audio_processed.export("audio1.wav", format="wav")

        # plays audio file after it has been split
        await interaction.followup.send("Playing clip.")
        voice.play(
            discord.FFmpegPCMAudio("audio1.wav"),
            after=lambda e: os.remove("audio1.wav"),
        )
        voice.source = discord.PCMVolumeTransformer(voice.source)
        voice.source.volume = 0.5

    @app_commands.command(name="sclip", description="Save an audio clip from a URL for later playback")
    async def sclip(
        self,
        interaction: discord.Interaction,
        url: str,
        timestamp_start: str,
        timestamp_end: str,
        name: str,
    ):
        save_path = f"./Saved/{name}.wav"
        print(save_path)
        if os.path.isfile(save_path):
            print("File already exists.")
            await interaction.response.send_message(
                f"Error in filename: {name} is already in use, please select a different file name."
            )
            return

        await interaction.response.defer()
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": save_path,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "wav",
                    "preferredquality": "192",
                }
            ],
        }

        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            print("Trying to download clip now...\n")
            try:
                ydl.download([url])
            except youtube_dl.DownloadError:
                await interaction.followup.send("Please enter a valid URL.")
                return
            await interaction.followup.send("One moment please...")

        audio = AudioSegment.from_file(os.path.abspath(save_path))
        start, end = self.colonSplit(timestamp_start, timestamp_end)

        print("split at [{}:{}] ms".format(start, end))
        audio_processed = audio[start:end]
        audio_processed.export(save_path)
        await interaction.followup.send(
            f"Clip saved successfully as {name}. Please use command /cplay {name} to play it back."
        )
        print("File saved successfully.")

    @app_commands.command(name="cplay", description="Play a saved audio clip")
    async def cplay(self, interaction: discord.Interaction, name: str):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "You must be in a voice channel to use this command.", ephemeral=True
            )
            return

        # defines the bots voice client
        voice = get(self.bot.voice_clients, guild=interaction.guild)
        channel = interaction.user.voice.channel
        # connects to the voice channel the command user is in
        if voice and voice.is_connected():
            await voice.move_to(channel)
        else:
            voice = await channel.connect()
        audio_file = f"./Saved/{name}.wav"
        voice.play(
            discord.FFmpegPCMAudio(audio_file),
            after=lambda e: print("Clip Played"),
        )
        voice.source = discord.PCMVolumeTransformer(voice.source)
        voice.source.volume = 0.5
        await interaction.response.send_message("Clip Played")


async def setup(bot):
    await bot.add_cog(Clip(bot))
