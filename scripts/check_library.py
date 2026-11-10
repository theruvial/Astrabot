"""Show which In Progress audiobook requests Audiobookshelf already has.

Read-only: it lists the newest library books and the requests, prints
the matches, and changes nothing. Run it before turning the library
watcher on.

Run from the Astrabot folder:

    py -3.14 scripts\\check_library.py

It reads the address and API key from config.json (audiobooks.library).
The key is never printed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs.audiobooks_library import (  # noqa: E402
    AudiobookshelfClient,
    AudiobookshelfError,
    find_library_matches,
)


async def main() -> int:
    with open("config.json", encoding="utf-8") as handle:
        cfg = (json.load(handle).get("audiobooks") or {}).get("library") or {}
    if not cfg.get("url") or not cfg.get("api_key"):
        print("audiobooks.library.url / api_key are blank in config.json. Fill them in first.")
        return 1
    client = AudiobookshelfClient(cfg["url"], cfg["api_key"], str(cfg.get("library_id") or ""))
    try:
        items = await client.list_items()
    except AudiobookshelfError as exc:
        print(f"Could not read Audiobookshelf: {exc}")
        return 1
    db = sqlite3.connect("file:audiobooks.db?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    requests = [dict(r) for r in db.execute("SELECT * FROM requests")]
    waiting = [r for r in requests if r["status"] == "In Progress"]
    print(f"{len(items)} newest library book(s), {len(waiting)} request(s) In Progress.")
    matches = find_library_matches(requests, items)
    if not matches:
        print("No matches.")
    for row, found in matches:
        print(f"MATCH  request {row['id']} {row['bookName']!r} by {row.get('author') or '?'}"
              f"  <->  library {found['title']!r} by {found['author'] or '?'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
