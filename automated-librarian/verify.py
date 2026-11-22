"""After a single-book download finishes, compare the file's own tags with where it was filed.

  * filed under an author the tags do not name  -> work out the right place from the tags and have
    TRANSMISSION move it (torrent-set-location, move=true), so it keeps seeding from the new place;
  * staged (the name was not enough)            -> same, so a clear tag files it;
  * tags missing / unclear / no confident place -> leave it exactly where it is and say why.

Files are never moved by hand. A move onto an existing book folder is refused. Runs before the
Audiobookshelf scan, which is then handed the final location.
"""
import json
import logging
import time
import urllib.error
from pathlib import Path

import history
import tags as tagmod

log = logging.getLogger("automated-librarian.verify")
STATE = Path.home() / ".local/state/automated-librarian/verify.json"
POLL_SECS, MOVE_WAIT_SECS, MAX_ERRORS = 10, 120, 30
# Folders the user fills on purpose (a series or collection of mixed authors): never move a book out of them.
EXEMPT_AUTHORS = {"mixedauthors"}


class Verifier:
    def __init__(self, tr, lib_root: Path, staging: Path, decide_fn, final_fn, display_fn, post=None,
                 probe=tagmod.probe, state=STATE, move=True, confirm=None, series_fn=None):
        """decide_fn(name) -> Decision|None; final_fn(decision) -> Path; display_fn(folder) -> 'James Hunter'."""
        self.tr, self.root, self.staging = tr, Path(lib_root), Path(staging)
        self.decide, self.final, self.display = decide_fn, final_fn, display_fn
        self.post, self.probe, self.state, self.move = post, probe, state, move
        self.series_fn = series_fn or (lambda author, series: series)
        self.confirm = confirm        # (tag authors, title) -> (status, catalogue authors); see bookcheck.py
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
    def track(self, infohash, name, kind, author="", author_display="", title="", multi=False, strong=False):
        """kind: 'placed' (filed in the library) or 'staged' (in staging).
        strong: the torrent NAME itself named this author (not inferred from a series folder or a model)."""
        self.jobs[infohash] = {"hash": infohash, "name": name, "kind": kind, "author": author, "author_display": author_display,
                               "title": title, "multi": multi, "strong": strong, "errs": 0, "next": 0}
        self._save()
        log.info("will check the tags of %s once it finishes downloading", name)

    def tick(self):
        changed = False
        for h in list(self.jobs):
            j = self.jobs[h]
            if time.time() < j.get("next", 0):
                continue
            history.focus(h)          # lines logged from here on belong to this book
            try:
                verdict = self._advance(h, j)
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
                j["errs"] += 1
                j["next"] = time.time() + 30
                changed = True
                if j["errs"] < MAX_ERRORS:
                    log.warning("%s: %s (will retry)", j["name"], e)
                    continue
                log.warning("giving up checking %s (%s)", j["name"], e)
                verdict = "drop"
            except Exception as e:
                log.error("%s: unexpected error, leaving it where it is: %s", j["name"], e)
                verdict = "drop"
            if verdict in ("done", "drop"):
                del self.jobs[h]
                changed = True
        if changed:
            self._save()

    # -- the work ---------------------------------------------------------
    def _advance(self, h, j):
        got = self.tr.call("torrent-get", ids=[h],
                           fields=["percentDone", "downloadDir", "files"])["torrents"]
        if not got:
            log.warning("%s is no longer in Transmission; not checking tags", j["name"])
            return "drop"
        t = got[0]
        if t["percentDone"] < 1:
            j["next"] = time.time() + POLL_SECS
            return "keep"
        names = [f["name"] for f in t["files"]]
        audio = sorted(n for n in names if n.lower().endswith(tagmod.AUDIO_EXT))
        base = Path(t["downloadDir"])
        multi = bool(names) and "/" in names[0]
        book_dir = base / names[0].split("/")[0] if multi else base
        if not audio:
            log.info("%s: no audio file to read tags from", j["name"])
            return self._handoff(j, book_dir, j["title"], j["author_display"])
        if len(audio) > 1:
            many = [self.probe(base / n) for n in audio[:40]]
            albums = {tagmod.norm(x.album) for x in many if x and x.album}
            people = {frozenset(tagmod.norm(a) for a in x.authors) for x in many if x and x.authors}
            if len(albums) > 1 or len(people) > 1:
                log.warning("%s: its files carry %d albums / %d author sets, so it looks like SEVERAL books; "
                            "not moving it (a multi-book torrent belongs in staging with links)",
                            j["name"], len(albums), len(people))
                return "done"
        tg = self.probe(base / audio[0])
        history.record(h, j["name"], "ffprobe", "tags read from the downloaded file" if tg else "ffprobe gave nothing",
                       file=audio[0], audio_files=len(audio),
                       title=tg.title if tg else None, authors=tg.authors if tg else None,
                       album=tg.album if tg else None, filed_under=j["author"] or "(staging)")
        if not tg or not tg.authors:
            log.warning("%s: no usable author tag (%s); leaving it where it is", j["name"], base / audio[0])
            return self._handoff(j, book_dir, j["title"], j["author_display"])

        placed = j["kind"] == "placed"
        if placed and j["author"].lower() in EXEMPT_AUTHORS:
            log.info("%s: filed under %r on purpose; tags are not checked", j["name"], j["author"])
            return self._handoff(j, book_dir, j["title"], j["author_display"])
        if placed and tagmod.author_matches(j["author_display"] or self.display(j["author"]), tg.authors):
            log.info("%s: tags agree with the folder (%s)", j["name"], " / ".join(tg.authors))
            return self._handoff(j, book_dir, j["title"], j["author_display"])

        if placed and j.get("strong"):
            # Tags often carry the NARRATOR in the artist field (e.g. Harry Potter: 'Stephen Fry'), so when the
            # torrent name itself named the author, a disagreeing tag only earns a warning.
            log.warning("%s: tags name %s but the torrent name named %r; leaving it (could be a narrator tag)",
                        j["name"], tg.authors, j["author"])
            return self._handoff(j, book_dir, j["title"], j["author_display"])
        title = tg.title or j["title"]
        v = tuple(self.confirm(tg.authors, title)) if self.confirm else ("unknown", [])
        status, canon, series, ctitle = (v + ("", ""))[:4]
        history.record(h, j["name"], "catalogue", f"{status}", asked=f"{tg.authors} / {title!r}",
                       authors=canon, series=series, title=ctitle)
        if status == "unknown":
            log.warning("%s: tags name %s but no catalogue confirms who wrote %r; left where it is",
                        j["name"], tg.authors, title)
            return self._handoff(j, book_dir, j["title"], j["author_display"]) if placed else "done"
        if status == "contradicted":
            if placed:
                if tagmod.author_matches(j["author_display"] or self.display(j["author"]), canon):
                    log.info("%s: catalogue says %s, which is the folder's author; the tag %s is probably the "
                             "narrator", j["name"], canon, tg.authors)
                else:
                    log.warning("%s: tags say %s, catalogue says %s, folder is %r; left where it is",
                                j["name"], tg.authors, canon, j["author"])
                return self._handoff(j, book_dir, j["title"], j["author_display"])
            log.info("%s: staged; tags say %s but the catalogue says %s; using the catalogue", j["name"],
                     tg.authors, canon)
        first = canon[0]
        if placed and tagmod.author_matches(j["author_display"] or self.display(j["author"]), canon):
            log.info("%s: catalogue authors %s include the folder's author; leaving it", j["name"], canon)
            return self._handoff(j, book_dir, j["title"], j["author_display"])
        why = (f"tags name {tg.authors}, folder author is {j['author']!r}" if placed
               else f"staged; tags say {tg.authors} / {title!r}")
        d = self.decide(f"{first} - {ctitle or title}")
        if not d:
            log.warning("%s: %s, but there is no confident place for it; left where it is", j["name"], why)
            return self._handoff(j, book_dir, j["title"], j["author_display"]) if placed else "done"
        if d and series:          # layout is author/series/title; the catalogue knows the series
            d.series = self.series_fn(d.author, series)
        try:
            new = self.final(d)
        except ValueError as e:
            log.warning("%s: %s, but the new path is unusable (%s); left where it is", j["name"], why, e)
            return self._handoff(j, book_dir, j["title"], j["author_display"]) if placed else "done"
        target_dir = new            # the book's own folder (a multi-file torrent's root is renamed to match)
        if placed and d.author == j["author"]:
            log.info("%s: tags resolve to the same author folder; leaving it", j["name"])
            return self._handoff(j, book_dir, j["title"], j["author_display"])
        if placed and not (self.root / d.author).is_dir():
            log.warning("%s: %s, but that would create a new author folder %r; left where it is",
                        j["name"], why, d.author)
            return self._handoff(j, book_dir, j["title"], j["author_display"])
        if target_dir.exists() and any(target_dir.iterdir()):
            log.warning("%s: %s, but %s already has files; left where it is", j["name"], why, target_dir)
            return self._handoff(j, book_dir, j["title"], j["author_display"]) if placed else "done"
        if not self.move:
            log.warning("%s: %s -> WOULD MOVE to %s (moves are off)", j["name"], why, target_dir)
            return self._handoff(j, book_dir, j["title"], j["author_display"]) if placed else "done"

        log.warning("%s: %s -> moving via Transmission to %s", j["name"], why,
                    target_dir.relative_to(self.root) if self.root in target_dir.parents else target_dir)
        history.record(h, j["name"], "move", "moving via Transmission (torrent-set-location, move=true)",
                       **{"from": str(book_dir), "to": str(target_dir)})
        if not self._move(h, t, names, multi, book_dir, target_dir):
            history.record(h, j["name"], "move", "DID NOT COMPLETE; check it by hand")
            return "drop"
        history.record(h, j["name"], "move", "done; Transmission is re-checking the data at the new place")
        return self._handoff(j, target_dir, d.title, d.author_display or self.display(d.author), moved=True)

    def _move(self, h, t, names, multi, old_dir: Path, new: Path) -> bool:
        old_base = Path(t["downloadDir"])
        if multi:
            root = names[0].split("/")[0]
            location = new.parent
            expect = location / root
        else:
            location = new
            expect = location / names[0]
        location.mkdir(parents=True, exist_ok=True)
        self.tr.call("torrent-set-location", ids=[h], location=str(location), move=True)
        deadline = time.time() + MOVE_WAIT_SECS
        while time.time() < deadline:
            cur = self.tr.call("torrent-get", ids=[h], fields=["downloadDir", "error", "errorString"])["torrents"][0]
            if Path(cur["downloadDir"]) == location and expect.exists():
                break
            time.sleep(1)
        else:
            log.error("Transmission did not finish moving %s within %ds; check it by hand", old_dir, MOVE_WAIT_SECS)
            return False
        if multi and root != new.name:
            try:
                self.tr.call("torrent-rename-path", ids=[h], path=root, name=new.name)
            except Exception as e:
                log.warning("moved, but could not rename %r to %r (%s)", root, new.name, e)
        self.tr.call("torrent-verify", ids=[h])          # Transmission re-checks the data at the new place
        self._rmdirs(old_dir if not multi else old_base / root, stop=(self.root, self.staging))
        log.info("moved; Transmission now reports %s and is re-checking the data", location)
        return True

    @staticmethod
    def _rmdirs(d: Path, stop):
        """Remove the old, now-empty folder and any empty parents, never the library/staging roots."""
        stops = {Path(s).resolve() for s in stop}
        d = Path(d)
        while d.resolve() not in stops:
            try:
                d.rmdir()
            except OSError:
                return                       # not empty (or gone): leave it
            d = d.parent

    def _handoff(self, j, book_dir: Path, title, author_display, moved=False):
        """Pass a book that now sits in the library to Audiobookshelf; staged books just finish."""
        in_library = self.root.resolve() in Path(book_dir).resolve().parents
        history.record(j["hash"], j["name"], "final",
                       ("in the library at " + str(Path(book_dir).resolve().relative_to(self.root.resolve())))
                       if in_library else f"stays in staging at {book_dir}")
        if self.post and in_library:
            self.post.track(j["hash"], Path(book_dir).resolve().relative_to(
                self.root.resolve()).as_posix(), title or j["title"], author_display or j["author_display"])
        return "done"
