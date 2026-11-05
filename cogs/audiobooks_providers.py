"""Provider validation for audiobook-request links.

Only the five providers in the design document are accepted. For each
book we need *positive* audiobook evidence before a request is created:
an audiobook format label, a narrator credit, or a listening length.
Absence of that evidence fails that book -- never "probably an
audiobook".

Series handling (corrected 2026-10-29, partial -- NOT all-or-nothing):
books in a series that pass get requests; books that fail are skipped
and reported back to the user. Only a submission where *no* book passes
is a total failure.

This module is deliberately independent of discord.py so it can be
unit-tested with a fake fetcher. The default fetcher uses aiohttp.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlparse, urlunparse

AUDIOBOOK_INDICATORS = (
    "audible audiobook",
    "audiobook",
    "audio cd",
    "narrated by",
    "listening length",
    "listening time",
    "unabridged audiobook",
)

TRACKING_PARAMS = ("utm_source", "utm_medium", "utm_campaign", "utm_term",
                   "utm_content", "fbclid", "gclid", "si", "ref", "ref_",
                   # Amazon tracking / session params.
                   "tag", "dib", "dib_tag", "qid", "sr", "_encoding",
                   "psc", "smid", "linkCode", "linkId")

# host -> provider name; checked as exact host or a subdomain of it.
PROVIDERS = {
    "audible.com": "Audible",
    "audible.co.uk": "Audible",
    "audible.ca": "Audible",
    "audible.com.au": "Audible",
    "audible.de": "Audible",
    "audible.fr": "Audible",
    "audible.it": "Audible",
    "audible.es": "Audible",
    "audible.co.jp": "Audible",
    "open.spotify.com": "Spotify",
    "spotify.com": "Spotify",
    "books.apple.com": "Apple Books",
    "play.google.com": "Google Books",
    "books.google.com": "Google Books",
    "goodreads.com": "Goodreads",
    "www.goodreads.com": "Goodreads",
    "amazon.com": "Amazon",
    "amazon.co.uk": "Amazon",
    "amazon.ca": "Amazon",
    "amazon.com.au": "Amazon",
    "amazon.de": "Amazon",
    "amazon.fr": "Amazon",
    "amazon.it": "Amazon",
    "amazon.es": "Amazon",
    "amazon.co.jp": "Amazon",
    "amazon.in": "Amazon",
    "amazon.com.mx": "Amazon",
    "amazon.com.br": "Amazon",
}


def provider_for(url: str) -> str | None:
    """Return the provider name for a URL, or None if not allowlisted."""
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return None
    if host in PROVIDERS:
        return PROVIDERS[host]
    # Allow subdomains of an allowlisted host, e.g. www.audible.com.
    for known, name in PROVIDERS.items():
        if host.endswith("." + known):
            return name
    return None


def normalize_url(url: str) -> str:
    """HTTPS, no fragment, tracking params stripped, host lowercased."""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http(s) links are accepted.")
    query = "&".join(
        part for part in parsed.query.split("&")
        if part and part.split("=")[0] not in TRACKING_PARAMS
        and not part.split("=")[0].startswith("pd_rd_")
        and not part.split("=")[0].startswith("ref")
    )
    return urlunparse((
        "https",
        parsed.netloc.lower(),
        parsed.path or "/",
        parsed.params,
        query,
        "",
    ))


class _TextExtractor(HTMLParser):
    """Collect visible text plus title/meta content from a page."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title: str = ""
        self._in_title = False
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in ("script", "style"):
            self._skip = True
        if tag == "meta":
            attrs_d = dict(attrs)
            if attrs_d.get("property") in ("og:title", "og:audio", "og:type") \
                    or attrs_d.get("name") in ("title", "description"):
                if attrs_d.get("content"):
                    self.parts.append(attrs_d["content"])

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if self._skip:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title += text
        self.parts.append(text)


def extract_title_and_text(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    title = unescape(parser.title).strip()
    # Goodreads/Audible titles often carry a suffix after a separator.
    for sep in (" | ", " by ", " - Audible", " - Goodreads"):
        if sep in title:
            title = title.split(sep)[0].strip()
            break
    text = unescape(" ".join(parser.parts)).lower()
    return title, text


def amazon_title(html: str) -> str | None:
    """Clean book title from an Amazon product page.

    Amazon's <title> tag is noisy ("Dune: Frank Herbert: 978...:
    Amazon.com: Books"), while its og:title meta tag is usually just
    the book title. Prefer og:title; otherwise cut the <title> at
    the Amazon suffix.
    """
    match = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        html, flags=re.IGNORECASE)
    if not match:
        match = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
            html, flags=re.IGNORECASE)
    if match:
        title = unescape(match.group(1)).strip()
        if title:
            return title
    title, _ = extract_title_and_text(html)
    # Cut "…: Amazon.com: Books" / "… Amazon.co.uk" suffixes.
    cut = re.split(r"\s*:\s*Amazon\.", title)
    if cut:
        title = cut[0].strip()
    return title or None


