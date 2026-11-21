"""A running log of the last N books the watcher handled, for troubleshooting later.

One entry per torrent (keyed by info-hash): when it arrived, how it was placed and why, what ffprobe read from the
audio file, what the catalogue said, whether anything moved, and the explanation lines the watcher logged for it.
Stored as JSON (~/.local/state/audiobook-watcher/recent.json); the newest N entries are kept.
View it with:  audiobook_watcher.py --recent [N]
Never holds secrets: only names, paths, tags and decisions.
"""
import json
import logging
import time
from pathlib import Path

STATE = Path.home() / ".local/state/audiobook-watcher/recent.json"
LIMIT, MAX_EVENTS = 30, 60

_h = None        # the configured History; record() does nothing until configure() is called


class History:
    def __init__(self, path=STATE, limit=LIMIT):
        self.path, self.limit, self.current = Path(path), limit, None
        try:
            self.entries = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.entries = {}

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.entries, indent=1, ensure_ascii=False))
            tmp.replace(self.path)
        except OSError:
            pass                       # history must never break the watcher

    def record(self, key, name=None, step="", msg="", **data):
        if not key:
            return
        now = time.time()
        e = self.entries.setdefault(key, {"hash": key, "name": name or "", "first": now, "events": []})
        if name:
            e["name"] = name
        e["updated"] = now
        ev = {"t": now, "step": step, "msg": msg}
        if data:
            ev["data"] = data
        e["events"] = (e["events"] + [ev])[-MAX_EVENTS:]
        for k in sorted(self.entries, key=lambda k: self.entries[k]["updated"])[:max(0, len(self.entries) - self.limit)]:
            del self.entries[k]
        self._save()

    def recent(self, n=LIMIT):
        return sorted(self.entries.values(), key=lambda e: e["updated"], reverse=True)[:n]

    def render(self, n=LIMIT):
        out = []
        for e in self.recent(n):
            out.append(f"=== {e['name']}   [{e['hash'][:10]}]  first seen {time.strftime('%Y-%m-%d %H:%M', time.localtime(e['first']))}")
            for ev in e["events"]:
                line = f"  {time.strftime('%H:%M:%S', time.localtime(ev['t']))}  {ev['step']:<11} {ev['msg']}"
                out.append(line.rstrip())
                for k, v in (ev.get("data") or {}).items():
                    out.append(f"                       {k}: {v}")
            out.append("")
        return "\n".join(out) or "(nothing recorded yet)"


class _Capture(logging.Handler):
    """Keeps the watcher's own explanation lines for whichever book is being worked on."""
    def emit(self, record):
        if _h is not None and _h.current:
            _h.record(_h.current, step=record.levelname.lower(), msg=record.getMessage())


def configure(path=STATE, limit=LIMIT, loggers=()):
    global _h
    _h = History(path, limit)
    for name in loggers:
        lg = logging.getLogger(name)
        if not any(isinstance(x, _Capture) for x in lg.handlers):
            lg.addHandler(_Capture(level=logging.INFO))
    return _h


def record(key, name=None, step="", msg="", **data):
    if _h is not None:
        _h.record(key, name, step, msg, **data)


def focus(key):
    """Lines logged by the verifier/pack tracker from now on belong to this book."""
    if _h is not None:
        _h.current = key
