"""Small text/path helpers shared by the watcher, the pack planner and the Audiobookshelf client."""
import difflib
import re


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def safe_component(s: str) -> str:
    """Make one path component safe. Input may come from torrents, the web or an LLM."""
    s = re.sub(r"[\x00-\x1f/\\:*?\"<>|]", " ", s or "")
    s = re.sub(r"\s+", " ", s).strip().lstrip(". ")
    return s[:120].rstrip(". ")


_BRACKET = re.compile(r"[\[\(][^\[\]\(\)]*[\]\)]")
_NOISE = re.compile(r"m4b|mp3|flac|unabridged|abridged|audiobook|retail|kbps|\b\d+k\b|^\W*\d{4}\W*$"
                    r"|read by|narrated|\bepub\b", re.I)


def clean_name(name: str) -> str:
    s = re.sub(r"\.(m4b|mp3|zip|rar|flac)$", "", name, flags=re.I)
    if s.count(" ") < 2 and (s.count(".") >= 2 or s.count("_") >= 2):
        s = s.replace("_", " ").replace(".", " ")
    s = _BRACKET.sub(lambda m: "" if _NOISE.search(m.group(0)) else m.group(0), s)
    s = re.sub(r"\b(unabridged|abridged|audiobook)\b", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" -_.,")


def tidy_title(t: str) -> str:
    t = re.sub(r"\s*[-–]\s*\d+$", "", t.strip())          # trailing ' - 2'
    t = re.sub(r"^(?:(?:book|bk|vol\.?|volume|#)\s*0*\d+|0*\d{1,2})\s*[-:–.]\s+", "", t, flags=re.I)
    t = t.strip(" -_.,")
    if t and (t.islower() or t.isupper()):
        t = t.title()
    return t
