"""Lay the CV out as a portfolio page: a hero, project cards, skill chips and
a timeline, instead of one long document.

Pure functions only, like portfolio_cv. The content still comes only from the
CV. This module reads the CV's shape (a name and contact lines at the top,
then `## ` sections, `### ` groups, and `**Title** - details` entries) and
decides how to show it. Every piece of text goes through
portfolio_cv.inline, which escapes it first, so a bad edit to the CV can not
inject HTML. If the CV does not have the usual shape, the page falls back to
the plain section-per-heading layout rather than failing.
"""

from __future__ import annotations

import html
import re

from cogs.portfolio_cv import inline, render_cv

_DASHES = "—–"
_SEP = "·"          # the middle dot the CV puts between details
_HEAD = re.compile(r"^\*\*(.+?)\*\*\s*(?:[" + _DASHES + r"]\s*)?(.*)$")
_LINK_ONLY = re.compile(r"^\[([^\]]+)\]\(([^()\s]*(?:\([^()\s]*\)[^()\s]*)*)\)$")
_ITALIC_ONLY = re.compile(r"^\*[^*]+\*$")
_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$")
_LABEL = re.compile(r"^\*\*(.+?)\*\*:?\s*(.*)$")
_AI_WORD = re.compile(r"\bAI\b")
_OWN_SITE = "theruvial.com"

# Shown in this order, whatever order the CV lists them in: a portfolio leads
# with the work. Sections the CV adds that are not listed here go last.
SECTION_ORDER = ("projects", "technical skills", "experience", "education")
NAV_NAMES = {"technical skills": "Skills"}


# --- reading the CV --------------------------------------------------------

def _split_sections(markdown: str):
    """(lines before the first `## `, [{title, lines}, ...])."""
    pre: list[str] = []
    sections: list[dict] = []
    current = None
    for raw in markdown.replace("\r\n", "\n").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            current = {"title": line[3:].strip(), "lines": []}
            sections.append(current)
        elif current is None:
            pre.append(line)
        else:
            current["lines"].append(line)
    return pre, sections


def _groups(lines: list[str]) -> list[dict]:
    """Group a section's lines: `### ` starts a group, `**Title** ...` starts
    an entry, and bullets or text attach to the entry above them."""
    groups: list[dict] = []
    group = None
    entry = None

    def need_group() -> dict:
        nonlocal group
        if group is None:
            group = {"title": None, "entries": []}
            groups.append(group)
        return group

    def need_entry() -> dict:
        nonlocal entry
        if entry is None:
            entry = {"head": None, "bullets": [], "paras": []}
            need_group()["entries"].append(entry)
        return entry

    for raw in lines:
        text = raw.strip()
        if not text:
            continue
        if text.startswith("### "):
            group = {"title": text[4:].strip(), "entries": []}
            groups.append(group)
            entry = None
        elif text.startswith("- "):
            need_entry()["bullets"].append(text[2:].strip())
        elif text.startswith("**"):
            entry = {"head": text.removesuffix("\\").strip(), "bullets": [], "paras": []}
            need_group()["entries"].append(entry)
        else:
            need_entry()["paras"].append(text.removesuffix("\\").strip())
    return groups


