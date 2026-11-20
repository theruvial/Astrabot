"""Private endpoint for the audiobook watcher on the Ubuntu server.

The watcher (``~/audiobook-watcher`` on 192.168.4.74, not in this repo)
files every new torrent into the library. It knows the real title and
author of each book, even inside a multi-book pack whose torrent name
says nothing useful. It sends those books here, and the bot links each
one to a waiting request:

1. ``POST /intake/books`` with the torrent's hash and name and a list of
   books. Each book that matches exactly one ``Requested`` request (and
   that request matches only that book) moves the request to
   ``In Progress`` and records the torrent hash on it. **No email is
   sent** (Thea, 2026-11-20): the requester gets one email, when the
   book is in the library.
2. ``audiobooks_library`` then closes the request as ``Fulfilled`` once
   Audiobookshelf reports the book, as before.

Books that match no request are ignored: Thea also adds books that
nobody asked for, and those need nothing on the requests side.

Security (the bot's public site is proxied by nginx on the *same*
server as the watcher, so the source address alone proves nothing):

* a separate port that nginx never forwards (default 13385);
* any request carrying a proxy header (X-Forwarded-For, X-Real-IP, ...)
  is refused, because the watcher talks to the bot directly;
* the peer address must be in ``allowed_ips`` (the watcher's server);
* a shared secret in ``Authorization: Bearer <secret>``, compared in
  constant time. With no secret configured the endpoint does not start.

Config, in ``config.json`` under ``audiobooks.intake``::

    "intake": {
        "enabled": false,
        "dry_run": true,
        "host": "0.0.0.0",
        "port": 13385,
        "secret": "",
        "allowed_ips": ["192.168.4.74"]
    }

``secret`` may be left blank to use the ``AUDIOBOOKS_INTAKE_SECRET``
environment variable instead. ``dry_run`` defaults to true: matches are
reported back and logged, nothing is changed.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from typing import Mapping

from .audiobooks_access import PROXY_HEADERS, parse_ip
from .audiobooks_torrents import match_title_exact

log = logging.getLogger("audiobooks.intake")

MAX_BODY_BYTES = 256 * 1024
MAX_BOOKS = 200
DEFAULT_PORT = 13385


# -- who may call ------------------------------------------------------
def intake_secret(cfg: Mapping) -> str:
    return str(cfg.get("secret") or os.environ.get("AUDIOBOOKS_INTAKE_SECRET") or "")


def check_caller(peer: object, headers: Mapping[str, str], secret: str,
                 allowed_ips) -> str | None:
    """None if the caller is the watcher, else a short refusal reason.

    Fails closed: no secret, no allowed address, a proxy header or a
    wrong secret all refuse.
    """
    if not secret:
        return "no_secret_configured"
    lowered = {str(k).lower(): v for k, v in headers.items()}
    if any(h in lowered for h in PROXY_HEADERS):
        return "proxied"
    ip = parse_ip(peer)
    allowed = {parse_ip(a) for a in (allowed_ips or [])} - {None}
    if ip is None or ip not in allowed:
        return "address_not_allowed"
    auth = str(lowered.get("authorization") or "")
    scheme, _, given = auth.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
            given.strip().encode(), secret.encode()):
        return "bad_secret"
    return None


# -- what was sent -----------------------------------------------------
def parse_payload(raw: object) -> tuple[dict | None, str | None]:
    """Validate the watcher's JSON. Returns (payload, error)."""
    if not isinstance(raw, dict):
        return None, "body must be a JSON object"
    torrent = raw.get("torrent")
    if not isinstance(torrent, dict):
        return None, "torrent must be an object"
    torrent_hash = str(torrent.get("hash") or "").strip().lower()
    if not torrent_hash or len(torrent_hash) > 64 or not torrent_hash.isalnum():
        return None, "torrent.hash is required"
    books = raw.get("books")
    if not isinstance(books, list) or not books:
        return None, "books must be a non-empty list"
    if len(books) > MAX_BOOKS:
        return None, f"at most {MAX_BOOKS} books"
    clean = []
    for book in books:
        if not isinstance(book, dict) or not str(book.get("title") or "").strip():
            return None, "every book needs a title"
        clean.append({
            "title": str(book["title"]).strip()[:300],
            "author": str(book.get("author") or "").strip()[:300],
            "series": str(book.get("series") or "").strip()[:300],
        })
    return {
        "torrent": {"hash": torrent_hash,
                    "name": str(torrent.get("name") or "").strip()[:500]},
        "books": clean,
    }, None


