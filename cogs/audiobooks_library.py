"""Close audiobook requests when Audiobookshelf has the book.

Second half of the torrent flow:

1. ``audiobooks_torrents`` sees the torrent appear in Transmission and
   marks the request ``In Progress``, which tells the requester it is
   being worked on.
2. This module watches the Audiobookshelf library. When a book that
   matches an ``In Progress`` request shows up there, the request is
   marked ``Fulfilled`` (the requester is told the book is on the
   server, and the Discord post is deleted).

A request is never closed because a download finished, only because
Audiobookshelf itself reports the book in its library.

No Discord code in here, so it is testable without a bot.

Config, in ``config.json`` under ``audiobooks.library``::

    "library": {
        "enabled": false,
        "dry_run": true,
        "url": "https://audio.theruvial.com",
        "api_key": "",
        "library_id": "",
        "interval_seconds": 120
    }

``api_key`` is an Audiobookshelf API token. Leave it blank to use the
``ABS_API_KEY`` environment variable, the same variable absLogin uses,
so the key stays in one place and is never copied into ``config.json``
(which sits on the Drive). Leave ``library_id`` blank to search every
book library.
``dry_run`` defaults to true: matches are only logged.

Matching reuses the strict torrent matcher: every word of the title
must be in the library title, the author's surname must agree, and a
book added to the library long before the request does not count. A
request matching several library books, or a book matching several
requests, is left for Thea.
"""

from __future__ import annotations

import asyncio
import logging
import os

import aiohttp

from .audiobooks_torrents import match_torrent

log = logging.getLogger("audiobooks.library")

# How many of the newest library items to look at. A request is only
# ever closed by a recently added book, so the newest few hundred are
# plenty and keep each check small.
RECENT_ITEMS = 300


class AudiobookshelfError(Exception):
    pass


class AudiobookshelfClient:
    """Minimal read-only Audiobookshelf API client."""

    def __init__(self, url: str, api_key: str, library_id: str = "",
                 timeout: float = 15.0):
        self.url = url.rstrip("/")
        self.library_id = library_id.strip()
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(self.url + path, params=params,
                                       headers=self._headers) as resp:
                    if resp.status in (401, 403):
                        raise AudiobookshelfError(f"login rejected ({resp.status})")
                    if resp.status != 200:
                        raise AudiobookshelfError(f"HTTP {resp.status}")
                    return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise AudiobookshelfError(f"unreachable: {exc}") from exc

    async def _library_ids(self) -> list[str]:
        if self.library_id:
            return [self.library_id]
        data = await self._get("/api/libraries")
        return [lib["id"] for lib in data.get("libraries") or []
                if isinstance(lib, dict) and lib.get("mediaType") == "book"
                and lib.get("id")]

    async def list_items(self) -> list[dict]:
        """The newest books: ``{"id", "title", "author", "addedDate"}``.

        ``addedDate`` is in seconds, like Transmission's, so the torrent
        matcher can be reused.
        """
        items: list[dict] = []
        for library_id in await self._library_ids():
            data = await self._get(
                f"/api/libraries/{library_id}/items",
                {"limit": RECENT_ITEMS, "sort": "addedAt", "desc": 1, "minified": 1})
            for entry in data.get("results") or []:
                if not isinstance(entry, dict):
                    continue
                meta = (entry.get("media") or {}).get("metadata") or {}
                added = entry.get("addedAt")
                items.append({
                    "id": entry.get("id"),
                    "title": str(meta.get("title") or ""),
                    "author": str(meta.get("authorName") or ""),
                    "addedDate": added / 1000 if isinstance(added, (int, float)) else 0,
                })
        return items


def match_library_item(row: dict, item: dict) -> bool:
    """True when this library book is the one the request asked for."""
    return match_torrent(row, {
        "name": f"{item.get('title') or ''} {item.get('author') or ''}",
        "addedDate": item.get("addedDate"),
    })


def find_library_matches(requests: list[dict], items: list[dict]) -> list[tuple[dict, dict]]:
    """(request, library item) pairs for requests that are ``In Progress``."""
    waiting = [r for r in requests if r.get("status") == "In Progress"]
    pairs: dict[int, list[dict]] = {}
    for row in waiting:
        hits = [i for i in items if match_library_item(row, i)]
        if hits:
            pairs[row["id"]] = hits
    claimed: dict[str, int] = {}
    for hits in pairs.values():
        for item in hits:
            claimed[item["id"]] = claimed.get(item["id"], 0) + 1
    out = []
    for row in waiting:
        hits = pairs.get(row["id"]) or []
        if len(hits) == 1 and claimed[hits[0]["id"]] == 1:
            out.append((row, hits[0]))
    return out


class LibraryWatcher:
    """Polls Audiobookshelf and marks matching In Progress requests Fulfilled.

    ``cog`` needs ``db`` (with ``list_requests``) and ``admin_fulfilled``,
    the same path the Mark Fulfilled button uses. In dry-run mode it
    only logs, and logs each match once.
    """

    def __init__(self, cog, client: AudiobookshelfClient, *,
                 dry_run: bool = True, interval_seconds: float = 120):
        self.cog = cog
        self.client = client
        self.dry_run = dry_run
        self._reported: set[tuple[int, str]] = set()
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
        except Exception as exc:  # noqa: BLE001 - must never kill the bot
            log.warning("Library check failed: %s", exc)

    async def run_once(self) -> list[tuple[dict, dict]]:
        # Nothing In Progress: no reason to ask Audiobookshelf anything.
        if not any(r.get("status") == "In Progress" for r in self.cog.db.list_requests()):
            return []
        try:
            items = await self.client.list_items()
        except AudiobookshelfError as exc:
            if self._reachable is not False:
                log.warning("Audiobookshelf check failed: %s", exc)
            self._reachable = False
            return []
        if self._reachable is False:
            log.info("Audiobookshelf is reachable again.")
        self._reachable = True
        matches = find_library_matches(self.cog.db.list_requests(), items)
        for row, item in matches:
            key = (row["id"], str(item["id"]))
            if self.dry_run:
                if key not in self._reported:
                    self._reported.add(key)
                    log.info("Dry run: request %s (%s) is in the library as %r. "
                             "Not changing anything.", row["id"],
                             row.get("bookName"), item.get("title"))
                continue
            _, sent, error = await self.cog.admin_fulfilled(row["id"])
            if error:
                log.info("Request %s is in the library but was not closed: %s",
                         row["id"], error)
            else:
                log.info("Request %s (%s) marked Fulfilled: library has %r%s.",
                         row["id"], row.get("bookName"), item.get("title"),
                         "" if sent else " (email could not be sent)")
        return matches


def build_library_watcher(cog) -> LibraryWatcher | None:
    """A watcher from ``audiobooks.library`` config, or None if off."""
    cfg = (cog.config or {}).get("library") or {}
    api_key = str(cfg.get("api_key") or os.environ.get("ABS_API_KEY") or "")
    if not cfg.get("enabled") or not cfg.get("url") or not api_key:
        return None
    client = AudiobookshelfClient(
        str(cfg["url"]), api_key, str(cfg.get("library_id") or ""))
    return LibraryWatcher(
        cog, client, dry_run=bool(cfg.get("dry_run", True)),
        interval_seconds=float(cfg.get("interval_seconds") or 120))
