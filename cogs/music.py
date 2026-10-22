import asyncio
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
import itertools
import json
import os
import re
import shutil
import ssl
import time
from typing import Deque, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

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

def log(message: str) -> None:
    """Timestamped console output for the music cog."""
    print(f"{time.strftime('%H:%M:%S')} [music] {message}", flush=True)


def fmt_secs(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    if seconds >= 60:
        minutes, secs = divmod(int(seconds), 60)
        return f"{minutes}m{secs:02d}s"
    return f"{seconds:.1f}s"


class YtdlConsoleLogger:
    """Forwards yt-dlp's own progress messages (e.g. 'Downloading webpage',
    'Extracting URL') to the console so you can see what it is working on."""

    def debug(self, msg: str) -> None:
        if msg.startswith("[debug]"):
            return
        log(f"    yt-dlp: {msg}")

    def info(self, msg: str) -> None:
        self.debug(msg)

    def warning(self, msg: str) -> None:
        log(f"    yt-dlp WARNING: {msg}")

    def error(self, msg: str) -> None:
        log(f"    yt-dlp ERROR: {msg}")


STAGE_LABELS = {
    "connect": "Connecting to voice channel",
    "resolve_spotify": "Resolving Spotify metadata",
    "resolve_url": "Extracting info from URL",
    "search": "Searching for track",
    "stream": "Fetching direct audio stream URL",
    "playback": "Starting FFmpeg playback",
    "cut": "Cutting clip with FFmpeg",
    "upload": "Uploading to saved-clips",
}


class TaskProgress:
    """Tracks the stages of a long-running music task ("Bot is thinking...")
    and prints what it is doing along with an estimated time to completion.

    Stage durations are seeded with defaults and then refined with a moving
    average of how long each stage actually took earlier in this session, so
    the ETA gets more accurate the longer the bot runs.
    """

    DEFAULT_SECONDS = {
        "connect": 1.5,
        "resolve_spotify": 2.0,
        "resolve_url": 4.0,
        "search": 3.0,
        "stream": 3.0,
        "playback": 0.5,
        "cut": 6.0,
        "upload": 3.0,
    }
    TICK_SECONDS = 2.0
    _history: Dict[str, float] = {}
    _ids = itertools.count(1)

    def __init__(self, command: str, stage_keys: List[str], context: str = ""):
        self.id = next(self._ids)
        self.command = command
        self.stages: List[Tuple[str, str]] = [
            (key, STAGE_LABELS.get(key, key)) for key in stage_keys
        ]
        self.started = time.monotonic()
        self.index = 0
        self._finished = False
        plan = " -> ".join(label for _, label in self.stages)
        log(f"#{self.id} /{command} started {context}".rstrip())
        log(
            f"#{self.id} plan: {plan} | "
            f"estimated total ~{fmt_secs(sum(self.expected(k) for k, _ in self.stages))}"
        )

    @classmethod
    def expected(cls, key: str) -> float:
        return cls._history.get(key, cls.DEFAULT_SECONDS.get(key, 3.0))

    @classmethod
    def _record(cls, key: str, took: float) -> None:
        old = cls._history.get(key)
        cls._history[key] = took if old is None else (0.7 * old + 0.3 * took)

    def _remaining(self, stage_elapsed: float) -> float:
        key = self.stages[self.index][0]
        current = max(self.expected(key) - stage_elapsed, 0.0)
        later = sum(self.expected(k) for k, _ in self.stages[self.index + 1:])
        return current + later

    async def _tick(self, label: str, detail: str, started: float) -> None:
        try:
            while True:
                await asyncio.sleep(self.TICK_SECONDS)
                elapsed = time.monotonic() - started
                remaining = self._remaining(elapsed)
                eta = f"~{fmt_secs(remaining)}" if remaining > 0.05 else "any moment now"
                log(
                    f"#{self.id}     ...still on '{label}'"
                    f"{f' ({detail})' if detail else ''} | stage {fmt_secs(elapsed)}"
                    f" | total {fmt_secs(time.monotonic() - self.started)} | ETA {eta}"
                )
        except asyncio.CancelledError:
            pass

    @asynccontextmanager
    async def stage(self, key: str, detail: str = ""):
        idx = next((i for i, (k, _) in enumerate(self.stages) if k == key), None)
        if idx is None:
            self.stages.append((key, STAGE_LABELS.get(key, key)))
            idx = len(self.stages) - 1
        self.index = idx
        label = self.stages[idx][1]
        started = time.monotonic()
        log(
            f"#{self.id} [{idx + 1}/{len(self.stages)}] {label}"
            f"{f': {detail}' if detail else ''} | ETA ~{fmt_secs(self._remaining(0.0))}"
        )
        ticker = asyncio.create_task(self._tick(label, detail, started))
        try:
            yield
        except Exception as e:
            log(f"#{self.id}     X {label} failed after {fmt_secs(time.monotonic() - started)}: {e}")
            raise
        else:
            took = time.monotonic() - started
            self._record(key, took)
            log(f"#{self.id}     OK {label} done in {fmt_secs(took)}")
        finally:
            ticker.cancel()

    def finish(self, ok: bool = True, note: str = "") -> None:
        if self._finished:
            return
        self._finished = True
        status = "completed" if ok else "ended early"
        log(
            f"#{self.id} /{self.command} {status} in "
            f"{fmt_secs(time.monotonic() - self.started)}{f' - {note}' if note else ''}"
        )


try:
    import deno as _deno_pkg
except ImportError:
    _deno_pkg = None


def get_js_runtimes() -> Optional[dict]:
    deno_path = shutil.which("deno")
    if not deno_path and _deno_pkg is not None:
        try:
            deno_path = _deno_pkg.find_deno_bin()
        except Exception:
            deno_path = None
    if deno_path:
        return {"deno": {"path": deno_path}}
    return None


JS_RUNTIMES = get_js_runtimes()
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_cookies_file() -> Optional[str]:
    cookies_setting = config.get("cookiesFile", "cookies.txt")
    candidates = [
        cookies_setting if os.path.isabs(cookies_setting) else os.path.join(PROJECT_ROOT, cookies_setting),
        os.path.join(PROJECT_ROOT, "cookies.txt"),
    ]
    for path in candidates:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    return None


def get_ytdl_opts(base_opts: dict, use_cookies: bool = False) -> dict:
    opts = dict(base_opts)
    opts.pop("cookiefile", None)
    if JS_RUNTIMES:
        opts["js_runtimes"] = JS_RUNTIMES
    if use_cookies:
        cookie_path = resolve_cookies_file()
        if cookie_path:
            opts["cookiefile"] = cookie_path
    return opts


def ytdl_extract_sync(target: str, base_opts: dict):
    """Extracts info with yt-dlp, trying without cookies first (fast mobile clients)
    and automatically retrying with cookies.txt + Deno JS runtime for age-restricted tracks."""
    cookie_path = resolve_cookies_file()
    attempts = [False, True] if cookie_path else [False]
    last_err = None
    for use_cookies in attempts:
        opts = get_ytdl_opts(base_opts, use_cookies=use_cookies)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(target, download=False)
        except Exception as e:
            last_err = e
            if use_cookies is False and cookie_path:
                log(f"    retrying with cookies.txt after unauthenticated error: {e}")
    raise last_err


def _clean_search_term(raw: str) -> str:
    cleaned = re.sub(r"^(?:ytsearch|scsearch)\d*:", "", raw.strip(), flags=re.IGNORECASE)
    return cleaned.strip()


def _provider_search_order(query: str, preferred: str = "youtube") -> List[Tuple[str, str]]:
    clean = _clean_search_term(query)
    audio_clean = re.sub(r"\s+(?:official\s+)?audio$", "", clean, flags=re.IGNORECASE).strip() or clean
    all_providers = {
        "youtube": ("YouTube", f"ytsearch5:{audio_clean}"),
        "ytmusic": ("YouTube Music", f"ytsearch5:{audio_clean} official audio"),
        "soundcloud": ("SoundCloud", f"scsearch5:{audio_clean}"),
    }
    if preferred == "spotify":
        order = ["ytmusic", "youtube", "soundcloud"]
    elif preferred in all_providers:
        order = [preferred] + [k for k in ("youtube", "ytmusic", "soundcloud") if k != preferred]
    else:
        order = ["youtube", "ytmusic", "soundcloud"]

    seen = set()
    result: List[Tuple[str, str]] = []
    for key in order:
        label, search_str = all_providers[key]
        if search_str not in seen:
            seen.add(search_str)
            result.append((label, search_str))
    return result


def search_across_platforms_sync(
    query: str,
    preferred: str = "youtube",
    target_duration: Optional[int] = None,
) -> Tuple[dict, str]:
    """Searches for a playable audio stream across YouTube, YouTube Music, and SoundCloud,
    trying multiple results per platform until a working stream is found."""
    search_plan = _provider_search_order(query, preferred)
    last_err: Optional[Exception] = None

    for idx, (provider_label, search_expr) in enumerate(search_plan):
        if idx > 0:
            log(f"    continuing search on {provider_label}: '{search_expr}'")
        try:
            flat_info = ytdl_extract_sync(search_expr, YTDL_PLAYLIST_OPTS)
        except Exception as e:
            log(f"    search on {provider_label} failed ({e}), trying next platform...")
            last_err = e
            continue

        entries = [e for e in (flat_info or {}).get("entries", []) if e]
        if not entries and flat_info and flat_info.get("url"):
            entries = [flat_info]

        if not entries:
            log(f"    no results on {provider_label}, trying next platform...")
            continue

        if target_duration:
            def _dur_Sort_key(entry: dict) -> float:
                d = entry.get("duration")
                if d is None:
                    return 9999.0
                return abs(float(d) - float(target_duration))
            entries.sort(key=_dur_Sort_key)

        for cand_idx, entry in enumerate(entries, start=1):
            cand_url = entry.get("url") or entry.get("webpage_url") or entry.get("id")
            if not cand_url:
                continue
            if not cand_url.startswith(("http://", "https://")):
                if provider_label in ("YouTube", "YouTube Music"):
                    cand_url = f"https://www.youtube.com/watch?v={cand_url}"
                else:
                    continue
            cand_title = entry.get("title") or cand_url
            try:
                stream_info = ytdl_extract_sync(cand_url, YTDL_STREAM_OPTS)
                if stream_info and "entries" in stream_info:
                    sub = [e for e in stream_info["entries"] if e]
                    stream_info = sub[0] if sub else None
                if stream_info and stream_info.get("url"):
                    if idx > 0 or cand_idx > 1:
                        log(
                            f"    matched playable stream on {provider_label} "
                            f"(result #{cand_idx}): '{stream_info.get('title') or cand_title}'"
                        )
                    return stream_info, provider_label
            except Exception as e:
                last_err = e
                log(
                    f"    result #{cand_idx} on {provider_label} ('{cand_title}') "
                    f"unavailable, trying next..."
                )

    if last_err:
        raise ValueError(
            f"Could not find a playable stream for `{_clean_search_term(query)}` across YouTube, YouTube Music, or SoundCloud ({last_err})."
        )
    raise ValueError(
        f"No playable results found for `{_clean_search_term(query)}` across YouTube, YouTube Music, or SoundCloud."
    )


YTDL_STREAM_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "ffmpeg_location": FFMPEG_EXECUTABLE,
    "logger": YtdlConsoleLogger(),
}

