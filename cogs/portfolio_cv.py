"""Turn the CV (Markdown, kept in the public profile repo) into the body of
the portfolio page.

Pure functions only: no network, no files, no Discord. The cog fetches the
Markdown, calls these, and decides whether to publish. Everything from the
CV is HTML-escaped before the few Markdown marks the CV uses (headings,
bullets, bold, italics, links) are turned into tags, and links are limited
to http, https and mailto, so a bad edit to the CV can not inject script
into the live site.
"""

from __future__ import annotations

import difflib
import html
import re

# A CV missing any of these is probably a broken edit, not a real one.
REQUIRED_SECTIONS = ("Summary", "Technical Skills", "Experience", "Projects", "Education")

# The URL may hold one level of balanced parentheses, like a Wikipedia link.
_LINK = re.compile(r"\[([^\]]+)\]\(([^()\s]*(?:\([^()\s]*\)[^()\s]*)*)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])")
_SAFE_URL = re.compile(r"^(https?://|mailto:)", re.IGNORECASE)


def inline(text: str) -> str:
    """Escape first, then apply bold, italics and safe links."""
    out = html.escape(text.strip(), quote=True)

    def link(match: re.Match) -> str:
        label, url = match.group(1), html.unescape(match.group(2))
        if not _SAFE_URL.match(url):
            return label  # keep the words, drop the unsafe link
        return f'<a href="{html.escape(url, quote=True)}" rel="noopener">{label}</a>'

    out = _LINK.sub(link, out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    return _ITALIC.sub(r"<em>\1</em>", out)


def section_titles(markdown: str) -> list[str]:
    """The `## ` headings, in order."""
    return [line[3:].strip() for line in markdown.splitlines() if line.startswith("## ")]


def missing_sections(markdown: str) -> list[str]:
    """Required sections that are not in this CV."""
    have = {title.lower() for title in section_titles(markdown)}
    return [name for name in REQUIRED_SECTIONS if name.lower() not in have]


def render_cv(markdown: str) -> str:
    """The CV as an HTML fragment: one <section> per `## ` heading."""
    parts: list[str] = []
    state = {"list": False, "para": [], "section": False}

    def flush_para() -> None:
        if state["para"]:
            parts.append("<p>" + "<br>".join(state["para"]) + "</p>")
            state["para"] = []

    def close_list() -> None:
        if state["list"]:
            parts.append("</ul>")
            state["list"] = False

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush_para()
            close_list()
        elif line.startswith("# "):
            flush_para(); close_list()
            parts.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            flush_para(); close_list()
            if state["section"]:
                parts.append("</section>")
            parts.append(f"<section>\n<h2>{inline(line[3:])}</h2>")
            state["section"] = True
        elif line.startswith("### "):
            flush_para(); close_list()
            parts.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("- "):
            flush_para()
            if not state["list"]:
                parts.append("<ul>")
                state["list"] = True
            parts.append(f"<li>{inline(line[2:])}</li>")
        else:
            close_list()
            # A trailing backslash is Markdown for a line break.
            state["para"].append(inline(line.removesuffix("\\")))
            if line.endswith("\\"):
                continue
    flush_para(); close_list()
    if state["section"]:
        parts.append("</section>")
    return "\n".join(parts)


def summarize_change(old: str, new: str, limit: int = 12) -> list[str]:
    """The changed lines, short enough for a Discord message. Blank lines are
    ignored; each line is cut at 140 characters; at most `limit` lines."""
    def lines(text: str) -> list[str]:
        return [ln.strip() for ln in text.splitlines() if ln.strip()]

    out: list[str] = []
    for ln in difflib.unified_diff(lines(old), lines(new), lineterm="", n=0):
        if ln.startswith(("+++", "---", "@@")):
            continue
        out.append(ln if len(ln) <= 140 else ln[:137] + "...")
    if len(out) > limit:
        extra = len(out) - limit
        out = out[:limit] + [f"... and {extra} more changed lines"]
    return out


def looks_safe_to_publish(old: str, new: str) -> tuple[bool, str]:
    """Decide whether a changed CV may go on the site at all.

    Returns (ok, reason). When ok is False the cog keeps the last good page
    and tells Thea the reason, so a broken edit never replaces the live site.
    `old` is the CV behind the live page ("" the first time); `new` is the
    change under review.

    TODO(human): write the rules. Until they are written this refuses
    everything, which is the safe default. Candidates are a CV with no text,
    required sections gone (use `missing_sections(new)`), a big shrink
    against `old`, and raw HTML or script tags appearing in the Markdown.
    """
    return False, "publish rules are not written yet"
