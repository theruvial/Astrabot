# automated-librarian

Runs on the Ubuntu server next to Transmission and Audiobookshelf (not on the bot's PC). Watches a folder for
`.torrent` files, works out where each book belongs (`Author/Series/Title`), adds it to Transmission, checks the
downloaded file's tags, links multi-book packs into the library over SSH on the NAS, asks Audiobookshelf to scan and
match, and tells Astrabot which books a torrent holds (contract: `docs/LIBRARIAN_INTAKE.md`).

Python 3.12, standard library only. `ffprobe` must be installed.

## Pieces
| File | Job |
|---|---|
| `automated_librarian.py` | main loop, torrent parsing, placement (heuristics, Open Library, model fallback), Transmission RPC |
| `bookcheck.py` | asks the Audible catalogue (through Audiobookshelf) who wrote a book |
| `tags.py`, `verify.py` | reads the file's tags after download; moves a misfiled book through Transmission |
| `packs.py` | multi-book torrents: plan, per-book author from tags, symlinks over SSH |
| `abs_client.py` | Audiobookshelf scan and match |
| `intake.py` | tells Astrabot what a torrent contains |
| `history.py` | running log of the last 30 books (`--recent`) |

## Configuration
Environment file at `~/.config/automated-librarian/env` (mode 600, never committed). Names only, no values:
`AUDIOBOOKSHELF_URL`, `AUDIOBOOKSHELF_TOKEN`, `ABS_PROVIDER`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`
(or `ANTHROPIC_API_KEY`), `NAS_SSH_HOST`, `NAS_ROOT`, `NAS_SSH_KEY`, `INTAKE_URL`, `INTAKE_SECRET`, `VERIFY_MOVE`.

## Tests
`cd automated-librarian && for t in tests/test_*.py; do python3 "$t"; done`

## Troubleshooting
`python3 automated_librarian.py --recent` shows the last 30 books handled: how each was placed and why, the tags
ffprobe read, the catalogue's answer and any move.

## Running and updating (on the server)
The user service runs straight from this folder in a clone of the repo (`~/repos/Astrabot`). The installed unit is
`~/.config/systemd/user/automated-librarian.service` (a copy of `automated-librarian.service` here).
`./update.sh` fetches, runs the tests on the incoming code in a scratch worktree, and only if they pass pulls it and
restarts the service. The clone follows whichever branch is checked out; it should be on `master`.