def _split_commas(text: str) -> list[str]:
    """Split on commas that are not inside (...) or [...]."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _split_dots(text: str) -> list[str]:
    return [p.strip() for p in text.split(_SEP) if p.strip()]


def _head_parts(head: str | None) -> tuple[str | None, list[str]]:
    """('Title' as HTML, [details split on the middle dot])."""
    if not head:
        return None, []
    match = _HEAD.match(head)
    if not match:
        return inline(head), []
    return inline(match.group(1)), _split_dots(match.group(2))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"


# --- small pieces ----------------------------------------------------------

def _chip(text: str) -> str:
    css = "chip ai" if _AI_WORD.search(text) else "chip"
    return f'<span class="{css}">{inline(text)}</span>'


def _chips(items: list[str]) -> str:
    if not items:
        return ""
    return '<div class="chips">' + "".join(_chip(i) for i in items) + "</div>"


def _bullets(items: list[str]) -> str:
    if not items:
        return ""
    return "<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>"


def _link_button(markup: str, css: str = "btn") -> str:
    """A link written as [label](url) as a button. inline() drops unsafe URLs,
    leaving plain words, so the replace only touches real, safe links."""
    out = inline(markup)
    return out.replace("<a ", f'<a class="{css}" ', 1) if out.startswith("<a ") else out


# --- entries ---------------------------------------------------------------

def _project_card(entry: dict) -> str:
    title, parts = _head_parts(entry["head"])
    sub = None
    if parts and not any(c in parts[0] for c in ",[") and not _ITALIC_ONLY.match(parts[0]):
        sub = parts.pop(0)
    chips: list[str] = []
    dates: list[str] = []
    links: list[str] = []
    for part in parts:
        if _LINK_ONLY.match(part):
            links.append(part)
        elif _ITALIC_ONLY.match(part):
            dates.append(part)
        elif "[" in part:
            chips.append(part)
        else:
            chips.extend(_split_commas(part))
    head = ""
    if title:
        when_text = f" {_SEP} ".join(dates)
        when = f'<span class="when">{inline(when_text)}</span>' if dates else ""
        subtitle = f'<p class="sub">{inline(sub)}</p>' if sub else ""
        head = f'<header class="card-head"><div><h4>{title}</h4>{subtitle}</div>{when}</header>'
    body = "".join(f"<p>{inline(p)}</p>" for p in entry["paras"])
    foot = ""
    if links:
        foot = '<div class="links">' + "".join(_link_button(l) for l in links) + "</div>"
    return f'<article class="card reveal">{head}{_chips(chips)}{body}{_bullets(entry["bullets"])}{foot}</article>'


def _step(entry: dict) -> str:
    """A timeline entry: a job, a school, or any other dated item."""
    title, parts = _head_parts(entry["head"])
    dates = [p for p in parts if _ITALIC_ONLY.match(p)]
    others = [p for p in parts if not _ITALIC_ONLY.match(p)]
    when = ""
    if len(dates) == 1:
        # One date: a pill beside the title. Several (two employers, say)
        # stay in order in the line under it so each date keeps its place.
        when = f'<span class="when">{inline(dates[0])}</span>'
        parts = others
    meta = f'<p class="meta">{inline(f" {_SEP} ".join(parts))}</p>' if parts else ""
    head = f'<div class="step-head"><h4>{title}</h4>{when}</div>' if title else ""
    body = "".join(f"<p>{inline(p)}</p>" for p in entry["paras"])
    return f'<article class="step reveal"><div class="step-card">{head}{meta}{body}{_bullets(entry["bullets"])}</div></article>'


def _skill(bullet: str) -> str:
    match = _LABEL.match(bullet)
    if not match:
        return f'<div class="skill reveal">{_chips([bullet])}</div>'
    label = match.group(1).strip().rstrip(":").strip()
    return (f'<div class="skill reveal"><h4>{inline(label)}</h4>'
            f'{_chips(_split_commas(match.group(2)))}</div>')


# --- sections --------------------------------------------------------------

def _projects(lines: list[str]) -> str:
    out = []
    for group in _groups(lines):
        ai = bool(group["title"] and _AI_WORD.search(group["title"]))
        title = f'<h3 class="reveal">{inline(group["title"])}</h3>' if group["title"] else ""
        cards = "".join(_project_card(e) for e in group["entries"])
        css = "group ai" if ai else "group"
        out.append(f'<div class="{css}">{title}<div class="grid">{cards}</div></div>')
    return "".join(out)


def _skills(lines: list[str]) -> str:
    bullets = [b for g in _groups(lines) for e in g["entries"] for b in e["bullets"]]
    return '<div class="skills">' + "".join(_skill(b) for b in bullets) + "</div>"


def _timeline(lines: list[str]) -> str:
    steps = "".join(_step(e) for g in _groups(lines) for e in g["entries"])
    return f'<div class="timeline">{steps}</div>'


_BUILDERS = {"projects": _projects, "technical skills": _skills}


def _section(section: dict) -> tuple[str, str, str]:
    """(nav id, nav label, HTML) for one `## ` section."""
    key = section["title"].lower()
    build = _BUILDERS.get(key, _timeline)
    sid = _slug(section["title"])
    markup = (f'<section id="{sid}" class="block"><h2 class="reveal"><span>{inline(section["title"])}</span></h2>'
              f'{build(section["lines"])}</section>')
    return sid, NAV_NAMES.get(key, section["title"]), markup


# --- the hero --------------------------------------------------------------

def _paragraphs(lines: list[str]) -> list[str]:
    paras: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line.strip().removesuffix("\\").strip())
        elif current:
            paras.append(" ".join(current))
            current = []
    if current:
        paras.append(" ".join(current))
    return paras


def _initials(name: str) -> str:
    name = re.sub(r"<(sub|sup)>.*?</\1>", "", name)      # pronouns and the like
    words = re.findall(r"[A-Za-z0-9]+", re.sub(r"<[^>]*>", "", name))
    return "".join(w[0] for w in words[:2]).upper() or "?"


def _contact(part: str, first: bool) -> str:
    link = _LINK_ONLY.match(part)
    if link:
        if _OWN_SITE in link.group(2).lower():
            return ""            # a link to this very page is noise
        return _link_button(part)
    if _EMAIL.match(part):
        address = html.escape(part, quote=True)
        css = "btn primary" if first else "btn"
        return f'<a class="{css}" href="mailto:{address}">{address}</a>'
    return _chip(part)


def _contact_buttons(pre: list[str]) -> list[str]:
    """The contact line(s) under the name, as buttons (email first)."""
    paras = _paragraphs([l for l in pre if not l.startswith("# ")])
    buttons: list[str] = []
    for para in paras[1:]:
        for part in _split_dots(para):
            piece = _contact(part, first=not buttons)
            if piece:
                buttons.append(piece)
    return buttons


def _closing(pre: list[str]) -> str:
    """A last call to action, using the same contact details as the hero."""
    buttons = _contact_buttons(pre)
    if not buttons:
        return ""
    return ('<section class="closing wrap reveal"><div class="closing-in"><h2>Get in touch</h2>'
            f'<div class="cta">{"".join(buttons)}</div></div></section>')


def _hero(pre: list[str], summary: list[str]) -> str:
    name_line = next((l for l in pre if l.startswith("# ")), "# ")
    name = inline(name_line[2:])
    paras = _paragraphs([l for l in pre if not l.startswith("# ")])
    role_parts = _split_dots(paras[0]) if paras else []
    role = inline(role_parts[0]).replace("<strong>", "").replace("</strong>", "") if role_parts else ""
    role_html = f'<p class="role">{role}</p>' if role else ""
    where = _chips(role_parts[1:])
    buttons = _contact_buttons(pre)
    lede = "".join(f'<p class="lede">{inline(p)}</p>' for p in _paragraphs(summary))
    return (
        '<header class="hero" id="top"><div class="aurora" aria-hidden="true"><i></i><i></i><i></i></div>'
        f'<div class="hero-in"><div class="mono" aria-hidden="true">{_initials(name)}</div>'
        f"{role_html}<h1>{name}</h1>{where}{lede}"
        f'<div class="cta">{"".join(buttons)}</div></div></header>'
    )


# --- the whole page body ---------------------------------------------------

def _render(markdown: str) -> str:
    pre, sections = _split_sections(markdown)
    summary = [s for s in sections if s["title"].lower() == "summary"]
    rest = [s for s in sections if s["title"].lower() != "summary"]

    def rank(section: dict) -> int:
        key = section["title"].lower()
        return SECTION_ORDER.index(key) if key in SECTION_ORDER else len(SECTION_ORDER)

    built = [_section(s) for s in sorted(rest, key=rank)]   # sorted() is stable
    name_text = next((l[2:] for l in pre if l.startswith("# ")), "")
    brand = _initials(inline(name_text))
    nav = "".join(f'<a href="#{sid}">{inline(label)}</a>' for sid, label, _ in built)
    body = "".join(markup for _, _, markup in built)
    summary_lines = summary[0]["lines"] if summary else []
    return (
        f'<nav class="top"><a class="brand" href="#top" aria-label="Top of page">{brand}</a>'
        f'<div class="links">{nav}</div></nav>'
        f"{_hero(pre, summary_lines)}"
        f'<main class="wrap">{body}</main>'
        f"{_closing(pre)}"
    )


def render_portfolio(markdown: str) -> str:
    """The CV as the portfolio page body (nav, hero and sections). If the CV
    has an unexpected shape the plain layout is used instead of an error."""
    try:
        return _render(markdown)
    except Exception:  # noqa: BLE001 -- a layout bug must never take the site down
        return '<main class="wrap plain">' + render_cv(markdown) + "</main>"