def has_audiobook_evidence(text: str, provider: str, path: str) -> bool:
    """Positive-evidence check, with provider-specific path rules."""
    if provider == "Spotify":
        # Spotify audiobooks live under /audiobook/; a show/episode/track
        # is not an audiobook even if the page mentions the word.
        if not path.startswith("/audiobook/"):
            return False
        return True
    if provider == "Apple Books":
        if "/audiobook/" not in path:
            return False
        return True
    if provider == "Google Books":
        if "/store/audiobooks/" not in path:
            return False
        return True
    if provider == "Audible":
        # Audible sells audiobooks; a product/series page that resolves
        # with a title is enough, search pages are not.
        if "/search" in path or path in ("/", ""):
            return False
        return True
    if provider == "Amazon":
        # Amazon is scraped (unlike Audible): the page must be a
        # product page (/dp/ or /gp/product/) AND show an audiobook
        # edition — "Audible Audiobook" in the format selector, a
        # narrator credit, or a listening length. A print/ebook-only
        # page, a search page, or a non-book product page fails.
        # Note: a print book page that lists an Audible edition among
        # its formats passes, by design — the book has an audiobook.
        if "/dp/" not in path and "/gp/product/" not in path:
            return False
        return any(ind in text for ind in AUDIOBOOK_INDICATORS)
    # Goodreads and any other provider: need an audiobook edition label.
    return any(ind in text for ind in AUDIOBOOK_INDICATORS)


def is_series_url(provider: str, path: str) -> bool:
    if provider == "Goodreads":
        return path.startswith("/series/")
    if provider == "Audible":
        return "/series/" in path
    return False


def extract_series_books(html: str, provider: str) -> list[dict]:
    """Extract book links from a Goodreads series page, primary works only.

    Only Goodreads series pages are enumerated in this build (book/show
    links). Audible series pages vary too much to enumerate reliably
    from markup alone, so an Audible series link is treated as a single
    item whose title is the series title -- see check_link().

    "Primary works" is Goodreads' own distinction, shown on the series
    page as e.g. "3 primary works • 4 total works". The non-primary
    works are the extras that used to leak into requests: omnibus /
    collected editions, boxed sets, anthologies, and other collections.
    Goodreads lists those after the primary works, and collected
    editions are usually headed "Book 1-3", "Book 1-2", etc. -- a range,
    not a single book. So, in order:

    1. Cut the page at "Related series" so books from related series
       can never be picked up.
    2. Drop any book whose most recent "Book ..." heading is a range
       ("Book 1-3"). Those are collected editions, never a primary work.
    3. If the page states a primary-work count, keep only that many
       books, in series order. That drops trailing anthologies /
       collections even when they carry a single-book heading.
    4. If no count can be parsed, fall back to the range filter only
       (old behaviour, minus collected editions) rather than failing
       the whole series.

    Decimal-numbered entries ("Book 1.5") are kept: Goodreads series
    do count some of those among their primary works, and the count in
    step 3 is the authority on how many to keep, not the numbering.
    """
    if provider != "Goodreads":
        return []
    # 1. Related series live below the main list on the page.
    related = re.search(r"Related series", html, flags=re.IGNORECASE)
    if related:
        html = html[:related.start()]
    # Primary-work count from the series header, e.g.
    # "3 primary works • 4 total works".
    primary_count: int | None = None
    count_match = re.search(r"(\d+)\s+primary works", html)
    if count_match:
        try:
            primary_count = int(count_match.group(1))
        except ValueError:
            primary_count = None

    seen: set[str] = set()
    books: list[dict] = []
    # Scan headings and book links together, in document order, so each
    # book gets the "Book ..." heading that precedes it on the page.
    heading_re = re.compile(r"Book\s+(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?")
    link_re = re.compile(r'href="(/book/show/[^"#?]+)"[^>]*>([^<]{1,200})<')
    events: list[tuple[int, str, re.Match]] = []
    for match in heading_re.finditer(html):
        events.append((match.start(), "heading", match))
    for match in link_re.finditer(html):
        events.append((match.start(), "link", match))
    events.sort(key=lambda item: item[0])
    is_range = False
    for _, kind, match in events:
        if kind == "heading":
            is_range = match.group(2) is not None
            continue
        rel, name = match.groups()
        link = "https://www.goodreads.com" + rel
        if link in seen:
            continue
        seen.add(link)
        if is_range:
            # Collected edition spanning several books: not a primary work.
            continue
        books.append({"linkToBook": link, "bookName": unescape(name).strip()})
    # Fallback: bare /book/show/ links without a readable anchor title.
    if not books:
        for rel in re.findall(r'/book/show/(\d+)[^"\' ]*', html):
            link = f"https://www.goodreads.com/book/show/{rel}"
            if link not in seen:
                seen.add(link)
                books.append({"linkToBook": link, "bookName": ""})
    # 3. Primary-work count, if parsed, is authoritative.
    if primary_count is not None:
        books = books[:primary_count]
    return books


