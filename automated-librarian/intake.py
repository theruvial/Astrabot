"""Tell Astrabot which books a torrent contains (contract: Astrabot docs/LIBRARIAN_INTAKE.md).

Best-effort: a down bot never blocks a download. Unsent calls are kept in a state file and retried.
Enabled only when INTAKE_URL and INTAKE_SECRET are both set. The secret is never logged.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("automated-librarian.intake")
STATE = Path.home() / ".local/state/automated-librarian/intake.json"
MAX_BOOKS, MAX_TRIES, RETRY_SECS = 200, 20, 60


class Intake:
    def __init__(self, url: str, secret: str, state: Path = STATE, timeout: float = 10.0):
        self.url, self.secret, self.state, self.timeout = url.rstrip("/"), secret, state, timeout
        self.queue = self._load()

    def _load(self):
        try:
            return json.loads(self.state.read_text())
        except (OSError, ValueError):
            return []

    def _save(self):
        try:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.queue, indent=1))
            tmp.replace(self.state)
        except OSError as e:
            log.warning("could not save state: %s", e)

    def _call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data, {
            "Authorization": "Bearer " + self.secret, "Content-Type": "application/json",
            "User-Agent": "automated-librarian/0.1"}, method=method)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read() or b"{}")

    def health(self):
        return self._call("GET", "/intake/health")

    def send(self, infohash: str, name: str, books: list):
        """books: [{"title", "author", "series"?}]. Queued first, so a crash cannot lose it."""
        books = [{k: v for k, v in b.items() if v} for b in books if b.get("title")][:MAX_BOOKS]
        if not books:
            return
        self.queue.append({"hash": infohash, "name": name, "books": books, "tries": 0, "next": 0})
        self._save()
        self.tick()

    def tick(self):
        changed = False
        for item in list(self.queue):
            if time.time() < item["next"]:
                continue
            try:
                res = self._call("POST", "/intake/books", {
                    "torrent": {"hash": item["hash"], "name": item["name"]}, "books": item["books"]})
            except urllib.error.HTTPError as e:
                if e.code in (400, 403):          # retrying cannot fix these
                    log.error("%s: bot refused the call (HTTP %d); dropping", item["name"], e.code)
                    self.queue.remove(item)
                    changed = True
                    continue
                self._failed(item, f"HTTP {e.code}")
                changed = True
                continue
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, ValueError) as e:
                self._failed(item, str(e))
                changed = True
                continue
            summary = {}
            for b in res.get("books", []):
                summary[b.get("status", "?")] = summary.get(b.get("status", "?"), 0) + 1
            log.info("%s: told Astrabot about %d book(s)%s -> %s", item["name"], len(item["books"]),
                     " [bot dry run]" if res.get("dryRun") else "", summary or "no reply detail")
            self.queue.remove(item)
            changed = True
        if changed:
            self._save()

    def _failed(self, item, why):
        item["tries"] += 1
        if item["tries"] >= MAX_TRIES:
            log.warning("%s: giving up telling Astrabot (%s)", item["name"], why)
            self.queue.remove(item)
        else:
            item["next"] = time.time() + RETRY_SECS
            log.warning("%s: Astrabot unreachable (%s); will retry", item["name"], why)


def from_env():
    url = os.environ.get("INTAKE_URL", "").strip()
    secret = os.environ.get("INTAKE_SECRET", "").strip()
    return Intake(url, secret) if url and secret else None
