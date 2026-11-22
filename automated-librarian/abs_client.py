"""Audiobookshelf integration for automated-librarian.

When a torrent the librarian filed finishes downloading, this:
  1. asks Audiobookshelf to scan the library,
  2. finds the new item by its Author/[Series/]Title path,
  3. searches the metadata provider using the title/author the librarian already worked out,
  4. applies the match only if a result agrees with that title and author.

A job survives restarts (state file). Nothing here can block downloads: any error just
leaves the book as Audiobookshelf's own scan found it.
"""
import difflib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("automated-librarian.abs")
STATE = Path.home() / ".local/state/automated-librarian/pending.json"
USER_AGENT = "automated-librarian/0.1"
SCAN_RETRY_SECS, SCAN_MAX_TRIES, MAX_ERRORS = 8, 15, 30
GATE_POLL_SECS = 5   # how often a finished book re-checks whether downloads have gone quiet


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def display_author(folder: str) -> str:
    """'jamesHunter' -> 'James Hunter', 'jamesSACorey' -> 'James SA Corey'."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", folder)
    return s[:1].upper() + s[1:]


class Abs:
    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token

    def call(self, method, path, params=None, body=None, timeout=30):
        url = self.url + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data, {
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json",
            "User-Agent": USER_AGENT}, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {"raw": raw.decode("utf-8", "replace")}

    def pick_library(self, root: Path, want_id: str = ""):
        """Find the book library that points at our Audiobooks folder."""
        libs = [l for l in self.call("GET", "/api/libraries").get("libraries", [])
                if l.get("mediaType") == "book"]
        if want_id:
            return next((l for l in libs if l["id"] == want_id), None)
        for l in libs:
            for f in l.get("folders", []):
                try:
                    if Path(f["fullPath"]).resolve() == root.resolve():
                        return l
                except OSError:
                    pass
        return libs[0] if len(libs) == 1 else None

    def scan(self, lib_id):
        self.call("POST", f"/api/libraries/{lib_id}/scan")

    def find_item(self, lib_id, rel: str):
        res = self.call("GET", f"/api/libraries/{lib_id}/items",
                        {"sort": "addedAt", "desc": 1, "limit": 60, "page": 0}).get("results", [])
        return next((i for i in res if i.get("relPath") == rel), None)

    def search(self, provider, title, author):
        out = self.call("GET", "/api/search/books",
                        {"title": title, "author": author, "provider": provider}, timeout=60)
        return out if isinstance(out, list) else []


def best_result(results, title: str, author: str):
    """Pick the provider result that agrees with our own title and author, else None."""
    best, best_score = None, 0.0
    for r in results:
        rt, ra = r.get("title") or "", r.get("author") or ""
        t_ok = norm(rt).startswith(norm(title)) or sim(rt, title) >= 0.8   # tolerate added subtitles
        a_ok = not ra or sim(ra, author) >= 0.6 or norm(author) in norm(ra) or norm(ra) in norm(author)
        if not (t_ok and a_ok):
            continue
        score = sim(rt, title) + (sim(ra, author) if ra else 0)
        if score > best_score:
            best, best_score = r, score
    return best


class PostProcessor:
    def __init__(self, abs_: Abs, tr, root: Path, provider="", override=True, library_id="", state=STATE,
                 settle=60.0):
        self.abs, self.tr, self.root = abs_, tr, root
        self.provider, self.override, self.want_lib = provider, override, library_id
        self.state, self.lib = state, None
        self.settle, self.idle_since, self.last_scan = settle, None, 0.0
        self.jobs = self._load()

    # -- persistence ------------------------------------------------------
    def _load(self):
        try:
            return json.loads(self.state.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self):
        try:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.jobs, indent=1))
            tmp.replace(self.state)
        except OSError as e:
            log.warning("could not save state: %s", e)

    # -- API --------------------------------------------------------------
    def track(self, infohash: str, rel: str, title: str, author: str, stage: str = "download"):
        """stage="scan" skips waiting for the download (used once pack links exist)."""
        key = infohash if stage == "download" else f"{infohash}:{rel}"
        self.jobs[key] = {"rel": rel, "title": title, "author": author, "stage": stage, "hash": infohash,
                          "added": time.time(), "tries": 0, "errs": 0, "next": 0}
        self._save()
        log.info("tracking %s%s, then scan + match in Audiobookshelf", rel,
                 " until it finishes" if stage == "download" else "")

    def tick(self):
        changed = False
        for h in list(self.jobs):
            j = self.jobs[h]
            if time.time() < j.get("next", 0):
                continue
            try:
                verdict = self._advance(h, j)
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
                j["errs"] += 1
                j["next"] = time.time() + 30
                if j["errs"] >= MAX_ERRORS:
                    log.warning("giving up on %s after repeated errors (%s)", j["rel"], e)
                    verdict = "drop"
                else:
                    log.warning("%s: %s (will retry)", j["rel"], e)
                    changed = True
                    continue
            except Exception as e:
                log.error("%s: unexpected error, dropping job: %s", j["rel"], e)
                verdict = "drop"
            if verdict in ("done", "drop"):
                del self.jobs[h]
            changed = True
        if changed:
            self._save()

    def _settled(self) -> bool:
        """True once nothing in Transmission has been downloading for `settle` seconds.
        Looks at ALL torrents (also ones added by hand); stopped or seeding torrents do not count."""
        rows = self.tr.call("torrent-get", fields=["status"])["torrents"]
        if any(r["status"] in (3, 4) for r in rows):          # 3 = queued to download, 4 = downloading
            self.idle_since = None
            return False
        if self.idle_since is None:
            self.idle_since = time.time()
            log.info("no downloads running; refreshing Audiobookshelf in %ds unless one starts", self.settle)
        return time.time() - self.idle_since >= self.settle

    def _advance(self, h, j):
        if j["stage"] == "download":
            got = self.tr.call("torrent-get", ids=[j.get("hash", h)], fields=["percentDone"])["torrents"]
            if not got:
                log.warning("%s is no longer in Transmission; not scanning", j["rel"])
                return "drop"
            if got[0]["percentDone"] < 1:
                return "keep"
            j["stage"], j["next"], j["errs"] = "scan", 0, 0
            log.info("%s finished downloading", j["rel"])
        if self.lib is None:
            self.lib = self.abs.pick_library(self.root, self.want_lib)
            if self.lib is None:
                log.warning("could not tell which Audiobookshelf library to use; set ABS_LIBRARY_ID")
                return "drop"
        lib_id = self.lib["id"]
        if not j.get("scanned"):
            if not self._settled():
                j["next"] = time.time() + GATE_POLL_SECS
                return "keep"
            if time.time() - self.last_scan > 30:             # one scan covers every book that just finished
                self.abs.scan(lib_id)
                self.last_scan = time.time()
                log.info("asked Audiobookshelf to scan library %r", self.lib.get("name"))
            j.update(scanned=True, next=time.time() + SCAN_RETRY_SECS, errs=0)
            return "keep"
        item = self.abs.find_item(lib_id, j["rel"])
        if not item:
            j["tries"] += 1
            if j["tries"] >= SCAN_MAX_TRIES:
                log.warning("Audiobookshelf never showed %s; check the library folder/path", j["rel"])
                return "drop"
            j["next"] = time.time() + SCAN_RETRY_SECS
            return "keep"
        provider = self.provider or self.lib.get("provider") or "audible"
        r = best_result(self.abs.search(provider, j["title"], j["author"]), j["title"], j["author"])
        if not r:
            log.warning("%s: no %s result agreed with %r by %r; left as scanned",
                        j["rel"], provider, j["title"], j["author"])
            return "done"
        body = {"provider": provider, "title": r["title"], "author": r.get("author") or j["author"],
                "overrideDefaults": self.override}
        if provider == "audible" and r.get("asin"):
            body["asin"] = r["asin"]
        res = self.abs.call("POST", f"/api/items/{item['id']}/match", body=body, timeout=60)
        log.info("%s: matched via %s -> %r by %r (updated=%s)", j["rel"], provider,
                 r["title"], r.get("author"), res.get("updated"))
        return "done"


def from_env(tr, root: Path):
    """Build a PostProcessor from AUDIOBOOKSHELF_* env vars, or None when no token is set."""
    token = os.environ.get("AUDIOBOOKSHELF_TOKEN", "").strip()
    if not token:
        return None
    url = os.environ.get("AUDIOBOOKSHELF_URL", "http://127.0.0.1:13378")
    return PostProcessor(Abs(url, token), tr, root,
                         provider=os.environ.get("ABS_PROVIDER", ""),
                         override=os.environ.get("ABS_OVERRIDE", "1") != "0",
                         library_id=os.environ.get("ABS_LIBRARY_ID", ""),
                         settle=float(os.environ.get("ABS_SETTLE_SECS", "60")))