YTDL_PLAYLIST_OPTS = {
    "extract_flat": "in_playlist",
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
    "ffmpeg_location": FFMPEG_EXECUTABLE,
    "logger": YtdlConsoleLogger(),
}

if resolve_cookies_file():
    log(f"Found YouTube cookies at {resolve_cookies_file()} (JS runtime: {'Deno' if JS_RUNTIMES else 'none'})")

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


class PlaylistPromptView(discord.ui.View):
    """Interactive prompt asking the user whether to queue only the first track
    or the entire playlist."""

    def __init__(self, requester_id: int, total_count: int, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.requester_id = requester_id
        self.choice: Optional[str] = None
        self.all_button.label = f"Entire Playlist ({total_count} tracks)"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the user who requested this link can choose.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="First Video Only", style=discord.ButtonStyle.primary)
    async def first_only_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.choice = "first"
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Entire Playlist", style=discord.ButtonStyle.success)
    async def all_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.choice = "all"
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.choice = "cancel"
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


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
                return ytdl_extract_sync(query, YTDL_PLAYLIST_OPTS)

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
                    # If the URL points to a specific video inside a playlist (?v=ID&list=...),
                    # make sure that specific video is first when the user picks "First Video Only".
                    qs = parse_qs(urlparse(query).query)
                    target_vid = (qs.get("v") or [None])[0]
                    if target_vid:
                        match_idx = next(
                            (
                                i
                                for i, t in enumerate(tracks)
                                if t.webpage_url and target_vid in t.webpage_url
                            ),
                            None,
                        )
                        if match_idx is not None and match_idx > 0:
                            tracks.insert(0, tracks.pop(match_idx))
                        elif match_idx is None:
                            single_url = f"https://www.youtube.com/watch?v={target_vid}"
                            tracks.insert(
                                0,
                                Track(
                                    title=single_url,
                                    query_or_url=single_url,
                                    provider=detected_provider,
                                    webpage_url=single_url,
                                    requester_id=requester_id,
                                ),
                            )
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

        # Search query across all supported platforms (starting with selected provider)
        search_target = query
        is_spotify_search = provider_choice == "spotify"
        spotify_webpage = None
        spotify_thumb = None
        spotify_artists = None
        spotify_duration = None

        if is_spotify_search and self.spotify_client is not None:
            try:
                results = await loop.run_in_executor(
                    None,
                    lambda: self.spotify_client.search(q=query, type="track", limit=1),
                )
                items = results.get("tracks", {}).get("items", [])
                if items:
                    item = items[0]
                    spotify_artists = ", ".join(a["name"] for a in item.get("artists", []))
                    search_target = (
                        f"{spotify_artists} - {item['name']}"
                        if spotify_artists
                        else item["name"]
                    )
                    images = item.get("album", {}).get("images", [])
                    spotify_thumb = images[0]["url"] if images else None
                    spotify_webpage = item.get("external_urls", {}).get("spotify")
                    spotify_duration = int(item.get("duration_ms", 0) // 1000) or None
            except Exception as e:
                print(f"Spotipy search error: {e}")

        def extract_search():
            return search_across_platforms_sync(
                search_target,
                preferred=provider_choice,
                target_duration=spotify_duration,
            )

        info, matched_provider = await loop.run_in_executor(None, extract_search)
        final_provider = "Spotify" if is_spotify_search else matched_provider

        return [
            Track(
                title=search_target if (is_spotify_search and spotify_artists) else (info.get("title") or query),
                query_or_url=info.get("webpage_url") or search_target,
                provider=final_provider,
                webpage_url=spotify_webpage or info.get("webpage_url"),
                stream_url=info.get("url"),
                uploader=spotify_artists or info.get("uploader") or info.get("artist"),
                duration=spotify_duration or info.get("duration"),
                thumbnail=spotify_thumb or info.get("thumbnail"),
                requester_id=requester_id,
            )
        ]

    async def _ensure_stream_info(self, track: Track) -> Track:
        """Resolves a fresh direct audio stream URL just before playback, falling back
        across all supported platforms if a specific video/source is unavailable."""
        if track.stream_url:
            return track
        loop = asyncio.get_running_loop()

        def extract():
            target = track.query_or_url.strip()
            if self._is_url(target):
                try:
                    info = ytdl_extract_sync(target, YTDL_STREAM_OPTS)
                    if info and "entries" in info:
                        entries = [e for e in info["entries"] if e]
                        info = entries[0] if entries else None
                    if info and info.get("url"):
                        return info, track.provider
                except Exception as url_err:
                    if track.title and track.title != target:
                        log(
                            f"    direct URL unavailable ({url_err}), searching other platforms for '{track.title}'..."
                        )
                        return search_across_platforms_sync(
                            track.title,
                            preferred="youtube",
                            target_duration=track.duration,
                        )
                    raise
            search_term = track.title if track.provider == "Spotify" else target
            pref = "soundcloud" if track.provider == "SoundCloud" else "youtube"
            return search_across_platforms_sync(
                search_term,
                preferred=pref,
                target_duration=track.duration,
            )

        info, matched_provider = await loop.run_in_executor(None, extract)

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

    async def _play_next(
        self, guild: discord.Guild, progress: Optional[TaskProgress] = None
    ):
        queue = self.get_queue(guild.id)
        voice: Optional[discord.VoiceClient] = get(self.bot.voice_clients, guild=guild)

        if not voice or not voice.is_connected():
            self.current_track[guild.id] = None
            if progress:
                progress.finish(False, "not connected to voice")
            return

        if not queue:
            self.current_track[guild.id] = None
            log(f"[{guild.name}] queue finished, nothing left to play")
            if progress:
                progress.finish(False, "queue empty")
            return

        next_track = queue.popleft()
        if progress is None:
            progress = TaskProgress(
                "auto-advance",
                ["stream", "playback"],
                context=f"[{guild.name}] next up: {next_track.title} ({len(queue)} left in queue)",
            )

        try:
            async with progress.stage(
                "stream", f"{next_track.title} via {next_track.provider}"
            ):
                await self._ensure_stream_info(next_track)
        except Exception as e:
            print(f"Error resolving stream for {next_track.title}: {e}")
            progress.finish(False, f"could not load {next_track.title}")
            channel = self.text_channels.get(guild.id)
            if channel:
                await channel.send(
                    f"Skipping **{next_track.title}** (unable to load stream)."
                )
            await self._play_next(guild)
            return

        self.current_track[guild.id] = next_track
        volume = self.volumes.get(guild.id, 0.5)

        async with progress.stage("playback", f"{next_track.title} @ {int(volume * 100)}% volume"):
            source = discord.FFmpegPCMAudio(
                next_track.stream_url,
                executable=FFMPEG_EXECUTABLE,
                **FFMPEG_OPTIONS,
            )
            transformed_source = discord.PCMVolumeTransformer(source, volume=volume)

            def after_playback(error):
                if error:
                    print(f"Playback error in guild {guild.id}: {error}")
                log(f"[{guild.name}] finished: {next_track.title}")
                asyncio.run_coroutine_threadsafe(
                    self._play_next(guild), self.bot.loop
                )

            voice.play(transformed_source, after=after_playback)

        progress.finish(True, f"now streaming '{next_track.title}' ({next_track.format_duration()})")

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
        provider_value = provider.value if provider else "youtube"

        # Work out which stages this request will go through so the ETA is meaningful
        needs_connect = not (voice and voice.is_connected())
        already_busy = bool(voice and (voice.is_playing() or voice.is_paused()))
        if SPOTIFY_URL_REGEX.search(query):
            resolve_key, kind = "resolve_spotify", "Spotify link"
        elif self._is_url(query):
            resolve_key, kind = "resolve_url", f"{self._detect_provider_from_url(query)} link"
        else:
            resolve_key, kind = "search", f"search on {provider_value}"

        stage_keys = (["connect"] if needs_connect else []) + [resolve_key]
        if not already_busy:
            stage_keys += ["stream", "playback"]

        progress = TaskProgress(
            "play",
            stage_keys,
            context=f"[{interaction.guild.name}] by {interaction.user} -> {kind}: '{query}'",
        )

        if needs_connect:
            async with progress.stage("connect", f"#{voice_channel.name}"):
                voice = await voice_channel.connect()
        elif voice.channel != voice_channel:
            await voice.move_to(voice_channel)

        try:
            async with progress.stage(resolve_key, f"'{query}'"):
                tracks = await self._resolve_tracks(
                    query, provider_value, interaction.user.id
                )
        except Exception as e:
            progress.finish(False, f"could not load track: {e}")
            await interaction.followup.send(f"Could not load track: {e}")
            return

        if len(tracks) == 1:
            log(f"#{progress.id} found: '{tracks[0].title}' [{tracks[0].provider}] ({tracks[0].format_duration()})")
        else:
            log(
                f"#{progress.id} playlist detected ({len(tracks)} tracks, first: '{tracks[0].title}'), "
                f"waiting for {interaction.user} to choose single track vs full playlist..."
            )
            prompt_view = PlaylistPromptView(
                requester_id=interaction.user.id, total_count=len(tracks)
            )
            prompt_embed = discord.Embed(
                title="Playlist Detected",
                description=(
                    f"This link contains **{len(tracks)}** tracks "
                    f"(first track: **{tracks[0].title}**).\n\n"
                    f"Would you like to queue **just the first video** or the **entire playlist**?"
                ),
                color=discord.Colour(3665255),
            )
            prompt_msg = await interaction.followup.send(
                embed=prompt_embed, view=prompt_view, wait=True
            )
            await prompt_view.wait()

            if prompt_view.choice == "cancel" or prompt_view.choice is None:
                reason = "cancelled by user" if prompt_view.choice == "cancel" else "prompt timed out"
                progress.finish(False, f"playlist prompt {reason}")
                status_text = (
                    "Playlist queueing cancelled."
                    if prompt_view.choice == "cancel"
                    else "Playlist prompt timed out — nothing was added to the queue."
                )
                await prompt_msg.edit(content=status_text, embed=None, view=None)
                return
            elif prompt_view.choice == "first":
                tracks = [tracks[0]]
                log(f"#{progress.id} user selected First Video Only: '{tracks[0].title}'")
                await prompt_msg.edit(
                    content=f"Selected **First Video Only**: **{tracks[0].title}**.",
                    embed=None,
                    view=None,
                )
            else:
                log(f"#{progress.id} user selected Entire Playlist ({len(tracks)} tracks)")
                await prompt_msg.edit(
                    content=f"Selected **Entire Playlist** (**{len(tracks)}** tracks).",
                    embed=None,
                    view=None,
                )

        queue = self.get_queue(interaction.guild.id)
        queue.extend(tracks)

        is_busy = voice.is_playing() or voice.is_paused()

        if len(tracks) == 1:
            track = tracks[0]
            if is_busy:
                embed = self._build_track_embed("Added to Queue", track)
                embed.set_footer(text=f"Position in queue: {len(queue)}")
                await interaction.followup.send(embed=embed)
                progress.finish(True, f"queued '{track.title}' at position {len(queue)}")
            else:
                await interaction.followup.send(
                    f"Loading **{track.title}** from **{track.provider}**..."
                )
                await self._play_next(interaction.guild, progress)
        else:
            await interaction.followup.send(
                f"Queued **{len(tracks)}** tracks from **{tracks[0].provider}**!"
            )
            if not is_busy:
                await self._play_next(interaction.guild, progress)
            else:
                progress.finish(True, f"queued {len(tracks)} tracks")

    @app_commands.command(name="skip", description="Skip the currently playing song")
    async def skip(self, interaction: discord.Interaction):
        voice: Optional[discord.VoiceClient] = get(
            self.bot.voice_clients, guild=interaction.guild
        )
        if voice and (voice.is_playing() or voice.is_paused()):
            current = self.current_track.get(interaction.guild.id)
            log(f"[{interaction.guild.name}] /skip by {interaction.user}: skipping '{current.title if current else '?'}'")
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
        log(f"[{interaction.guild.name}] /volume by {interaction.user}: set to {level}%")
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
            log(f"[{interaction.guild.name}] /pause by {interaction.user}")
            voice.pause()
            await interaction.response.send_message("Audio Paused")
        else:
            await interaction.response.send_message("Music not playing")

    @app_commands.command(name="resume", description="Resume paused audio")
    async def resume(self, interaction: discord.Interaction):
        voice = get(self.bot.voice_clients, guild=interaction.guild)

        if voice and voice.is_paused():
            log(f"[{interaction.guild.name}] /resume by {interaction.user}")
            voice.resume()
            await interaction.response.send_message("Resume Playing")
        else:
            await interaction.response.send_message("Music Not Paused")

    @app_commands.command(name="stop", description="Stop playing audio and clear the queue")
    async def stop(self, interaction: discord.Interaction):
        voice = get(self.bot.voice_clients, guild=interaction.guild)

        queue = self.get_queue(interaction.guild.id)
        log(f"[{interaction.guild.name}] /stop by {interaction.user}: clearing {len(queue)} queued track(s)")
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
        log(f"[{interaction.guild.name}] /leave by {interaction.user}")
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
