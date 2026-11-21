"""Multi-book torrents ("packs").

A pack is downloaded exactly as the torrent lays it out, into audiobookStaging/<torrent name>.
Once it has finished, each book gets its own symlink in the library:

    Audiobooks/<author>/[<series>/]<Title>/<original file>   ->  ../../../../audiobookStaging/<torrent>/<file>

The links are created on the NAS itself over SSH (the CIFS mount cannot make symlinks) and are
relative, so they work wherever the share is mounted. Nothing in the torrent is moved or renamed.

SSH linking is opt-in (NAS_SSH_HOST). Without it the plan is saved to ~/.local/state/
audiobook-watcher/plans/ and logged, and can be applied later with --link-plan.
"""
import json
import logging
import os
import re
import shlex
import subprocess
import time
import urllib.error
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

from textutil import clean_name, safe_component, sim, tidy_title

import history

log = logging.getLogger("audiobook-watcher.packs")
STATE_DIR = Path.home() / ".local/state/audiobook-watcher"
PLANS = STATE_DIR / "plans"
AUDIO_EXT = {".m4b", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wav"}
BOOK_EXT = {".m4b", ".m4a"}          # one file == one whole book
STAGING_REL = "audiobookStaging"     # staging folder, relative to the share root
LIBRARY_REL = "Audiobooks"


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------
@dataclass
class Unit:
    """One book found inside a torrent."""
    kind: str            # "file" (a single .m4b) or "dir" (a folder of tracks)
    path: str            # path inside the torrent, relative to its root folder
    series: str = ""
    number: str = ""
    title: str = ""


_NUM = re.compile(r"^(?P<series>.*?)[\s._-]*(?P<num>\d+[A-Za-z]?)\s*[-–:.]\s*(?P<title>\S.*)$")


def parse_unit(kind: str, path: str) -> Unit:
    """'Argeneau 04 - Tall, Dark & Hungry.m4b' -> series Argeneau, number 04, title 'Tall, Dark & Hungry'."""
    stem = Path(path).stem if kind == "file" else Path(path).name
    s = clean_name(stem)
    m = _NUM.match(s)
    if not m:
        return Unit(kind, path, "", "", tidy_title(s))
    series = re.sub(r"[\s,]*(?:book|bk|vol\.?|volume|#)$", "", m.group("series").strip(" -_.,"), flags=re.I)
    return Unit(kind, path, series, m.group("num"), tidy_title(m.group("title")))


def _parts_of_one_book(stems) -> bool:
    """'Part 1', 'Part 2', ... differ only by digits: that is one book split up, not a pack."""
    flat = [re.sub(r"\d+", "", s).lower() for s in stems]
    return all(sim(a, b) >= 0.9 for a, b in combinations(flat, 2))


def detect_books(files) -> list:
    """Return one Unit per book when the torrent holds several, else [].

    - two or more .m4b/.m4a files with different titles  -> each file is a book
    - two or more top-level folders of audio tracks       -> each folder is a book
    Loose tracks at the top level, or a mix of both, are treated as a single book (old behaviour)."""
    audio = [p for p, _ in files if Path(p).suffix.lower() in AUDIO_EXT]
    singles = [p for p in audio if Path(p).suffix.lower() in BOOK_EXT]
    if len(singles) >= 2:
        if _parts_of_one_book([Path(p).stem for p in singles]):
            return []
        return [parse_unit("file", p) for p in sorted(singles)]
    if any(len(Path(p).parts) == 1 for p in audio):
        return []
    tops = sorted({Path(p).parts[0] for p in audio})
    return [parse_unit("dir", d) for d in tops] if len(tops) >= 2 else []


def common_series(units) -> str:
    """The series name shared by (nearly) all units, or ''."""
    names = [u.series for u in units if u.series]
    if len(names) < max(2, round(0.8 * len(units))):
        return ""
    key, count = Counter(n.lower() for n in names).most_common(1)[0]
    if count < 0.8 * len(units):
        return ""
    return next(n for n in names if n.lower() == key)


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------
@dataclass
class PackPlan:
    author: str                      # library folder name
    author_display: str = ""
    series: str = ""
    books: list = field(default_factory=list)   # [(Unit, title)]
    notes: list = field(default_factory=list)


def _unsafe(link: str, target: str) -> bool:
    """True if a link/target pair could escape its place: control chars, '..' inside the link,
    '..' anywhere in the target after its leading '../' run, or empty path components."""
    if re.search(r"[\x00-\x1f]", link + target):
        return True
    if ".." in link.split("/") or "" in link.split("/"):
        return True
    rest = target
    while rest.startswith("../"):
        rest = rest[3:]
    return ".." in rest.split("/") or "" in rest.split("/")


def build_specs(plan: PackPlan, root: str):
    """[(link, target)] as share-root-relative paths. Raises ValueError if anything looks unsafe."""
    base = [LIBRARY_REL, safe_component(plan.author)] + ([safe_component(plan.series)] if plan.series else [])
    if not all(base):
        raise ValueError("empty author/series component")
    specs, seen = [], set()
    for unit, title in plan.books:
        t = safe_component(title)
        if not t:
            raise ValueError(f"empty title for {unit.path!r}")
        if unit.kind == "file":
            folder = base + [t]
            link = "/".join(folder + [safe_component(Path(unit.path).name)])
        else:
            folder = base
            link = "/".join(base + [t])
        target = "../" * len(folder) + "/".join([STAGING_REL, root, unit.path])
        if _unsafe(link, target):
            raise ValueError(f"unsafe path: {link!r} -> {target!r}")
        place = "/".join(folder).lower() if unit.kind == "file" else link.lower()   # the BOOK's location
        if place in seen:
            raise ValueError(f"two books map to the same place: {place!r}")
        seen.add(place)
        specs.append((link, target))
    return specs


def write_plan(infohash: str, specs) -> Path:
    PLANS.mkdir(parents=True, exist_ok=True)
    p = PLANS / f"{infohash}.tsv"
    p.write_text("".join(f"{l}\t{t}\n" for l, t in specs))
    return p


def read_plan(path: Path):
    return [tuple(line.rstrip("\n").split("\t", 1)) for line in Path(path).read_text().splitlines() if line.strip()]


# --------------------------------------------------------------------------
# Linking on the NAS (over SSH)
# --------------------------------------------------------------------------
_SCRIPT = r'''set -u
cd __ROOT__ || { echo "RESULT made=0 kept=0 bad=1"; exit 1; }
[ -d Audiobooks ] || { echo "not the share root" >&2; echo "RESULT made=0 kept=0 bad=1"; exit 1; }
APPLY=__APPLY__; made=0; kept=0; bad=0
while IFS="$(printf '\t')" read -r link target; do
    [ -n "$link" ] || continue
    case "$link" in Audiobooks/*) ;; *) echo "REFUSED $link"; bad=$((bad+1)); continue ;; esac
    case "$link" in */../*|*/..) echo "REFUSED $link"; bad=$((bad+1)); continue ;; esac
    case "$target" in ../*) ;; *) echo "REFUSED target $target"; bad=$((bad+1)); continue ;; esac
    if [ -L "$link" ]; then
        if [ -e "$link" ]; then echo "kept    $link"; kept=$((kept+1)); else echo "BROKEN  $link"; bad=$((bad+1)); fi
        continue
    fi
    if [ -e "$link" ]; then echo "BLOCKED a real file is in the way: $link"; bad=$((bad+1)); continue; fi
    rel="$target"
    while :; do case "$rel" in ../*) rel="${rel#../}" ;; *) break ;; esac; done
    if [ ! -e "$rel" ]; then echo "MISSING target for $link ($rel)"; bad=$((bad+1)); continue; fi
    echo "link    $link"
    if [ "$APPLY" = 1 ]; then
        if mkdir -p "$(dirname "$link")" && ln -s "$target" "$link" && [ -e "$link" ]; then :; else
            echo "FAILED  $link"; bad=$((bad+1)); continue
        fi
    fi
    made=$((made+1))
done <<'PLAN_EOF'
__PLAN__
PLAN_EOF
echo "RESULT made=$made kept=$kept bad=$bad"
'''


class NasLinker:
    def __init__(self, host: str, root: str, key: str):
        self.host, self.root, self.key = host, root, key

    def script(self, specs, apply: bool) -> str:
        lines = []
        for link, target in specs:
            for s in (link, target):
                if "\t" in s or "\n" in s or "PLAN_EOF" in s:
                    raise ValueError(f"cannot send path: {s!r}")
            lines.append(f"{link}\t{target}")
        return (_SCRIPT.replace("__ROOT__", shlex.quote(self.root))
                .replace("__APPLY__", "1" if apply else "0").replace("__PLAN__", "\n".join(lines)))

    def run(self, specs, apply: bool) -> dict:
        proc = subprocess.run(
            ["ssh", "-i", self.key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
             "-o", "ConnectTimeout=15", self.host, "sh -s"],
            input=self.script(specs, apply), capture_output=True, text=True, timeout=180)
        out = proc.stdout.splitlines()
        m = next((re.match(r"RESULT made=(\d+) kept=(\d+) bad=(\d+)", l) for l in reversed(out)
                  if l.startswith("RESULT")), None)
        res = {"lines": [l for l in out if not l.startswith("RESULT")], "stderr": proc.stderr.strip(),
               "rc": proc.returncode, "ok": False, "made": 0, "kept": 0, "bad": 0}
        if m:
            res.update(made=int(m[1]), kept=int(m[2]), bad=int(m[3]), ok=(int(m[3]) == 0))
        return res


def linker_from_env():
    host = os.environ.get("NAS_SSH_HOST", "").strip()
    if not host:
        return None
    return NasLinker(host, os.environ.get("NAS_ROOT", "/mnt/Thea-Server/thea"),
                     os.path.expanduser(os.environ.get("NAS_SSH_KEY", "~/.ssh/truenas_claude")))


# --------------------------------------------------------------------------
# Tracking until the download finishes, then linking
# --------------------------------------------------------------------------
class PackTracker:
    MAX_TRIES = 4

    def __init__(self, tr, linker, post=None, state: Path = STATE_DIR / "packs.json", resolver=None, intake=None):
        """resolver(root_name, base_dir, files, units) -> (specs, books, skipped) for packs whose authors are
        only known from the downloaded files' tags (see track_mixed)."""
        self.tr, self.linker, self.post, self.state = tr, linker, post, state
        self.resolver, self.intake = resolver, intake
        try:
            self.jobs = json.loads(state.read_text())
        except (OSError, ValueError):
            self.jobs = {}

    def _save(self):
        try:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.jobs, indent=1))
            tmp.replace(self.state)
        except OSError as e:
            log.warning("could not save pack state: %s", e)

    def track(self, infohash: str, name: str, specs, books, author_display: str):
        plan_file = write_plan(infohash, specs)
        self.jobs[infohash] = {"name": name, "specs": specs, "books": books, "author": author_display,
                               "plan": str(plan_file), "tries": 0, "next": 0}
        self._save()
        log.info("pack %r: %d links will be made once it finishes downloading (plan: %s)",
                 name, len(specs), plan_file)

    def track_mixed(self, infohash: str, name: str, units):
        """A pack whose books may have different authors: it stays whole in staging, and each book is linked
        under its own author once the files can be read."""
        self.jobs[infohash] = {"name": name, "mixed": True, "units": units, "specs": [], "books": [],
                               "author": "", "plan": "", "tries": 0, "next": 0}
        self._save()
        log.info("pack %r (%d books, authors unknown): each book is linked under its own author once it "
                 "finishes downloading", name, len(units))

    def tick(self):
        changed = False
        for h in list(self.jobs):
            j = self.jobs[h]
            if time.time() < j["next"]:
                continue
            history.focus(h)
            try:
                verdict = self._advance(h, j)
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, subprocess.SubprocessError) as e:
                j["tries"] += 1
                j["next"] = time.time() + 60
                log.warning("pack %r: %s (try %d/%d)", j["name"], e, j["tries"], self.MAX_TRIES)
                verdict = "drop" if j["tries"] >= self.MAX_TRIES else "keep"
            if verdict == "drop":
                log.warning("pack %r: giving up; apply later with: audiobook_watcher.py --link-plan %s --apply",
                            j["name"], j["plan"])
            if verdict in ("done", "drop"):
                del self.jobs[h]
            changed = True
        if changed:
            self._save()

    def _advance(self, h, j):
        got = self.tr.call("torrent-get", ids=[h], fields=["percentDone", "downloadDir", "files"])["torrents"]
        if not got:
            log.warning("pack %r is no longer in Transmission; no links made", j["name"])
            return "done"
        if got[0]["percentDone"] < 1:
            j["next"] = time.time() + 5
            return "keep"
        if j.get("mixed") and not j["specs"]:
            if self.resolver is None:
                log.warning("pack %r finished but no resolver is set; nothing linked", j["name"])
                return "done"
            specs, books, skipped = self.resolver(j["name"], Path(got[0]["downloadDir"]),
                                                  [f["name"] for f in got[0]["files"]], j["units"])
            for what, why in skipped:
                log.warning("pack %r: no link for %s (%s); it stays in staging", j["name"], what, why)
            log.info("pack %r: %d of %d books resolved to an author", j["name"], len(books), len(j["units"]))
            history.record(h, j["name"], "pack-books", f"{len(books)} of {len(j['units'])} books resolved from their tags",
                           linked=[f"{b.get('author', '')} / {b['rel']}" for b in books],
                           skipped=[f"{w} ({why})" for w, why in skipped])
            if not specs:
                return "done"
            j["specs"], j["books"] = [list(s) for s in specs], books
            j["plan"] = str(write_plan(h, specs))
            self._save()
        if self.linker is None:
            log.warning("pack %r finished. NAS linking is off (no NAS_SSH_HOST), so no links yet. "
                        "Apply with: audiobook_watcher.py --link-plan %s --apply", j["name"], j["plan"])
            return "done"
        res = self.linker.run([tuple(s) for s in j["specs"]], apply=True)
        if not res["ok"]:
            j["tries"] += 1
            j["next"] = time.time() + 60
            log.warning("pack %r: linking reported problems (try %d/%d): %s %s", j["name"], j["tries"],
                        self.MAX_TRIES, "; ".join(l for l in res["lines"] if l.split()[0].isupper()), res["stderr"])
            return "drop" if j["tries"] >= self.MAX_TRIES else "keep"
        log.info("pack %r: %d links created, %d already there", j["name"], res["made"], res["kept"])
        if self.post:
            for b in j["books"]:
                self.post.track(h, b["rel"], b["title"], b.get("author") or j["author"], stage="scan")
        if j.get("mixed") and self.intake:
            self.intake.send(h, j["name"], [{"title": b["title"], "author": b.get("author", "")} for b in j["books"]])
        return "done"
