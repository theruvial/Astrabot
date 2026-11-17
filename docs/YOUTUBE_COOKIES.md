# YouTube cookies for the music and clip cogs

Most videos play without a login. Age-restricted videos, and any video when
YouTube decides the bot looks automated ("Sign in to confirm you're not a
bot"), need a signed-in session. yt-dlp gets one from a `cookies.txt` file.

The cogs already use it: `cogs/music.py` looks for the file named by
`cookiesFile` in `config.json` (default `cookies.txt` in the Astrabot
folder). It tries without cookies first and retries with them, and it reads
the file fresh each time, so **replacing the file needs no restart**.

## Easiest: export from Firefox

1. Sign in to YouTube in Firefox (a throwaway Google account is wise: the
   cookies are a login, and heavy bot use can get an account flagged).
2. From the Astrabot folder:

       py -3.14 scripts\export_youtube_cookies.py

   Options: `--browser edge`, `--profile <name>`, `--out <path>`.
3. It keeps only YouTube and Google cookies, writes the file, and does a test
   lookup. It prints counts, never cookie values.

Chrome and Edge often fail on Windows (the cookie file is locked while the
browser runs, and newer versions use encryption yt-dlp can not read). If so,
use Firefox or the manual method.

## Manual method (any browser)

1. Open a private/incognito window and sign in to YouTube.
2. In that same window, go to `https://www.youtube.com/robots.txt` (a page
   that does not refresh the cookies).
3. With a cookie-export extension (for example "Get cookies.txt LOCALLY"),
   export the cookies for `youtube.com` in Netscape format.
4. **Close the private window** so YouTube does not rotate them, and save
   the result as `cookies.txt` in the Astrabot folder.

## Keeping it working

- When playback starts failing with a sign-in or bot message, run the script
  again.
- `cookies.txt` is a credential and is git-ignored. Never commit or share it.
- The JS runtime matters too: recent YouTube needs Deno (or Node) for
  yt-dlp's signature step. The music cog uses Deno if installed, else Node.
