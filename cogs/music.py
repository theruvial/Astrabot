import asyncio
from collections import deque
from dataclasses import dataclass
import json
import os
import re
import shutil
import ssl
from typing import Deque, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp
import certifi
import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import get
import yt_dlp

from config import config

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

try:
    import imageio_ffmpeg
except ImportError:
    imageio_ffmpeg = None

try:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
except ImportError:
    spotipy = None


def get_ffmpeg_executable() -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    if imageio_ffmpeg is not None:
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    return "ffmpeg"


FFMPEG_EXECUTABLE = get_ffmpeg_executable()

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

YTDL_STREAM_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

YTDL_PLAYLIST_OPTS = {
    "extract_flat": "in_playlist",
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
}

SPOTIFY_URL_REGEX = re.compile(
    r"(?:https?://open\.spotify\.com/(?:intl-[a-zA-Z-]+/)?|spotify:)(track|album|playlist)[:/]([a-zA-Z0-9]+)"
)


@dataclass
class Track:
    title: str
    query_or_url: str
    provider: str
    webpage_url: Optional[str] = None
    stream_url: Optional[str] = None
    uploader: Optional[str] = None
    duration: Optional[int] = None
    thumbnail: Optional[str] = None
    requester_id: Optional[int] = None

    def format_duration(self) -> str:
        if not self.duration:
            return "Live / Unknown"
        minutes, seconds = divmod(int(self.duration), 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queues: Dict[int, Deque[Track]] = {}
        self.current_track: Dict[int, Optional[Track]] = {}
        self.volumes: Dict[int, float] = {}
        self.text_channels: Dict[int, discord.abc.Messageable] = {}
        self.spotify_client = self._init_spotify()

    def _init_spotify(self):
        if spotipy is None:
            return None
        client_id = config.get("spotifyClientId")
        client_secret = config.get("spotifyClientSecret")
        if client_id and client_secret:
            try:
                auth_manager = SpotifyClientCredentials(
                    client_id=client_id, client_secret=client_secret
                )
                return spotipy.Spotify(auth_manager=auth_manager)
            except Exception as e:
                print(f"Failed to initialize Spotipy client: {e}")
        return None

    def get_queue(self, guild_id: int) -> Deque[Track]:
        if guild_id not in self.queues:
            self.queues[guild_id] = deque()
        return self.queues[guild_id]

    @staticmethod
    def _is_url(text: str) -> bool:
        try:
            parsed = urlparse(text.strip())
            return parsed.scheme in ("http", "https") and bool(parsed.netloc)
        except Exception:
            return False

    @staticmethod
    def _detect_provider_from_url(url: str) -> str:
        host = urlparse(url.lower()).netloc
        if "spotify.com" in host or url.startswith("spotify:"):
            return "Spotify"
        if "soundcloud.com" in host:
            return "SoundCloud"
        if "music.youtube.com" in host:
            return "YouTube Music"
        if "youtube.com" in host or "youtu.be" in host:
            return "YouTube"
        return "Stream"

    async def _resolve_spotify_url(
        self, url: str, requester_id: Optional[int]
    ) -> List[Track]:
        match = SPOTIFY_URL_REGEX.search(url)
        if not match:
            raise ValueError("Invalid or unsupported Spotify URL.")

        kind, item_id = match.group(1), match.group(2)
        tracks: List[Track] = []

        # 1. Try official Spotipy client if credentials are configured
        if self.spotify_client is not None:
            loop = asyncio.get_running_loop()
            try:
                if kind == "track":
                    item = await loop.run_in_executor(
                        None, lambda: self.spotify_client.track(item_id)
                    )
                    artists = ", ".join(a["name"] for a in item.get("artists", []))
                    title = f"{artists} - {item['name']}" if artists else item["name"]
                    images = item.get("album", {}).get("images", [])
                    thumb = images[0]["url"] if images else None
                    tracks.append(
                        Track(
                            title=title,
                            query_or_url=f"ytsearch1:{title} audio",
                            provider="Spotify",
                            webpage_url=item.get("external_urls", {}).get("spotify", url),
                            uploader=artists,
                            duration=int(item.get("duration_ms", 0) // 1000) or None,
                            thumbnail=thumb,
                            requester_id=requester_id,
                        )
                    )
                    return tracks
                elif kind == "album":
                    album = await loop.run_in_executor(
                        None, lambda: self.spotify_client.album(item_id)
                    )
                    images = album.get("images", [])
                    thumb = images[0]["url"] if images else None
                    for item in album.get("tracks", {}).get("items", []):
                        artists = ", ".join(a["name"] for a in item.get("artists", []))
                        title = f"{artists} - {item['name']}" if artists else item["name"]
                        tracks.append(
                            Track(
                                title=title,
                                query_or_url=f"ytsearch1:{title} audio",
                                provider="Spotify",
                                webpage_url=item.get("external_urls", {}).get("spotify", url),
                                uploader=artists,
                                duration=int(item.get("duration_ms", 0) // 1000) or None,
                                thumbnail=thumb,
                                requester_id=requester_id,
                            )
                        )
                    return tracks
                elif kind == "playlist":
                    playlist = await loop.run_in_executor(
                        None, lambda: self.spotify_client.playlist_items(item_id, limit=100)
                    )
                    for entry in playlist.get("items", []):
                        item = entry.get("track")
                        if not item:
                            continue
                        artists = ", ".join(a["name"] for a in item.get("artists", []))
                        title = f"{artists} - {item['name']}" if artists else item["name"]
                        images = item.get("album", {}).get("images", [])
                        thumb = images[0]["url"] if images else None
                        tracks.append(
                            Track(
                                title=title,
                                query_or_url=f"ytsearch1:{title} audio",
                                provider="Spotify",
                                webpage_url=item.get("external_urls", {}).get("spotify", url),
                                uploader=artists,
                                duration=int(item.get("duration_ms", 0) // 1000) or None,
                                thumbnail=thumb,
                                requester_id=requester_id,
                            )
                        )
                    return tracks
            except Exception as e:
                print(f"Spotipy lookup failed, falling back to Spotify embed parser: {e}")

        # 2. Keyless fallback using Spotify's public embed & oEmbed endpoints
        embed_url = f"https://open.spotify.com/embed/{kind}/{item_id}"
        headers = {"User-Agent": "Mozilla/5.0"}
        connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            async with session.get(embed_url, timeout=15) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    next_data_match = re.search(
                        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                        html,
                        re.DOTALL,
                    )
                    if next_data_match:
                        payload = json.loads(next_data_match.group(1))
                        entity = (
                            payload.get("props", {})
                            .get("pageProps", {})
                            .get("state", {})
                            .get("data", {})
                            .get("entity", {})
                        )
                        cover_images = (
                            entity.get("visualIdentity", {})
                            .get("image", [])
                        )
                        thumb = cover_images[0].get("url") if cover_images else None

                        if kind == "track":
                            name = entity.get("title") or entity.get("name")
                            artists = ", ".join(
                                a.get("name", "")
                                for a in entity.get("artists", [])
                                if a.get("name")
                            )
                            if name:
                                full_title = f"{artists} - {name}" if artists else name
                                duration_ms = entity.get("duration")
                                tracks.append(
                                    Track(
                                        title=full_title,
                                        query_or_url=f"ytsearch1:{full_title} audio",
                                        provider="Spotify",
                                        webpage_url=f"https://open.spotify.com/track/{item_id}",
                                        uploader=artists or None,
                                        duration=int(duration_ms // 1000) if duration_ms else None,
                                        thumbnail=thumb,
                                        requester_id=requester_id,
                                    )
                                )
                                return tracks
                        else:
                            for item in entity.get("trackList", []):
                                name = item.get("title")
                                subtitle = item.get("subtitle", "")
                                if not name:
                                    continue
                                full_title = f"{subtitle} - {name}" if subtitle else name
                                duration_ms = item.get("duration")
                                tracks.append(
                                    Track(
                                        title=full_title,
                                        query_or_url=f"ytsearch1:{full_title} audio",
                                        provider="Spotify",
                                        webpage_url=f"https://open.spotify.com/{kind}/{item_id}",
                                        uploader=subtitle or None,
                                        duration=int(duration_ms // 1000) if duration_ms else None,
                                        thumbnail=thumb,
                                        requester_id=requester_id,
                                    )
                                )
                            if tracks:
                                return tracks

            # Final oEmbed fallback for single tracks
            oembed_url = f"https://open.spotify.com/oembed?url=https://open.spotify.com/{kind}/{item_id}"
            async with session.get(oembed_url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    title = data.get("title")
                    if title:
                        tracks.append(
                            Track(
                                title=title,
                                query_or_url=f"ytsearch1:{title} audio",
                                provider="Spotify",
                                webpage_url=f"https://open.spotify.com/{kind}/{item_id}",
                                thumbnail=data.get("thumbnail_url"),
                                requester_id=requester_id,
                            )
                        )
                        return tracks

        raise ValueError("Could not extract track metadata from Spotify URL.")

    async def _resolve_tracks(
        self,
        query: str,
        provider_choice: str,
        requester_id: Optional[int],
    ) -> List[Track]:
        query = query.strip()

        # Check if query is a Spotify URL/URI
        if SPOTIFY_URL_REGEX.search(query):
            return await self._resolve_spotify_url(query, requester_id)

        loop = asyncio.get_running_loop()

        # Direct URL (YouTube, YouTube Music, SoundCloud, etc.)
        if self._is_url(query):
            detected_provider = self._detect_provider_from_url(query)

            def extract_url():
                with yt_dlp.YoutubeDL(YTDL_PLAYLIST_OPTS) as ydl:
                    return ydl.extract_info(query, download=False)

            info = await loop.run_in_executor(None, extract_url)
            if not info:
                raise ValueError("No playable media found at that URL.")

            # Playlist / Album URL
            if "entries" in info:
                tracks: List[Track] = []
                for entry in info["entries"]:
                    if not entry:
                        continue
                    entry_url = entry.get("url") or entry.get("webpage_url")
                    if entry_url and not self._is_url(entry_url):
                        if detected_provider in ("YouTube", "YouTube Music"):
                            entry_url = f"https://www.youtube.com/watch?v={entry_url}"
                    tracks.append(
                        Track(
                            title=entry.get("title") or "Untitled Track",
                            query_or_url=entry_url or query,
                            provider=detected_provider,
                            webpage_url=entry_url,
                            uploader=entry.get("uploader") or entry.get("channel"),
                            duration=entry.get("duration"),
                            requester_id=requester_id,
                        )
                    )
                if tracks:
                    return tracks

            # Single URL
            return [
                Track(
                    title=info.get("title") or query,
                    query_or_url=query,
                    provider=detected_provider,
                    webpage_url=info.get("webpage_url") or query,
                    stream_url=info.get("url") if not info.get("_type") else None,
                    uploader=info.get("uploader") or info.get("artist"),
                    duration=info.get("duration"),
                    thumbnail=info.get("thumbnail"),
                    requester_id=requester_id,
                )
            ]

        # Search query based on selected provider
        if provider_choice == "soundcloud":
            search_query = f"scsearch1:{query}"
            provider_label = "SoundCloud"
        elif provider_choice == "ytmusic":
            search_query = f"ytsearch1:{query} audio"
            provider_label = "YouTube Music"
        elif provider_choice == "spotify":
            if self.spotify_client is not None:
                try:
                    results = await loop.run_in_executor(
                        None,
                        lambda: self.spotify_client.search(q=query, type="track", limit=1),
                    )
                    items = results.get("tracks", {}).get("items", [])
                    if items:
                        item = items[0]
                        artists = ", ".join(a["name"] for a in item.get("artists", []))
                        title = f"{artists} - {item['name']}" if artists else item["name"]
                        images = item.get("album", {}).get("images", [])
                        thumb = images[0]["url"] if images else None
                        return [
                            Track(
                                title=title,
                                query_or_url=f"ytsearch1:{title} audio",
                                provider="Spotify",
                                webpage_url=item.get("external_urls", {}).get("spotify"),
                                uploader=artists,
                                duration=int(item.get("duration_ms", 0) // 1000) or None,
                                thumbnail=thumb,
                                requester_id=requester_id,
                            )
                        ]
                except Exception as e:
                    print(f"Spotipy search error: {e}")
            search_query = f"ytsearch1:{query} official audio"
            provider_label = "Spotify"
        else:
            search_query = f"ytsearch1:{query}"
            provider_label = "YouTube"

        def extract_search():
            with yt_dlp.YoutubeDL(YTDL_STREAM_OPTS) as ydl:
                return ydl.extract_info(search_query, download=False)

        info = await loop.run_in_executor(None, extract_search)
        if info and "entries" in info:
            entries = [e for e in info["entries"] if e]
            info = entries[0] if entries else None

        if not info:
            raise ValueError(f"No results found for `{query}` on {provider_label}.")

        return [
            Track(
                title=info.get("title") or query,
                query_or_url=info.get("webpage_url") or search_query,
                provider=provider_label,
                webpage_url=info.get("webpage_url"),
                stream_url=info.get("url"),
                uploader=info.get("uploader") or info.get("artist"),
                duration=info.get("duration"),
                thumbnail=info.get("thumbnail"),
                requester_id=requester_id,
            )
        ]

    async def _ensure_stream_info(self, track: Track) -> Track:
        """Resolves a fresh direct audio stream URL just before playback."""
        loop = asyncio.get_running_loop()

        def extract():
            with yt_dlp.YoutubeDL(YTDL_STREAM_OPTS) as ydl:
                return ydl.extract_info(track.query_or_url, download=False)

        info = await loop.run_in_executor(None, extract)
        if info and "entries" in info:
            entries = [e for e in info["entries"] if e]
            info = entries[0] if entries else None

        if not info or "url" not in info:
            raise ValueError(f"Could not extract audio stream for `{track.title}`.")

        track.stream_url = info["url"]
        if not track.webpage_url:
            track.webpage_url = info.get("webpage_url")
        if not track.uploader:
            track.uploader = info.get("uploader") or info.get("artist")
        if not track.duration:
            track.duration = info.get("duration")
        if not track.thumbnail:
            track.thumbnail = info.get("thumbnail")
        if track.title == track.query_or_url and info.get("title"):
            track.title = info["title"]
        return track

    def _build_track_embed(self, header: str, track: Track) -> discord.Embed:
        embed = discord.Embed(
            title=track.title,
            url=track.webpage_url,
            color=discord.Colour(3665255),
        )
        embed.set_author(name=f"{header} ({track.provider})")
        if track.uploader:
            embed.add_field(name="Artist / Channel", value=track.uploader, inline=True)
        embed.add_field(name="Duration", value=track.format_duration(), inline=True)
        if track.requester_id:
            embed.add_field(
                name="Requested By", value=f"<@{track.requester_id}>", inline=True
            )
        if track.thumbnail:
            embed.set_thumbnail(url=track.thumbnail)
        return embed

    async def _play_next(self, guild: discord.Guild):
        queue = self.get_queue(guild.id)
        voice: Optional[discord.VoiceClient] = get(self.bot.voice_clients, guild=guild)

        if not voice or not voice.is_connected():
            self.current_track[guild.id] = None
            return

        if not queue:
            self.current_track[guild.id] = None
            return

        next_track = queue.popleft()
        try:
            await self._ensure_stream_info(next_track)
        except Exception as e:
            print(f"Error resolving stream for {next_track.title}: {e}")
            channel = self.text_channels.get(guild.id)
            if channel:
                await channel.send(
                    f"Skipping **{next_track.title}** (unable to load stream)."
                )
            await self._play_next(guild)
            return

        self.current_track[guild.id] = next_track
        volume = self.volumes.get(guild.id, 0.5)

        source = discord.FFmpegPCMAudio(
            next_track.stream_url,
            executable=FFMPEG_EXECUTABLE,
            **FFMPEG_OPTIONS,
        )
        transformed_source = discord.PCMVolumeTransformer(source, volume=volume)

        def after_playback(error):
            if error:
                print(f"Playback error in guild {guild.id}: {error}")
            asyncio.run_coroutine_threadsafe(
                self._play_next(guild), self.bot.loop
            )

        voice.play(transformed_source, after=after_playback)

        channel = self.text_channels.get(guild.id)
        if channel:
            embed = self._build_track_embed("Now Streaming", next_track)
            await channel.send(embed=embed)

    @app_commands.command(
        name="play",
        description="Stream music from YouTube, YouTube Music, SoundCloud, or Spotify",
    )
    @app_commands.describe(
        query="A song title, artist, or URL (YouTube, YT Music, SoundCloud, Spotify)",
        provider="Provider to search when a search term is given instead of a URL",
    )
    @app_commands.choices(
        provider=[
            app_commands.Choice(name="Auto / YouTube", value="youtube"),
            app_commands.Choice(name="YouTube Music", value="ytmusic"),
            app_commands.Choice(name="SoundCloud", value="soundcloud"),
            app_commands.Choice(name="Spotify", value="spotify"),
        ]
    )
    async def play(
        self,
        interaction: discord.Interaction,
        query: str,
        provider: Optional[app_commands.Choice[str]] = None,
    ):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "You must be in a voice channel to use this command.", ephemeral=True
            )
            return

        await interaction.response.defer()
        self.text_channels[interaction.guild.id] = interaction.channel

        voice: Optional[discord.VoiceClient] = get(
            self.bot.voice_clients, guild=interaction.guild
        )
        voice_channel = interaction.user.voice.channel

        if voice and voice.is_connected():
            if voice.channel != voice_channel:
                await voice.move_to(voice_channel)
        else:
            voice = await voice_channel.connect()

        provider_value = provider.value if provider else "youtube"

        try:
            tracks = await self._resolve_tracks(
                query, provider_value, interaction.user.id
            )
        except Exception as e:
            await interaction.followup.send(f"Could not load track: {e}")
            return

        queue = self.get_queue(interaction.guild.id)
        queue.extend(tracks)

        is_busy = voice.is_playing() or voice.is_paused()

        if len(tracks) == 1:
            track = tracks[0]
            if is_busy:
                embed = self._build_track_embed("Added to Queue", track)
                embed.set_footer(text=f"Position in queue: {len(queue)}")
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send(
                    f"Loading **{track.title}** from **{track.provider}**..."
                )
                await self._play_next(interaction.guild)
        else:
            await interaction.followup.send(
                f"Queued **{len(tracks)}** tracks from **{tracks[0].provider}**!"
            )
            if not is_busy:
                await self._play_next(interaction.guild)

    @app_commands.command(name="skip", description="Skip the currently playing song")
    async def skip(self, interaction: discord.Interaction):
        voice: Optional[discord.VoiceClient] = get(
            self.bot.voice_clients, guild=interaction.guild
        )
        if voice and (voice.is_playing() or voice.is_paused()):
            current = self.current_track.get(interaction.guild.id)
            voice.stop()  # Triggers after_playback -> _play_next
            title = current.title if current else "current track"
            await interaction.response.send_message(f"Skipped **{title}**.")
        else:
            await interaction.response.send_message(
                "Nothing is currently playing.", ephemeral=True
            )

    @app_commands.command(name="queue", description="Show the current music queue")
    async def show_queue(self, interaction: discord.Interaction):
        current = self.current_track.get(interaction.guild.id)
        queue = self.get_queue(interaction.guild.id)

        if not current and not queue:
            await interaction.response.send_message("The music queue is empty.")
            return

        embed = discord.Embed(
            title="Music Queue", color=discord.Colour(3665255)
        )
        if current:
            embed.add_field(
                name="Now Playing",
                value=f"**{current.title}** [{current.provider}] (`{current.format_duration()}`)",
                inline=False,
            )

        if queue:
            upcoming_lines = []
            for idx, track in enumerate(list(queue)[:10], start=1):
                upcoming_lines.append(
                    f"`{idx}.` **{track.title}** [{track.provider}] (`{track.format_duration()}`)"
                )
            if len(queue) > 10:
                upcoming_lines.append(f"...and **{len(queue) - 10}** more track(s).")
            embed.add_field(
                name="Up Next",
                value="\n".join(upcoming_lines),
                inline=False,
            )
        else:
            embed.add_field(name="Up Next", value="No tracks queued.", inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="nowplaying", description="Show the currently playing song")
    async def nowplaying(self, interaction: discord.Interaction):
        current = self.current_track.get(interaction.guild.id)
        if not current:
            await interaction.response.send_message(
                "Nothing is currently playing.", ephemeral=True
            )
            return
        embed = self._build_track_embed("Now Playing", current)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="volume", description="Set the playback volume (0-200%)")
    @app_commands.describe(level="Volume percentage between 0 and 200")
    async def volume(self, interaction: discord.Interaction, level: int):
        if level < 0 or level > 200:
            await interaction.response.send_message(
                "Please choose a volume level between 0 and 200.", ephemeral=True
            )
            return

        vol_float = level / 100.0
        self.volumes[interaction.guild.id] = vol_float

        voice: Optional[discord.VoiceClient] = get(
            self.bot.voice_clients, guild=interaction.guild
        )
        if voice and voice.source and isinstance(voice.source, discord.PCMVolumeTransformer):
            voice.source.volume = vol_float

        await interaction.response.send_message(f"Volume set to **{level}%**.")

    @app_commands.command(name="pause", description="Pause currently playing audio")
    async def pause(self, interaction: discord.Interaction):
        voice = get(self.bot.voice_clients, guild=interaction.guild)

        if voice and voice.is_playing():
            voice.pause()
            await interaction.response.send_message("Audio Paused")
        else:
            await interaction.response.send_message("Music not playing")

    @app_commands.command(name="resume", description="Resume paused audio")
    async def resume(self, interaction: discord.Interaction):
        voice = get(self.bot.voice_clients, guild=interaction.guild)

        if voice and voice.is_paused():
            voice.resume()
            await interaction.response.send_message("Resume Playing")
        else:
            await interaction.response.send_message("Music Not Paused")

    @app_commands.command(name="stop", description="Stop playing audio and clear the queue")
    async def stop(self, interaction: discord.Interaction):
        voice = get(self.bot.voice_clients, guild=interaction.guild)

        queue = self.get_queue(interaction.guild.id)
        queue.clear()
        self.current_track[interaction.guild.id] = None

        if voice and (voice.is_playing() or voice.is_paused()):
            voice.stop()
            await interaction.response.send_message("Audio Stopped and Queue Cleared")
        else:
            await interaction.response.send_message("Audio not playing")

    @app_commands.command(name="leave", description="Make the bot leave the voice channel")
    async def leave(self, interaction: discord.Interaction):
        channel = (
            interaction.user.voice.channel
            if interaction.user.voice
            else None
        )
        voice = get(self.bot.voice_clients, guild=interaction.guild)
        queue = self.get_queue(interaction.guild.id)
        queue.clear()
        self.current_track[interaction.guild.id] = None

        if voice and voice.is_connected():
            await voice.disconnect()
            await interaction.response.send_message("Disconnected from voice channel.")
        else:
            await interaction.response.send_message(
                f"{self.bot.user.name} is not currently connected to {channel}"
            )


async def setup(bot):
    await bot.add_cog(Music(bot))