# -- which request each book is ----------------------------------------
def match_book(row: dict, book: dict) -> bool:
    """True when this filed book is the one the request asked for.

    Same exact rule as the library (``match_title_exact``): the titles
    have the same words and the author agrees. No date check (the watcher
    only sends books it is filing now).
    """
    return match_title_exact(row, book.get("title") or "", book.get("author") or "")


def plan_links(requests: list[dict], books: list[dict], torrent_hash: str) -> list[dict]:
    """One result per book: which request it links to, if any.

    ``status`` is ``linked`` (a Requested row to move to In Progress),
    ``already_linked`` (In Progress and already tied to this torrent:
    the watcher resent), ``ambiguous`` or ``no_request``. A book
    matching several requests, or a request matching several books, is
    ambiguous and left for Thea, as everywhere else.
    """
    waiting = [r for r in requests if r.get("status") == "Requested"]
    mine = [r for r in requests if r.get("status") == "In Progress"
            and (r.get("torrentHash") or "") == torrent_hash]
    hits = [[r for r in waiting if match_book(r, b)] for b in books]
    claims: dict[int, int] = {}
    for rows in hits:
        for r in rows:
            claims[r["id"]] = claims.get(r["id"], 0) + 1
    results = []
    for book, rows in zip(books, hits):
        out = {"title": book["title"], "author": book["author"], "requestId": None}
        done = [r for r in mine if match_book(r, book)]
        if done:
            out.update(status="already_linked", requestId=done[0]["id"])
        elif len(rows) == 1 and claims[rows[0]["id"]] == 1:
            out.update(status="linked", requestId=rows[0]["id"])
        elif rows:
            out["status"] = "ambiguous"
        else:
            out["status"] = "no_request"
        results.append(out)
    return results


# -- the aiohttp app ---------------------------------------------------
class IntakeServer:
    """``cog`` needs ``db.list_requests`` and ``mark_downloading``."""

    def __init__(self, cog, cfg: Mapping):
        self.cog = cog
        self.cfg = cfg
        self.secret = intake_secret(cfg)
        self.allowed_ips = list(cfg.get("allowed_ips") or [])
        self.dry_run = bool(cfg.get("dry_run", True))

    def build_app(self):
        from aiohttp import web

        app = web.Application(client_max_size=MAX_BODY_BYTES)
        app.router.add_post("/intake/books", self.books)
        app.router.add_get("/intake/health", self.health)
        return app

    def _refusal(self, request):
        from aiohttp import web

        reason = check_caller(request.remote, request.headers, self.secret, self.allowed_ips)
        if reason is None:
            return None
        log.warning("Intake refused a call from %s: %s", request.remote, reason)
        return web.json_response({"error": "forbidden"}, status=403)

    async def health(self, request):
        from aiohttp import web

        refused = self._refusal(request)
        if refused is not None:
            return refused
        return web.json_response({"ok": True, "dryRun": self.dry_run})

    async def books(self, request):
        from aiohttp import web

        refused = self._refusal(request)
        if refused is not None:
            return refused
        try:
            raw = json.loads(await request.text())
        except (ValueError, UnicodeDecodeError):
            return web.json_response({"error": "body is not JSON"}, status=400)
        payload, error = parse_payload(raw)
        if error:
            return web.json_response({"error": error}, status=400)
        return web.json_response(await self.handle(payload))

    async def handle(self, payload: dict) -> dict:
        torrent = payload["torrent"]
        results = plan_links(self.cog.db.list_requests(), payload["books"], torrent["hash"])
        for res in results:
            if res["status"] != "linked":
                continue
            if self.dry_run:
                res["status"] = "would_link"
                log.info("Dry run: %r from torrent %r matches request %s. Not changing anything.",
                         res["title"], torrent["name"], res["requestId"])
                continue
            _, error = await self.cog.mark_downloading(res["requestId"], torrent["hash"])
            if error:
                res["status"] = error
                log.info("Request %s matched %r but was not changed: %s",
                         res["requestId"], res["title"], error)
            else:
                log.info("Request %s marked In Progress (no email): %r in torrent %r.",
                         res["requestId"], res["title"], torrent["name"])
        return {"torrent": torrent["hash"], "dryRun": self.dry_run, "books": results}


def build_intake(cog) -> IntakeServer | None:
    """An intake server from ``audiobooks.intake``, or None if off.

    Off unless enabled, a secret is set and at least one address is
    allowed.
    """
    cfg = (cog.config or {}).get("intake") or {}
    if not cfg.get("enabled"):
        return None
    server = IntakeServer(cog, cfg)
    if not server.secret or not server.allowed_ips:
        log.warning("Intake is enabled but has no secret or no allowed_ips; not starting.")
        return None
    return server
