"""Replace bare Audible IDs in audiobook requests with real titles and authors.

Requests made from Audible app share links were saved with the ASIN
(e.g. B0CLD8MBT6) as the title. This looks each one up on Audnexus and
fills in bookName and author. Rows that already have an author are
left alone.

Run from the Astrabot folder, with the bot running or stopped:

    py -3.14 scripts\\backfill_audiobook_titles.py           (shows changes)
    py -3.14 scripts\\backfill_audiobook_titles.py --apply   (saves them)

Opening the database adds the author column if it is missing, and
that step backs the file up first (audiobooks.db.pre-author-*.bak).
--apply also makes its own backup before writing.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from datetime import datetime
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs.audiobooks_db import AudiobooksDB  # noqa: E402
from cogs.audiobooks_providers import audible_asin, audnexus_lookup  # noqa: E402


async def main(apply: bool, path: str) -> None:
    db = AudiobooksDB(path)
    changes = []
    for row in db.list_requests():
        if row.get("author"):
            continue
        asin = audible_asin(urlparse(row["linkToBook"]).path)
        if not asin:
            continue
        found = await audnexus_lookup(asin)
        if not found:
            print(f"#{row['id']} {row['bookName']}: not found on Audnexus, left as is")
            continue
        changes.append((row, found))
        print(f"#{row['id']} {row['bookName']} -> {found['bookName']} by {found['author'] or '?'}")
    if not changes:
        print("Nothing to change.")
        return
    if not apply:
        print("Dry run. Run again with --apply to save.")
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = sqlite3.connect(f"{path}.pre-backfill-{stamp}.bak")
    db.conn.backup(backup)
    backup.close()
    for row, found in changes:
        db.update_request(row["id"], found["bookName"], row["linkToBook"], found["author"])
    print(f"Saved {len(changes)} request(s). Backup: {path}.pre-backfill-{stamp}.bak")
    print("Discord embeds keep the old title until the request's status next changes.")


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv, "audiobooks.db"))
