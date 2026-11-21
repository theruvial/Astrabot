"""Read an audiobook's embedded tags with ffprobe (reads the file header only; ~0.1 s per file)."""
import json
import logging
import re
import subprocess
from dataclasses import dataclass, field

from textutil import clean_name, norm, sim, tidy_title  # noqa: F401  (norm re-used by verify)

log = logging.getLogger("audiobook-watcher.tags")
AUDIO_EXT = (".m4b", ".m4a", ".mp3", ".flac", ".ogg", ".opus", ".aac", ".wma")


@dataclass
class Tags:
    title: str = ""
    authors: list = field(default_factory=list)   # in the order the file lists them
    album: str = ""


def _book_title(raw: str) -> str:
    """'The Eye of the World (The Wheel of Time #1)' -> 'The Eye of the World'; drops '(Unabridged)' etc."""
    s = re.sub(r"\s*\([^)]*\)\s*$|\s*\[[^\]]*\]\s*$", "", raw or "").strip()
    return tidy_title(clean_name(s))


def split_authors(s: str) -> list:
    out = []
    for p in re.split(r"\s*(?:,|;|/|&|\+|\band\b)\s*", s or "", flags=re.I):
        p = re.sub(r"\(.*?\)|\[.*?\]", "", p).strip(" .-")
        if len(norm(p)) >= 2 and p not in out:
            out.append(p)
    return out


def probe(path, timeout=30):
    """Tags of one audio file, or None when ffprobe fails or finds no author/title."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=timeout)
        raw = (json.loads(r.stdout or "{}").get("format") or {}).get("tags") or {}
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        log.warning("ffprobe failed on %s: %s", path, e)
        return None
    tg = {k.lower(): v for k, v in raw.items() if isinstance(v, str)}
    authors = split_authors(tg.get("album_artist") or tg.get("artist") or tg.get("author") or "")
    album = _book_title(tg.get("album") or "")
    # The ALBUM is the book; the title tag of a multi-file book is the chapter ('Intro', 'Chapter 3').
    title = album or _book_title(tg.get("title") or "")
    return Tags(title, authors, album) if authors or title else None


def author_matches(folder_display: str, authors: list) -> bool:
    """True if ANY tagged author is the folder's author (a co-author tag must never trigger a move)."""
    f = norm(folder_display)
    for a in authors:
        n = norm(a)
        if n == f or sim(a, folder_display) >= 0.8:
            return True
        if min(len(n), len(f)) >= 6 and (n in f or f in n):
            return True
    return False
