"""Tests for matching audiobook requests to torrents.

No network or Transmission needed: the client is a fake.
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp import web

from cogs.audiobooks_torrents import (
    TorrentWatcher,
    TransmissionClient,
    TransmissionError,
    find_matches,
    match_torrent,
)

NOW = "2026-11-09T12:00:00+00:00"
T_NOW = 1794225600  # 2026-11-09 12:00 UTC


def req(i, name, author="", status="Requested", created=NOW):
    return {"id": i, "bookName": name, "author": author,
            "status": status, "createdAt": created}


def tor(i, name, added=T_NOW + 60):
    return {"id": i, "name": name, "percentDone": 0.5, "addedDate": added}


class MatchTests(unittest.TestCase):
    def test_title_and_author_in_name(self):
        self.assertTrue(match_torrent(
            req(1, "The Ever King", "LJ Andrews"),
            tor(1, "LJ Andrews - The Ever Seas 01 - The Ever King (Unabridged) [m4b]")))

    def test_punctuation_and_case_do_not_matter(self):
        self.assertTrue(match_torrent(
            req(1, "The Ever King", "LJ Andrews"),
            tor(1, "the.ever.king.-.andrews.MP3")))

    def test_other_book_in_series_does_not_match(self):
        self.assertFalse(match_torrent(
            req(1, "The Ever King", "LJ Andrews"),
            tor(1, "LJ Andrews - The Ever Queen")))

    def test_short_title_does_not_match_longer_one(self):
        self.assertFalse(match_torrent(req(1, "Dune"), tor(1, "Dune Messiah audiobook")))
        self.assertTrue(match_torrent(req(1, "Dune"), tor(1, "Dune (Unabridged) mp3")))

    def test_author_makes_a_longer_name_match(self):
        self.assertTrue(match_torrent(
            req(1, "Dune", "Frank Herbert"),
            tor(1, "Frank Herbert - Dune - Full Cast Edition")))

    def test_bare_audible_id_never_matches(self):
        self.assertFalse(match_torrent(req(1, "B0CLD8MBT6"), tor(1, "B0CLD8MBT6")))

    def test_torrent_added_long_before_request_is_ignored(self):
        old = T_NOW - 7 * 86400
        self.assertFalse(match_torrent(
            req(1, "The Ever King", "LJ Andrews"),
            tor(1, "LJ Andrews - The Ever King", added=old)))

    def test_torrent_added_just_before_request_still_counts(self):
        self.assertTrue(match_torrent(
            req(1, "The Ever King", "LJ Andrews"),
            tor(1, "LJ Andrews - The Ever King", added=T_NOW - 1800)))


class FindMatchesTests(unittest.TestCase):
    def test_only_requested_status_is_considered(self):
        rows = [req(1, "The Ever King", "LJ Andrews", status="In Progress")]
        self.assertEqual(find_matches(rows, [tor(1, "LJ Andrews - The Ever King")]), [])

    def test_ambiguous_request_is_skipped(self):
        rows = [req(1, "The Ever King", "LJ Andrews")]
        torrents = [tor(1, "LJ Andrews - The Ever King [m4b]"),
                    tor(2, "LJ Andrews - The Ever King [mp3]")]
        self.assertEqual(find_matches(rows, torrents), [])

    def test_one_torrent_for_two_requests_is_skipped(self):
        rows = [req(1, "The Ever King", "LJ Andrews"),
                req(2, "The Ever King", "LJ Andrews")]
        self.assertEqual(find_matches(rows, [tor(1, "LJ Andrews - The Ever King")]), [])

    def test_clean_match_is_returned(self):
        rows = [req(1, "The Ever King", "LJ Andrews"),
                req(2, "The Mist Thief", "LJ Andrews")]
        got = find_matches(rows, [tor(7, "LJ Andrews - The Ever King")])
        self.assertEqual([(r["id"], t["id"]) for r, t in got], [(1, 7)])


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def list_requests(self):
        return self.rows


class FakeCog:
    def __init__(self, rows):
        self.db = FakeDB(rows)
        self.bot = None
        self.fulfilled = []

    async def admin_fulfill(self, request_id):
        self.fulfilled.append(request_id)
        return {"id": request_id}, True, None


class FakeClient:
    def __init__(self, torrents=None, error=None):
        self.torrents = torrents or []
        self.error = error

    async def list_torrents(self):
        if self.error:
            raise TransmissionError(self.error)
        return self.torrents


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class ClientTests(unittest.TestCase):
    """The real client against a fake Transmission on a local port."""

    async def _serve(self, handler, check):
        app = web.Application()
        app.router.add_post("/transmission/rpc", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            return await check(f"http://127.0.0.1:{port}/transmission/rpc")
        finally:
            await runner.cleanup()

    def test_session_token_handshake_and_login(self):
        seen = []

        async def handler(request):
            seen.append((request.headers.get("X-Transmission-Session-Id"),
                         request.headers.get("Authorization", "")[:6]))
            if request.headers.get("X-Transmission-Session-Id") != "tok123":
                return web.Response(status=409,
                                    headers={"X-Transmission-Session-Id": "tok123"})
            return web.json_response({"result": "success", "arguments": {
                "torrents": [{"id": 1, "name": "A Book", "percentDone": 1,
                              "addedDate": 5}]}})

        async def check(url):
            return await TransmissionClient(url, "me", "pw").list_torrents()

        got = run(self._serve(handler, check))
        self.assertEqual(got[0]["name"], "A Book")
        self.assertEqual(seen[0][0], "")
        self.assertEqual(seen[1][0], "tok123")
        self.assertEqual(seen[1][1], "Basic ")

    def test_wrong_login_raises(self):
        async def handler(request):
            return web.Response(status=401)

        async def check(url):
            return await TransmissionClient(url, "me", "bad").list_torrents()

        with self.assertRaises(TransmissionError):
            run(self._serve(handler, check))

    def test_unreachable_raises(self):
        with self.assertRaises(TransmissionError):
            run(TransmissionClient("http://127.0.0.1:1/transmission/rpc",
                                   "me", "pw", timeout=2).list_torrents())


class WatcherTests(unittest.TestCase):
    def _watcher(self, dry_run, client=None):
        cog = FakeCog([req(1, "The Ever King", "LJ Andrews")])
        client = client or FakeClient([tor(7, "LJ Andrews - The Ever King")])
        return cog, TorrentWatcher(cog, client, dry_run=dry_run)

    def test_dry_run_changes_nothing(self):
        cog, watcher = self._watcher(dry_run=True)
        self.assertEqual(len(run(watcher.run_once())), 1)
        self.assertEqual(cog.fulfilled, [])

    def test_live_run_marks_in_progress(self):
        cog, watcher = self._watcher(dry_run=False)
        run(watcher.run_once())
        self.assertEqual(cog.fulfilled, [1])

    def test_transmission_down_does_nothing(self):
        cog, watcher = self._watcher(False, FakeClient(error="unreachable"))
        self.assertEqual(run(watcher.run_once()), [])
        self.assertEqual(cog.fulfilled, [])


if __name__ == "__main__":
    unittest.main()
