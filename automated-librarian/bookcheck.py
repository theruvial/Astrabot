"""Ask a book catalogue (Audible via Audiobookshelf, else Open Library) who really wrote a book.

Returns (status, authors):
  confirmed    - a result with this title names one of the tagged authors; authors = the catalogue's list
  contradicted - results for this title agree on a different author; authors = that list
  unknown      - nothing usable (no result, results disagree, or the lookup failed)
Tags often carry the narrator, so a tag alone never decides a move; the catalogue must agree.
"""
import logging
import re
from collections import Counter, namedtuple

from tags import author_matches, split_authors
from textutil import norm, sim

log = logging.getLogger("automated-librarian.bookcheck")
Verdict = namedtuple("Verdict", "status authors series title", defaults=("", ""))
_EDITION = re.compile(r"\s*(?:\([^)]*\)|\[[^\]]*\]|,\s*book\s*\d+.*|:\s*book\s*\d+.*)$", re.I)


def clean_catalogue_title(t: str) -> str:
    """'Harry Potter and the Deathly Hallows, Book 7' -> 'Harry Potter and the Deathly Hallows'."""
    prev = None
    while prev != t:
        prev, t = t, _EDITION.sub("", t).strip()
    return t


def clean_series(name: str) -> str:
    """Drop edition tags: 'Harry Potter (Narrated by Stephen Fry)' -> 'Harry Potter'."""
    return re.sub(r"\s*\([^)]*\)|\s*\[[^\]]*\]", "", name or "").strip()


def _title_hit(result_title: str, title: str) -> bool:
    a, b = norm(result_title), norm(title)
    return bool(b) and (a.startswith(b) or sim(result_title, title) >= 0.8)   # tolerate '(Narrated by ...)', ', Book 7'


class BookCheck:
    def __init__(self, search_fn=None, open_library_fn=None):
        """search_fn(title, author) -> [{'title','author'}...]; open_library_fn(author, title) -> (author, title)|None"""
        self.search, self.ol = search_fn, open_library_fn

    def lookup(self, title: str, hint_authors: list):
        """Who does the catalogue say wrote a book we only know by title (plus a guess)?
        confirmed - a result for this title names one of hint_authors
        other     - results agree (by majority) on a different author; authors/series/title are theirs
        unknown   - nothing found, results tied, or the search failed"""
        if not self.search or not title:
            return Verdict("unknown", [])
        try:
            results = self.search(title, hint_authors[0] if hint_authors else "") or self.search(title, "")
        except Exception as e:
            log.warning("catalogue search failed (%s)", e)
            return Verdict("unknown", [])
        hits = []
        for r in results:
            if _title_hit(r.get("title") or "", title):
                h = split_authors(r.get("author") or "")
                if h:
                    ser = clean_series(((r.get("series") or [{}])[0] or {}).get("series", ""))
                    hits.append((h, ser, clean_catalogue_title(r.get("title") or "")))
        for h, ser, ct in hits:
            if any(author_matches(a, h) for a in hint_authors):
                return Verdict("confirmed", h, ser, ct)
        if hits:
            key = lambda h: frozenset(norm(x) for x in h)
            votes = Counter(key(h) for h, _, _ in hits).most_common(2)
            if len(votes) == 1 or votes[0][1] > votes[1][1]:
                h, ser, ct = next(x for x in hits if key(x[0]) == votes[0][0])
                return Verdict("other", h, ser, ct)
        return Verdict("unknown", [])

    def __call__(self, authors: list, title: str):
        if not title or not authors:
            return Verdict("unknown", [])
        search_ok = False
        if self.search:
            try:
                results = self.search(title, authors[0]) or self.search(title, "")
                search_ok = True
            except Exception as e:
                log.warning("catalogue search failed (%s)", e)
                results = []
            hits = []
            for r in results:
                if _title_hit(r.get("title") or "", title):
                    h = split_authors(r.get("author") or "")
                    ser = clean_series(((r.get("series") or [{}])[0] or {}).get("series", ""))
                    if h:
                        hits.append((h, ser, clean_catalogue_title(r.get("title") or ""),
                                     split_authors(r.get("narrator") or "")))
            for h, ser, ct, _ in hits:
                if any(author_matches(a, h) for a in authors):
                    return Verdict("confirmed", h, ser, ct)
            if hits:   # majority vote among the results for this title; a tie means we can't tell
                key = lambda h: frozenset(norm(x) for x in h)
                votes = Counter(key(h) for h, _, _, _ in hits).most_common(2)
                if len(votes) == 1 or votes[0][1] > votes[1][1]:
                    top = [x for x in hits if key(x[0]) == votes[0][0]]
                    # 'contradicted' needs proof that the tag is a NARRATOR of this very book; a tag author the
                    # catalogue has never heard of proves nothing (the tags may simply be wrong).
                    if any(author_matches(n, authors) or any(author_matches(a, [n]) for a in authors)
                           for _, _, _, narrs in top for n in narrs):
                        h, ser, ct, _ = top[0]
                        return Verdict("contradicted", h, ser, ct)
        # Open Library only when Audible could not be asked at all. It cannot tell authors from narrators
        # (some records list narrators as authors), so a search that ran and found nothing must stay "unknown".
        if self.ol and not search_ok:
            try:
                got = self.ol(authors[0], title)
            except Exception as e:
                log.warning("Open Library check failed (%s)", e)
                got = None
            if got:
                return Verdict("confirmed", [got[0]])
        return Verdict("unknown", [])