async def default_fetch(url: str, timeout: float = 10.0) -> str | None:
    """Fetch a page as text, or None on any failure (fail closed)."""
    import aiohttp

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        timeout_cfg = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
            async with session.get(url, headers=headers,
                                   allow_redirects=True) as resp:
                if resp.status != 200:
                    return None
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type and "application/json" not in content_type:
                    return None
                raw = await resp.read()
                if len(raw) > 5_000_000:
                    return None
                return raw.decode("utf-8", errors="ignore")
    except Exception:
        return None


def audible_title(path: str) -> str | None:
    """Title for an Audible link, without fetching anything.

    Audible has no regular books: an Audible product link IS an
    audiobook link, so there is nothing to scrape to validate. The
    title comes from the URL itself, e.g.
    /pd/Dune-Audiobook/B002V1OF70 -> "Dune".
    Returns None only for pages that are not a book at all (search,
    homepage), where there is no title to recover. A slug-less
    product URL (/pd/<ASIN>) falls back to the ASIN as the title,
    which can be edited later in the admin dashboard.
    """
    if "/search" in path or path in ("/", ""):
        return None
    parts = [part for part in path.split("/") if part]
    if not parts:
        return None
    # First part that is not a route word and not a bare ASIN is the
    # title slug (covers /pd/, /series/, and /web/audiobook/ URLs).
    for part in parts:
        if part in ("pd", "series", "web", "audiobook"):
            continue
        if re.fullmatch(r"[A-Z0-9]{10}", part):
            continue
        slug = part
        if slug.lower().endswith("-audiobook"):
            slug = slug[: -len("-audiobook")]
        title = slug.replace("-", " ").strip()
        if title:
            return title
    # Slug-less product URL: the ASIN is the only identifier available.
    return parts[-1]


def audible_slug_title(path: str) -> str | None:
    """Backwards-compatible alias for audible_title()."""
    return audible_title(path)


async def check_single(
    url: str, fetch=default_fetch
) -> dict | None:
    """Check one book link. Returns {"bookName", "linkToBook"} or None."""
    provider = provider_for(url)
    if provider is None:
        return None
    try:
        clean = normalize_url(url)
    except ValueError:
        return None
    path = urlparse(clean).path
    if provider == "Audible":
        # No scrape at all: Audible links are audiobook links by
        # definition, so the URL alone accepts the request.
        title = audible_title(path)
        if title is not None:
            return {"bookName": title, "linkToBook": clean}
        return None
    if provider == "Amazon":
        # Amazon IS scraped: the page must show an audiobook edition.
        html = await fetch(clean)
        if html is None:
            return None
        title = amazon_title(html)
        if not title:
            return None
        _, text = extract_title_and_text(html)
        if not has_audiobook_evidence(text, provider, path):
            return None
        return {"bookName": title, "linkToBook": clean}
    html = await fetch(clean)
    if html is None:
        return None
    title, text = extract_title_and_text(html)
    if not title:
        return None
    if not has_audiobook_evidence(text, provider, path):
        return None
    return {"bookName": title, "linkToBook": clean}


async def check_link(
    url: str, fetch=default_fetch
) -> dict:
    """Check a submitted link.

    Returns {"created": [...], "skipped": [...]} where created holds
    book dicts ready for insertion and skipped holds titles (or links)
    that had no audiobook. A total failure gives created == [].
    """
    provider = provider_for(url)
    if provider is None:
        return {"created": [], "skipped": [url], "error": "provider"}
    try:
        clean = normalize_url(url)
    except ValueError:
        return {"created": [], "skipped": [url], "error": "url"}
    path = urlparse(clean).path

    if provider == "Audible" and is_series_url(provider, path):
        # Audible series pages are not enumerated and not scraped:
        # the series link itself is one audiobook item.
        single = await check_single(clean, fetch=fetch)
        if single:
            return {"created": [single], "skipped": []}
        return {"created": [], "skipped": [url], "error": "audiobook"}

    if is_series_url(provider, path):
        html = await fetch(clean)
        if html is None:
            return {"created": [], "skipped": [url], "error": "fetch"}
        series_books = extract_series_books(html, provider)
        if not series_books:
            # Audible series pages are not enumerated (see above):
            # fall back to treating the series page itself as one item.
            single = await check_single(clean, fetch=fetch)
            if single:
                return {"created": [single], "skipped": []}
            return {"created": [], "skipped": [url], "error": "audiobook"}
        created: list[dict] = []
        skipped: list[str] = []
        for book in series_books:
            checked = await check_single(book["linkToBook"], fetch=fetch)
            if checked:
                created.append(checked)
            else:
                skipped.append(book["bookName"] or book["linkToBook"])
        return {"created": created, "skipped": skipped}

    single = await check_single(clean, fetch=fetch)
    if single:
        return {"created": [single], "skipped": []}
    return {"created": [], "skipped": [url], "error": "audiobook"}
