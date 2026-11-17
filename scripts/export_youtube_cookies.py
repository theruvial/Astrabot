"""Save your browser's YouTube login as cookies.txt for the music and clip cogs.

Some videos (age-restricted ones, or any video when YouTube suspects a bot)
only play for a signed-in viewer. yt-dlp can use a cookies file for that.
This script copies just the YouTube and Google cookies out of a browser
you are already signed in to, and writes them to the file named by
"cookiesFile" in config.json (default cookies.txt).

Run from the Astrabot folder:

    py -3.14 scripts\\export_youtube_cookies.py                  (Firefox)
    py -3.14 scripts\\export_youtube_cookies.py --browser edge
    py -3.14 scripts\\export_youtube_cookies.py --profile default-release

Tips:
* Firefox is the dependable choice on Windows. Chrome and Edge lock their
  cookie files while running and, since 2024, encrypt them in a way yt-dlp
  often can not read; if one fails, use Firefox or the manual method in
  docs/YOUTUBE_COOKIES.md.
* Use a throwaway Google account if you can. Cookies are a login: anyone
  with the file is signed in as you, and YouTube may flag an account that
  a bot uses heavily.
* The bot reads the file fresh on every request, so no restart is needed.
  YouTube rotates cookies now and then; just run this again if playback
  starts failing with "Sign in to confirm you're not a bot".

The file is a credential. It is git-ignored; never commit or share it.
Only counts and cookie names are printed, never values.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Only these sites' cookies are kept; everything else in the browser is left behind.
KEEP_DOMAINS = ("youtube.com", "google.com")
# If none of these are present the browser is not signed in to YouTube.
LOGIN_COOKIES = ("SAPISID", "__Secure-3PAPISID", "__Secure-3PSID", "LOGIN_INFO")
TEST_VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def wanted(domain: str) -> bool:
    """True for youtube.com / google.com and their subdomains."""
    bare = (domain or "").lstrip(".").lower()
    return any(bare == d or bare.endswith("." + d) for d in KEEP_DOMAINS)


def signed_in(names) -> bool:
    return any(name in LOGIN_COOKIES for name in names)


def cookies_path() -> str:
    try:
        with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
            setting = json.load(f).get("cookiesFile") or "cookies.txt"
    except (OSError, ValueError):
        setting = "cookies.txt"
    return setting if os.path.isabs(setting) else os.path.join(ROOT, setting)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Export YouTube cookies for yt-dlp.")
    parser.add_argument("--browser", default="firefox",
                        help="firefox (default), edge, chrome, brave, ...")
    parser.add_argument("--profile", default=None,
                        help="browser profile name or folder; default is the browser's main one")
    parser.add_argument("--out", default=None, help="where to write (default: cookiesFile in config.json)")
    args = parser.parse_args(argv)

    from yt_dlp.cookies import YoutubeDLCookieJar, extract_cookies_from_browser

    out = args.out or cookies_path()
    try:
        jar = extract_cookies_from_browser(args.browser, args.profile)
    except Exception as e:  # yt-dlp raises plain errors for locked/encrypted stores
        print(f"Could not read cookies from {args.browser}: {e}")
        print("Close the browser and try again, or try --browser firefox "
              "(see docs/YOUTUBE_COOKIES.md for the manual method).")
        return 1

    kept = YoutubeDLCookieJar()
    names = []
    for cookie in jar:
        if wanted(cookie.domain):
            kept.set_cookie(cookie)
            names.append(cookie.name)

    if not names:
        print(f"No YouTube or Google cookies found in {args.browser}"
              f"{' profile ' + args.profile if args.profile else ''}. Sign in to YouTube there first.")
        return 1
    if not signed_in(names):
        print(f"Found {len(names)} cookies but none of them is a login, so "
              f"{args.browser} does not look signed in to YouTube. Nothing written.")
        return 1

    kept.save(out, ignore_discard=True, ignore_expires=True)
    print(f"Wrote {len(names)} cookies to {out}")

    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "cookiefile": out, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(TEST_VIDEO, download=False)
        print(f"Test lookup worked with the cookies: {info.get('title')!r}")
    except Exception as e:
        print(f"Cookies saved, but a test lookup failed: {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
