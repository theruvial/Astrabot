"""Show which audiobook requests match which Transmission torrents.

Read-only: it lists torrents and requests, prints the matches, and
changes nothing. Run it before turning the watcher on.

Run from the Astrabot folder:

    py -3.14 scripts\\check_torrents.py

It reads the Transmission address and login from config.json
(audiobooks.torrents). The password is never printed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs.audiobooks_torrents import (  # noqa: E402
    TransmissionClient,
    TransmissionError,
    find_matches,
)


async def main() -> int:
    with open("config.json", encoding="utf-8") as handle:
        cfg = (json.load(handle).get("audiobooks") or {}).get("torrents") or {}
    if not cfg.get("url"):
        print("No audiobooks.torrents.url in config.json.")
        return 1
    if not cfg.get("username") or not cfg.get("password"):
        print("audiobooks.torrents.username / password are blank in config.json. "
              "Fill them in first.")
        return 1
    client = TransmissionClient(cfg["url"], cfg["username"], cfg["password"])
    try:
        torrents = await client.list_torrents()
    except TransmissionError as exc:
        print(f"Could not read Transmission: {exc}")
        return 1
    db = sqlite3.connect("file:audiobooks.db?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    requests = [dict(r) for r in db.execute(
        "SELECT id, bookName, author, status, createdAt FROM requests")]
    print(f"{len(torrents)} torrent(s) in Transmission, {len(requests)} request(s).")
    waiting = [r for r in requests if r["status"] == "Requested"]
    print(f"{len(waiting)} request(s) still Requested.")
    matches = find_matches(requests, torrents)
    if not matches:
        print("No matches.")
    for row, torrent in matches:
        print(f"MATCH  request {row['id']} {row['bookName']!r} by {row['author'] or '?'}"
              f"  <->  torrent {torrent['name']!r} ({torrent.get('percentDone', 0):.0%})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
