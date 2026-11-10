"""Tests for closing audiobook requests when Audiobookshelf has the book.

No real Audiobookshelf: the client is a fake, plus one local test server
for the HTTP client.
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp import web

from cogs.audiobooks_library import (
    AudiobookshelfClient,
    AudiobookshelfError,
    LibraryWatcher,
    build_library_watcher,
    find_library_matches,
    match_library_item,
)

NOW = "2026-11-09T12:00:00+00:00"
T_NOW = 1794225600  # 2026-11-09 12:00 UTC


def req(i, name, author="", status="In Progress", created=NOW):
    return {"id": i, "bookName": name, "author": author,
            "status": status, "createdAt": created}


def item(i, title, author="", added=T_NOW + 600):
    return {"id": i, "title": title, "author": author, "addedDate": added}


class MatchTests(unittest.TestCase):
    def test_title_and_author_match(self):
        self.assertTrue(match_library_item(
            req(1, "The Ever King", "LJ Andrews"),
            item("a", "The Ever King", "L.J. Andrews")))

    def test_a_different_book_does_not(self):
        self.assertFalse(match_library_item(
            req(1, "The Ever King", "LJ Andrews"),
            item("a", "The Ever Queen", "LJ Andrews")))

    def test_a_book_added_long_before_the_request_does_not_close_it(self):
        self.assertFalse(match_library_item(
            req(1, "The Ever King", "LJ Andrews"),
            item("a", "The Ever King", "LJ Andrews", added=T_NOW - 86400)))

    def test_only_in_progress_requests_are_closed(self):
        items = [item("a", "The Ever King", "LJ Andrews")]
        for status in ("Requested", "Fulfilled"):
            self.assertEqual(find_library_matches(
                [req(1, "The Ever King", "LJ Andrews", status=status)], items), [])
        self.assertEqual(len(find_library_matches(
            [req(1, "The Ever King", "LJ Andrews")], items)), 1)

    def test_ambiguous_matches_are_left_alone(self):
        items = [item("a", "Sample Story", "A Writer"), item("b", "Sample Story", "A Writer")]
        self.assertEqual(find_library_matches([req(1, "Sample Story", "A Writer")], items), [])
        one_item = [item("a", "Sample Story", "A Writer")]
        two_requests = [req(1, "Sample Story", "A Writer"), req(2, "Sample Story", "A Writer")]
        self.assertEqual(find_library_matches(two_requests, one_item), [])


class FakeDb:
    def __init__(self, rows):
        self.rows = rows

    def list_requests(self):
        return self.rows


class FakeCog:
    def __init__(self, rows):
        self.db = FakeDb(rows)
        self.closed = []
        self.config = {}

    async def admin_fulfilled(self, request_id):
        self.closed.append(request_id)
        return {"id": request_id}, True, None


class FakeClient:
    def __init__(self, items=None, error=None):
        self.items = items or []
        self.error = error
        self.calls = 0

    async def list_items(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.items


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class WatcherTests(unittest.TestCase):
    def make(self, rows, client, dry_run):
        cog = FakeCog(rows)
        return cog, LibraryWatcher(cog, client, dry_run=dry_run)

    def test_dry_run_changes_nothing(self):
        cog, watcher = self.make([req(1, "Sample Story", "A Writer")],
                                 FakeClient([item("a", "Sample Story", "A Writer")]), True)
        self.assertEqual(len(run(watcher.run_once())), 1)
        self.assertEqual(cog.closed, [])

    def test_live_closes_the_request_through_the_mark_fulfilled_path(self):
        cog, watcher = self.make([req(1, "Sample Story", "A Writer")],
                                 FakeClient([item("a", "Sample Story", "A Writer")]), False)
        run(watcher.run_once())
        self.assertEqual(cog.closed, [1])

    def test_no_in_progress_requests_means_no_library_call(self):
        client = FakeClient([item("a", "Sample Story", "A Writer")])
        cog, watcher = self.make([req(1, "Sample Story", "A Writer", status="Requested")],
                                 client, False)
        run(watcher.run_once())
        self.assertEqual(client.calls, 0)
        self.assertEqual(cog.closed, [])

    def test_an_unreachable_library_closes_nothing_and_does_not_raise(self):
        cog, watcher = self.make([req(1, "Sample Story", "A Writer")],
                                 FakeClient(error=AudiobookshelfError("unreachable")), False)
        self.assertEqual(run(watcher.run_once()), [])
        self.assertEqual(cog.closed, [])

    def test_off_unless_enabled_with_a_url_and_key(self):
        cog = FakeCog([])
        self.assertIsNone(build_library_watcher(cog))
        cog.config = {"library": {"enabled": True, "url": "http://x"}}
        self.assertIsNone(build_library_watcher(cog))  # no api key


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.seen = []

        async def libraries(request):
            self.seen.append(("libraries", request.headers.get("Authorization")))
            return web.json_response({"libraries": [
                {"id": "lib-books", "mediaType": "book"},
                {"id": "lib-pods", "mediaType": "podcast"}]})

        async def items(request):
            self.seen.append((request.match_info["lib"], dict(request.query)))
            return web.json_response({"results": [{
                "id": "li_1", "addedAt": (T_NOW + 5) * 1000,
                "media": {"metadata": {"title": "Sample Story", "authorName": "A Writer"}}}]})

        async def locked(request):
            return web.Response(status=401)

        app = web.Application()
        app.router.add_get("/api/libraries", libraries)
        app.router.add_get("/api/libraries/{lib}/items", items)
        app.router.add_get("/locked/api/libraries", locked)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def test_lists_book_libraries_only_and_converts_milliseconds(self):
        client = AudiobookshelfClient(self.base, "secret-key")
        result = await client.list_items()
        self.assertEqual(result, [{"id": "li_1", "title": "Sample Story",
                                   "author": "A Writer", "addedDate": T_NOW + 5}])
        self.assertEqual(self.seen[0], ("libraries", "Bearer secret-key"))
        self.assertEqual(self.seen[1][0], "lib-books")  # the podcast library is skipped
        self.assertEqual(self.seen[1][1]["sort"], "addedAt")

    async def test_a_fixed_library_id_skips_the_library_lookup(self):
        client = AudiobookshelfClient(self.base, "k", library_id="lib-books")
        await client.list_items()
        self.assertEqual([s[0] for s in self.seen], ["lib-books"])

    async def test_a_rejected_key_is_a_clear_error(self):
        client = AudiobookshelfClient(self.base + "/locked", "bad")
        with self.assertRaises(AudiobookshelfError) as caught:
            await client.list_items()
        self.assertIn("401", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
