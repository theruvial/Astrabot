# Librarian intake: how the Automated Librarian talks to the bot

This is the contract between Astrabot (Windows PC, 192.168.4.80) and the
Automated Librarian (`~/automated-librarian` on the Ubuntu server, 192.168.4.74).
The bot side lives in `cogs/audiobooks_intake.py`. The librarian side is not in
this repo.

## Why it exists

A request for one book of a multi-book torrent ("pack") never matched the
torrent, because the pack's name has none of the book titles in it. The
librarian already knows each book's real title and author when it files the
torrent, so it tells the bot. The bot links each book to a waiting request:

1. When the librarian adds a torrent to Transmission, it sends the bot the
   torrent's hash and name and the books inside it.
2. Each book that matches exactly one `Requested` request moves that request
   to `In Progress` and stores the torrent hash on it. The Discord post updates.
   **No email is sent at this step.**
3. When Audiobookshelf reports the book, the library watcher marks the request
   `Fulfilled`. That sends the requester's one email and deletes the Discord
   post. This happens whether or not step 2 ran: a request that is still
   `Requested` is closed directly.

Books that match no request are ignored. Books Thea adds that nobody asked for
need nothing on the requests side.

## The call

`POST http://192.168.4.80:13385/intake/books`

Headers:

```
Authorization: Bearer <shared secret>
Content-Type: application/json
```

Body:

```json
{
  "torrent": {"hash": "<Transmission hashString, hex>", "name": "<torrent name>"},
  "books": [
    {"title": "Love Bites", "author": "Lynsay Sands", "series": "Argeneau"},
    {"title": "A Quick Bite", "author": "Lynsay Sands", "series": "Argeneau"}
  ]
}
```

- A single-book torrent sends a list with one book.
- `title` is required. `author` is strongly recommended, because the match needs
  the author's surname unless the title alone is unambiguous. `series` is
  optional and stored for later use.
- At most 200 books per call, and a body of at most 256 KB.
- Sending the same torrent again is safe. Books already linked come back as
  `already_linked`.

Response `200`:

```json
{
  "torrent": "<hash>",
  "dryRun": false,
  "books": [
    {"title": "Love Bites", "author": "Lynsay Sands", "requestId": 7, "status": "linked"},
    {"title": "A Quick Bite", "author": "Lynsay Sands", "requestId": null, "status": "no_request"}
  ]
}
```

| `status` | Meaning |
|---|---|
| `linked` | The request moved to In Progress and is tied to this torrent. |
| `would_link` | Same as `linked`, but the bot is in dry run, so nothing changed. |
| `already_linked` | The request was already In Progress for this torrent. |
| `no_request` | No waiting request matches this book. This is normal. |
| `ambiguous` | Several requests match this book, or one request matches several books. Left for Thea. |
| `already_in_progress`, `already_closed`, `not_found` | The request changed between matching and saving. Nothing was done. |

Other responses: `400` means the body is bad (the reason is in `error`), and
`403` means the caller was refused (the reason is in the bot's log, never in the
response).

`GET /intake/health` with the same header returns `{"ok": true, "dryRun": ...}`.
Use it to test the connection.

## Security rules (the bot enforces all of them)

- The intake uses its own port, 13385. **nginx must never proxy this port.**
- Any request carrying `X-Forwarded-For`, `X-Real-IP`, `Forwarded` or
  `X-Forwarded-Host`/`-Proto` is refused. nginx runs on the librarian's own
  server, so the bot cannot tell proxied public traffic apart by address alone.
- The peer address must be in `audiobooks.intake.allowed_ips` (default
  `["192.168.4.74"]`).
- The secret is compared in constant time. Without a secret, the intake does
  not start.
- Windows Firewall on the PC must allow inbound TCP 13385, ideally only from
  192.168.4.74.

## Turning it on

On the PC, in `config.json` under `audiobooks`:

```json
"intake": {
    "enabled": true,
    "dry_run": true,
    "host": "0.0.0.0",
    "port": 13385,
    "secret": "",
    "allowed_ips": ["192.168.4.74"]
}
```

Keep the secret out of `config.json` (it syncs to Google Drive). Set it as an
environment variable instead:

```powershell
setx AUDIOBOOKS_INTAKE_SECRET "<a long random value>"
```

Then restart the console so the bot picks it up. The librarian needs the same
value in its own config on the server.

Start with `dry_run: true`. The librarian's calls then return `would_link` and the
bot logs what it would do. Set it to `false` once the matches look right.
