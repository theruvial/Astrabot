"""Match audiobook requests to torrents in Transmission.

The audiobooks cog can watch Thea's Transmission client. When a torrent
shows up whose name matches a request that is still ``Requested``, the
request is marked ``In Progress``, which emails the requester and
updates the Discord post, exactly as the Fulfill Request button does.

This module has no Discord code, so it can be tested and run from a
script (``scripts/check_torrents.py``) without a bot.

Config, in ``config.json`` under ``audiobooks.torrents``::

    "torrents": {
        "enabled": false,
        "dry_run": true,
        "url": "http://192.168.4.74:9092/transmission/rpc",
        "username": "",
        "password": "",
        "interval_seconds": 120
    }

``dry_run`` defaults to true: matches are only logged. Set it to false
to let the bot change requests. The username and password live in
``config.json`` only, which is never committed.

Matching is deliberately strict. A wrong match emails a requester that
their book is being worked on, so a missed match (Thea marks it by
hand) is better than a false one.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

import aiohttp

log = logging.getLogger("audiobooks.torrents")

# Words that say how a release was packaged, not what the book is.
NOISE = {
    "audiobook", "audiobooks", "audio", "book", "unabridged", "abridged",
    "mp3", "m4b", "m4a", "flac", "kbps", "retail", "complete", "full",
    "cast", "narrated", "by", "series", "vol", "volume",
}
TITLE_STOPWORDS = {"the", "a", "an", "of", "and", "to", "in"}
# A torrent added up to this long before the request still counts.
ADDED_SLACK_SECONDS = 3600


class TransmissionError(Exception):
    pass


class TransmissionClient:
    """Minimal Transmission RPC client (read-only: lists torrents)."""

    def __init__(self, url: str, username: str = "", password: str = "",
                 timeout: float = 10.0):
        self.url = url
        self._auth_header = (aiohttp.encode_basic_auth(username, password)
                             if username else None)
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session_id = ""

    async def _call(self, payload: dict) -> dict:
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                for _ in range(2):
                    headers = {"X-Transmission-Session-Id": self._session_id}
                    if self._auth_header:
                        headers["Authorization"] = self._auth_header
                    async with session.post(
                            self.url, json=payload, headers=headers) as resp:
                        if resp.status == 409:
                            # Transmission hands out a session token this way.
                            self._session_id = resp.headers.get(
                                "X-Transmission-Session-Id", "")
                            continue
                        if resp.status == 401:
                            raise TransmissionError("login rejected (401)")
                        if resp.status != 200:
                            raise TransmissionError(f"HTTP {resp.status}")
                        return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise TransmissionError(f"unreachable: {exc}") from exc
        raise TransmissionError("no session token")

    async def list_torrents(self) -> list[dict]:
        data = await self._call({
            "method": "torrent-get",
            "arguments": {"fields": ["id", "name", "percentDone", "addedDate"]},
        })
        if data.get("result") != "success":
            raise TransmissionError(str(data.get("result")))
        torrents = (data.get("arguments") or {}).get("torrents") or []
        return [t for t in torrents if isinstance(t, dict)]


# "Book 2", "Vol. 3", "Part 1": a position in a series, not part of the title.
_SERIES_NUMBER_RE = re.compile(r"\b(book|vol|volume|part)\.?\s*#?\d+\b", re.IGNORECASE)


def tokens(text: str) -> list[str]:
    """Lowercase words, punctuation removed, accents left alone."""
    return re.findall(r"[a-z0-9]+", str(text or "").lower().replace("'", ""))


def _title_tokens(title: str) -> list[str]:
    words = [w for w in tokens(title) if w not in NOISE]
    meaningful = [w for w in words if w not in TITLE_STOPWORDS]
    return meaningful or words


def _author_surname(author: str) -> str | None:
    first = re.split(r",|&|\band\b", str(author or ""), maxsplit=1)[0]
    words = tokens(first)
    return words[-1] if words else None


def _created_ts(row: dict) -> float | None:
    try:
        stamp = datetime.fromisoformat(str(row.get("createdAt")))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def match_torrent(row: dict, torrent: dict) -> bool:
    """True if this torrent is very likely the book in this request.

    All of the title's words must be in the torrent name. Then either
    the author's surname is in the name too, or the torrent name is
    little more than the title (so "Dune" does not match
    "Dune Messiah"). Torrents added before the request are ignored.
    """
    want = _title_tokens(row.get("bookName") or "")
    if not want:
        return False
    # A bare Audible ID is not a title. Wait for a real one.
    if re.fullmatch(r"b[0-9a-z]{9}", "".join(want)):
        return False
    name_text = _SERIES_NUMBER_RE.sub(" ", str(torrent.get("name") or ""))
    name = [w for w in tokens(name_text) if w not in NOISE]
    have = set(name)
    if not all(w in have for w in want):
        return False
    created = _created_ts(row)
    added = torrent.get("addedDate")
    if created is not None and isinstance(added, (int, float)) and added > 0:
        if added < created - ADDED_SLACK_SECONDS:
            return False
    surname = _author_surname(row.get("author") or "")
    if surname and surname in have:
        return True
    extra = [w for w in name if w not in set(want) and w not in TITLE_STOPWORDS]
    # No author in the name: the name must be the title and (at most) one
    # more word, and a one-word title must match with nothing extra.
    return not extra or (len(extra) == 1 and len(want) >= 2)


def find_matches(requests: list[dict], torrents: list[dict]) -> list[tuple[dict, dict]]:
    """(request, torrent) pairs for requests still waiting to start.

    A request matching several torrents is skipped: it is ambiguous, so
    Thea decides. A torrent matching several requests is skipped too.
    """
    waiting = [r for r in requests if r.get("status") == "Requested"]
    pairs: dict[int, list[dict]] = {}
    for row in waiting:
        hits = [t for t in torrents if match_torrent(row, t)]
        if hits:
            pairs[row["id"]] = hits
    claimed: dict[int, int] = {}
    for hits in pairs.values():
        for torrent in hits:
            claimed[torrent["id"]] = claimed.get(torrent["id"], 0) + 1
    out = []
    for row in waiting:
        hits = pairs.get(row["id"]) or []
        if len(hits) == 1 and claimed[hits[0]["id"]] == 1:
            out.append((row, hits[0]))
    return out


class TorrentWatcher:
    """Polls Transmission and marks matching requests In Progress.

    ``cog`` needs ``db`` (with ``list_requests``) and ``admin_fulfill``,
    which is the same In Progress path the Fulfill Request button uses.
    In dry-run mode it only logs, and logs each match once.
    """

    def __init__(self, cog, client: TransmissionClient, *,
                 dry_run: bool = True, interval_seconds: float = 120):
        self.cog = cog
        self.client = client
        self.dry_run = dry_run
        self._reported: set[tuple[int, int]] = set()
        self._reachable: bool | None = None
        from discord.ext import tasks as ext_tasks

        self._loop = ext_tasks.loop(seconds=max(30, interval_seconds))(self._tick)
        self._loop.before_loop(self._before)

    async def _before(self) -> None:
        await self.cog.bot.wait_until_ready()

    def start(self) -> None:
        if not self._loop.is_running():
            self._loop.start()

    def stop(self) -> None:
        self._loop.cancel()

    async def _tick(self) -> None:
        try:
            await self.run_once()
        except Exception as exc:  # noqa: BLE001 - the watcher must never kill the bot
            log.warning("Torrent check failed: %s", exc)

    async def run_once(self) -> list[tuple[dict, dict]]:
        try:
            torrents = await self.client.list_torrents()
        except TransmissionError as exc:
            if self._reachable is not False:
                log.warning("Transmission check failed: %s", exc)
            self._reachable = False
            return []
        if self._reachable is False:
            log.info("Transmission is reachable again.")
        self._reachable = True
        matches = find_matches(self.cog.db.list_requests(), torrents)
        for row, torrent in matches:
            key = (row["id"], torrent["id"])
            if self.dry_run:
                if key not in self._reported:
                    self._reported.add(key)
                    log.info("Dry run: request %s (%s) matches torrent %r. "
                             "Not changing anything.", row["id"],
                             row.get("bookName"), torrent.get("name"))
                continue
            _, sent, error = await self.cog.admin_fulfill(row["id"])
            if error:
                log.info("Request %s matched torrent %r but was not changed: %s",
                         row["id"], torrent.get("name"), error)
            else:
                log.info("Request %s (%s) marked In Progress: torrent %r%s.",
                         row["id"], row.get("bookName"), torrent.get("name"),
                         "" if sent else " (email could not be sent)")
        return matches


def build_watcher(cog) -> TorrentWatcher | None:
    """A watcher from ``audiobooks.torrents`` config, or None if off."""
    cfg = (cog.config or {}).get("torrents") or {}
    if not cfg.get("enabled") or not cfg.get("url"):
        return None
    client = TransmissionClient(
        str(cfg["url"]), str(cfg.get("username") or ""),
        str(cfg.get("password") or ""))
    return TorrentWatcher(
        cog, client, dry_run=bool(cfg.get("dry_run", True)),
        interval_seconds=float(cfg.get("interval_seconds") or 120))
