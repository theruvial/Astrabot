import asyncio
import os
import re
from typing import List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import get
import yt_dlp

from config import config
from cogs.music import (
    FFMPEG_EXECUTABLE,
    SPOTIFY_URL_REGEX,
    YTDL_STREAM_OPTS,
    Music,
    TaskProgress,
    fmt_secs,
    log,
    search_across_platforms_sync,
    ytdl_extract_sync,
)

SAVED_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Saved")
CLIP_EXTENSIONS = (".mp3", ".wav")
NAME_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RECONNECT_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FALLBACK_CHANNEL_NAME = "saved-clips"


def parse_timestamp(text: str) -> float:
    """Parses '83', '83.5', '1:23' or '1:02:03' into seconds."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"'{text}' is not a valid timestamp")
    seconds = 0.0
    for part in parts:
        try:
            value = float(part)
        except ValueError:
            raise ValueError(f"'{text}' is not a valid timestamp")
        if value < 0:
            raise ValueError(f"'{text}' is not a valid timestamp")
        seconds = seconds * 60 + value
    return seconds


def fmt_timestamp(seconds: float) -> str:
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def find_saved_clip(name: str) -> Optional[str]:
    for ext in CLIP_EXTENSIONS:
        path = os.path.join(SAVED_DIR, f"{name}{ext}")
        if os.path.isfile(path):
            return path
    return None


class Clip(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.max_clip_seconds = float(config.get("maxClipSeconds", 300))
        os.makedirs(SAVED_DIR, exist_ok=True)

    # ------------------------------------------------------------------ helpers

    def _validate_range(self, start_text: str, end_text: str) -> Tuple[float, float, float]:
        start = parse_timestamp(start_text)
        end = parse_timestamp(end_text)
        if end <= start:
            raise ValueError("The end time must be after the start time.")
        duration = end - start
        if duration > self.max_clip_seconds:
            raise ValueError(
                f"Clips can be at most {fmt_timestamp(self.max_clip_seconds)} long "
                f"(you asked for {fmt_timestamp(duration)})."
            )
        return start, end, duration

    @staticmethod
    def _is_url(text: str) -> bool:
        return text.strip().lower().startswith(("http://", "https://"))

    async def _resolve_stream(
        self, query: str, target_duration: Optional[int] = None
    ) -> dict:
        """Looks up a direct audio stream URL (no download) for a link or search term,
        falling back across YouTube, YouTube Music, and SoundCloud."""
        query = query.strip()
        loop = asyncio.get_running_loop()

        def extract():
            if self._is_url(query):
                info = ytdl_extract_sync(query, YTDL_STREAM_OPTS)
                if info and "entries" in info:
                    entries = [e for e in info["entries"] if e]
                    info = entries[0] if entries else None
                return info
            info, provider = search_across_platforms_sync(
                query, preferred="youtube", target_duration=target_duration
            )
            info["matched_provider"] = provider
            return info

        info = await loop.run_in_executor(None, extract)
        if not info or not info.get("url"):
            raise ValueError(f"Could not find playable audio for `{query}`.")
        return info

    def _resolve_keys(self, url: str) -> List[str]:
        """Progress stages that _resolve_source will go through for this input."""
        if SPOTIFY_URL_REGEX.search(url):
            return ["resolve_spotify", "search"]
        return ["resolve_url" if self._is_url(url) else "search"]

    async def _resolve_source(
        self, url: str, requester_id: int, progress: TaskProgress
    ) -> dict:
        """Resolves a link or search term to a streamable source. Spotify tracks are
        matched to the same song on YouTube, which is assumed to be the same length."""
        match = SPOTIFY_URL_REGEX.search(url)
        if not match:
            key = "resolve_url" if self._is_url(url) else "search"
            async with progress.stage(key, f"'{url}'"):
                return await self._resolve_stream(url)

        if match.group(1) != "track":
            raise ValueError(
                "Only single Spotify track links can be clipped, not albums or playlists."
            )

        music = self.bot.get_cog("Music") or Music(self.bot)
        async with progress.stage("resolve_spotify", f"'{url}'"):
            tracks = await music._resolve_spotify_url(url, requester_id)
        track = tracks[0]

        async with progress.stage("search", f"Match for '{track.title}'"):
            info = await self._resolve_stream(track.title, target_duration=track.duration)

        matched_provider = info.get("matched_provider", "YouTube")
        info["youtube_title"] = info.get("title")
        info["title"] = track.title
        info["spotify_duration"] = track.duration
        yt_duration = info.get("duration")
        if track.duration and yt_duration and abs(track.duration - yt_duration) > 10:
            info["note"] = (
                f"\nHeads up: the {matched_provider} match is {fmt_timestamp(yt_duration)} long but the "
                f"Spotify track is {fmt_timestamp(track.duration)}, so your timestamps may be off."
            )
        else:
            info["note"] = f"\nMatched on {matched_provider}: <{info.get('webpage_url')}>"
        log(f"#{progress.id} matched Spotify '{track.title}' -> {matched_provider} '{info['youtube_title']}'")
        return info

    @staticmethod
    def _check_bounds(info: dict, end_s: float) -> None:
        total = info.get("spotify_duration") or info.get("duration")
        if total and end_s > total + 1:
            raise ValueError(
                f"The track is only {fmt_timestamp(total)} long, but the clip ends at "
                f"{fmt_timestamp(end_s)}."
            )

    @staticmethod
    def _input_options(stream_url: str, start: float) -> str:
        reconnect = f"{RECONNECT_OPTIONS} " if stream_url.startswith("http") else ""
        return f"{reconnect}-ss {start}"

    async def _cut_to_file(
        self, stream_url: str, start: float, duration: float, out_path: str
    ) -> None:
        """Streams just the requested section through FFmpeg into an mp3 file."""
        cmd = [FFMPEG_EXECUTABLE, "-y", "-hide_banner", "-loglevel", "error"]
        if stream_url.startswith("http"):
            cmd += RECONNECT_OPTIONS.split()
        cmd += [
            "-ss", str(start),
            "-i", stream_url,
            "-t", str(duration),
            "-vn",
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            out_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            self._remove_quietly(out_path)
            raise RuntimeError("FFmpeg timed out while cutting the clip.")

        if proc.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            self._remove_quietly(out_path)
            tail = stderr.decode(errors="replace").strip().splitlines()[-1:] or ["unknown error"]
            raise RuntimeError(f"FFmpeg failed: {tail[0]}")

    @staticmethod
    def _remove_quietly(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    def _get_saved_clips_channel(self, guild: discord.Guild) -> Optional[discord.abc.Messageable]:
        channel_id = config.get("savedClipsChannel")
        if channel_id:
            channel = self.bot.get_channel(int(channel_id))
            if channel is not None:
                return channel
            log(f"savedClipsChannel {channel_id} not found, falling back to #{FALLBACK_CHANNEL_NAME}")
        return get(guild.text_channels, name=FALLBACK_CHANNEL_NAME)

    async def _join_voice(
        self, interaction: discord.Interaction, progress: TaskProgress, needs_connect: bool
    ) -> discord.VoiceClient:
        voice: Optional[discord.VoiceClient] = get(
            self.bot.voice_clients, guild=interaction.guild
        )
        channel = interaction.user.voice.channel
        if needs_connect:
            async with progress.stage("connect", f"#{channel.name}"):
                voice = await channel.connect()
        elif voice.channel != channel:
            await voice.move_to(channel)
        return voice

    # ----------------------------------------------------------------- commands

    @app_commands.command(name="clip", description="Stream a section of a track into your voice channel")
    @app_commands.describe(
        url="A link (YouTube, SoundCloud, ...) or a search term",
        start="Start time, e.g. 1:23 or 83",
        end="End time, e.g. 1:45 or 105",
    )
    async def clip(
        self, interaction: discord.Interaction, url: str, start: str, end: str
    ):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "You must be in a voice channel to use this command.", ephemeral=True
            )
            return
        try:
            start_s, end_s, duration = self._validate_range(start, end)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        voice: Optional[discord.VoiceClient] = get(
            self.bot.voice_clients, guild=interaction.guild
        )
        if voice and (voice.is_playing() or voice.is_paused()):
            await interaction.response.send_message(
                "Something is already playing, use /stop first or wait for it to finish.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        needs_connect = not (voice and voice.is_connected())
        progress = TaskProgress(
            "clip",
            self._resolve_keys(url) + (["connect"] if needs_connect else []) + ["playback"],
            context=(
                f"[{interaction.guild.name}] by {interaction.user} -> '{url}' "
                f"{fmt_timestamp(start_s)}-{fmt_timestamp(end_s)} ({fmt_secs(duration)})"
            ),
        )

        try:
            info = await self._resolve_source(url, interaction.user.id, progress)
            self._check_bounds(info, end_s)
            voice = await self._join_voice(interaction, progress, needs_connect)
        except Exception as e:
            progress.finish(False, str(e))
            await interaction.followup.send(f"Could not load clip: {e}")
            return

        title = info.get("title") or url
        log(f"#{progress.id} found: '{title}'")

        async with progress.stage("playback", f"'{title}' {fmt_timestamp(start_s)}-{fmt_timestamp(end_s)}"):
            source = discord.FFmpegPCMAudio(
                info["url"],
                executable=FFMPEG_EXECUTABLE,
                before_options=self._input_options(info["url"], start_s),
                options=f"-vn -t {duration}",
            )
            voice.play(
                discord.PCMVolumeTransformer(source, volume=0.5),
                after=lambda e: log(f"[{interaction.guild.name}] clip finished" + (f" with error: {e}" if e else "")),
            )

        progress.finish(True, f"playing clip of '{title}'")
        await interaction.followup.send(
            f"Playing clip from **{title}** ({fmt_timestamp(start_s)} - {fmt_timestamp(end_s)}).{info.get('note', '')}"
        )

    @app_commands.command(name="sclip", description="Save a clip and post it to the saved-clips channel")
    @app_commands.describe(
        url="A link (YouTube, SoundCloud, ...) or a search term",
        start="Start time, e.g. 1:23 or 83",
        end="End time, e.g. 1:45 or 105",
        name="Name to save the clip under (letters, numbers, - and _)",
    )
    async def sclip(
        self,
        interaction: discord.Interaction,
        url: str,
        start: str,
        end: str,
        name: str,
    ):
        name = name.strip()
        if not NAME_REGEX.match(name):
            await interaction.response.send_message(
                "Clip names may only contain letters, numbers, `-` and `_` (max 64 characters).",
                ephemeral=True,
            )
            return
        if find_saved_clip(name):
            await interaction.response.send_message(
                f"Error in filename: **{name}** is already in use, please select a different file name.",
                ephemeral=True,
            )
            return
        try:
            start_s, end_s, duration = self._validate_range(start, end)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        await interaction.response.defer()

        progress = TaskProgress(
            "sclip",
            self._resolve_keys(url) + ["cut", "upload"],
            context=(
                f"[{interaction.guild.name}] by {interaction.user} -> '{url}' as '{name}' "
                f"{fmt_timestamp(start_s)}-{fmt_timestamp(end_s)} ({fmt_secs(duration)})"
            ),
        )
        out_path = os.path.join(SAVED_DIR, f"{name}.mp3")

        try:
            info = await self._resolve_source(url, interaction.user.id, progress)
            self._check_bounds(info, end_s)
            title = info.get("title") or url
            log(f"#{progress.id} found: '{title}'")
            async with progress.stage("cut", f"{fmt_timestamp(start_s)}-{fmt_timestamp(end_s)} -> {name}.mp3"):
                await self._cut_to_file(info["url"], start_s, duration, out_path)
        except Exception as e:
            progress.finish(False, str(e))
            await interaction.followup.send(f"Could not save clip: {e}")
            return

        size_mb = os.path.getsize(out_path) / (1024 * 1024)
        log(f"#{progress.id} saved {out_path} ({size_mb:.2f} MB)")

        # Post the clip to the saved-clips channel
        upload_note = ""
        channel = self._get_saved_clips_channel(interaction.guild)
        if channel is None:
            upload_note = (
                f"\nNo saved-clips channel found, set `savedClipsChannel` in config.json "
                f"or create a #{FALLBACK_CHANNEL_NAME} channel."
            )
            progress.finish(True, "saved locally, no saved-clips channel")
        elif os.path.getsize(out_path) > interaction.guild.filesize_limit:
            upload_note = (
                f"\nThe clip ({size_mb:.1f} MB) is over this server's upload limit, "
                f"so it was only saved locally."
            )
            progress.finish(True, "saved locally, file too large to upload")
        else:
            try:
                async with progress.stage("upload", f"#{getattr(channel, 'name', channel)} ({size_mb:.2f} MB)"):
                    source_url = info.get("webpage_url")
                    # Wrapping the link in <> stops Discord unfurling a big video preview
                    # above the audio player.
                    source_line = f"\n<{source_url}>" if source_url else ""
                    await channel.send(
                        content=(
                            f"**{name}** - clipped from **{title}** "
                            f"({fmt_timestamp(start_s)} - {fmt_timestamp(end_s)}) "
                            f"by {interaction.user.mention}{source_line}"
                        ),
                        file=discord.File(out_path, filename=f"{name}.mp3"),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                upload_note = f"\nPosted to {channel.mention}."
                progress.finish(True, f"saved '{name}' and posted to saved-clips")
            except discord.HTTPException as e:
                upload_note = f"\nSaved locally, but posting to the saved-clips channel failed: {e}"
                progress.finish(True, f"saved locally, upload failed: {e}")

        await interaction.followup.send(
            f"Clip saved successfully as **{name}**. Use `/cplay {name}` to play it back.{upload_note}{info.get('note', '')}"
        )

    @app_commands.command(name="cplay", description="Play a saved clip in your voice channel")
    @app_commands.describe(name="Name of the saved clip")
    async def cplay(self, interaction: discord.Interaction, name: str):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "You must be in a voice channel to use this command.", ephemeral=True
            )
            return
        if not NAME_REGEX.match(name.strip()):
            await interaction.response.send_message("Invalid clip name.", ephemeral=True)
            return
        path = find_saved_clip(name.strip())
        if path is None:
            await interaction.response.send_message(
                f"No saved clip named **{name}**.", ephemeral=True
            )
            return

        voice: Optional[discord.VoiceClient] = get(
            self.bot.voice_clients, guild=interaction.guild
        )
        if voice and (voice.is_playing() or voice.is_paused()):
            await interaction.response.send_message(
                "Something is already playing, use /stop first or wait for it to finish.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        needs_connect = not (voice and voice.is_connected())
        progress = TaskProgress(
            "cplay",
            (["connect"] if needs_connect else []) + ["playback"],
            context=f"[{interaction.guild.name}] by {interaction.user} -> '{name}'",
        )
        try:
            voice = await self._join_voice(interaction, progress, needs_connect)
        except Exception as e:
            progress.finish(False, str(e))
            await interaction.followup.send(f"Could not join voice: {e}")
            return

        async with progress.stage("playback", os.path.basename(path)):
            source = discord.FFmpegPCMAudio(path, executable=FFMPEG_EXECUTABLE, options="-vn")
            voice.play(
                discord.PCMVolumeTransformer(source, volume=0.5),
                after=lambda e: log(f"[{interaction.guild.name}] saved clip '{name}' finished"),
            )
        progress.finish(True, f"playing saved clip '{name}'")
        await interaction.followup.send(f"Playing saved clip **{name}**.")

    @cplay.autocomplete("name")
    async def cplay_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        names = set()
        try:
            for file in os.listdir(SAVED_DIR):
                base, ext = os.path.splitext(file)
                if ext.lower() in CLIP_EXTENSIONS:
                    names.add(base)
        except OSError:
            return []
        matches = sorted(n for n in names if current.lower() in n.lower())
        return [app_commands.Choice(name=n, value=n) for n in matches[:25]]


async def setup(bot):
    await bot.add_cog(Clip(bot))
