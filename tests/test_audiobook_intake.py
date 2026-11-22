"""Tests for the librarian intake: the Ubuntu server's Automated Librarian tells
the bot which books a torrent holds, and the bot links them to requests.

No real librarian: calls come from the aiohttp test client on 127.0.0.1. The
secret here is a made-up test value, never the real one.
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp.test_utils import TestClient, TestServer

from cogs.audiobooks_db import AudiobooksDB
from cogs.audiobooks_intake import (
    IntakeServer,
    build_intake,
    check_caller,
    parse_payload,
    plan_links,
)

SECRET = "test-intake-secret"
LIBRARIAN = "192.168.4.74"
HASH = "abc123def456"
AUTH = {"Authorization": f"Bearer {SECRET}"}


def req(i, name, author="", status="Requested", torrent_hash=None):
    return {"id": i, "bookName": name, "author": author, "status": status,
            "createdAt": "2026-11-20T10:00:00+00:00", "torrentHash": torrent_hash}


def book(title, author=""):
    return {"title": title, "author": author, "series": ""}


class CallerTests(unittest.TestCase):
    def check(self, peer=LIBRARIAN, headers=None, secret=SECRET, allowed=(LIBRARIAN,)):
        return check_caller(peer, AUTH if headers is None else headers, secret, list(allowed))

    def test_the_librarian_with_the_secret_is_let_in(self):
        self.assertIsNone(self.check())

    def test_a_wrong_or_missing_secret_is_refused(self):
        self.assertEqual(self.check(headers={"Authorization": "Bearer nope"}), "bad_secret")
        self.assertEqual(self.check(headers={}), "bad_secret")
        self.assertEqual(self.check(headers={"Authorization": SECRET}), "bad_secret")

    def test_anything_through_nginx_is_refused_even_with_the_secret(self):
        # nginx runs on the librarian's own server, so the address alone proves nothing.
        for header in ("X-Forwarded-For", "X-Real-IP", "Forwarded"):
            self.assertEqual(self.check(headers={**AUTH, header: "203.0.113.9"}), "proxied")

    def test_other_addresses_are_refused(self):
        self.assertEqual(self.check(peer="192.168.4.80"), "address_not_allowed")
        self.assertEqual(self.check(allowed=()), "address_not_allowed")

    def test_no_configured_secret_refuses_everything(self):
        self.assertEqual(self.check(secret=""), "no_secret_configured")


class PayloadTests(unittest.TestCase):
    def test_a_good_payload_is_cleaned(self):
        payload, error = parse_payload({
            "torrent": {"hash": " ABC123 ", "name": "Argeneau 1-16"},
            "books": [{"title": " A Quick Bite ", "author": "Lynsay Sands"}]})
        self.assertIsNone(error)
        self.assertEqual(payload["torrent"], {"hash": "abc123", "name": "Argeneau 1-16"})
        self.assertEqual(payload["books"], [book("A Quick Bite", "Lynsay Sands")])

    def test_bad_payloads_are_rejected(self):
        for raw in ([], {"books": [book("x")]},
                    {"torrent": {"hash": ""}, "books": [book("x")]},
                    {"torrent": {"hash": "../etc"}, "books": [book("x")]},
                    {"torrent": {"hash": "abc"}, "books": []},
                    {"torrent": {"hash": "abc"}, "books": [{"author": "no title"}]}):
            payload, error = parse_payload(raw)
            self.assertIsNone(payload)
            self.assertTrue(error)


class PlanTests(unittest.TestCase):
    def test_one_book_of_a_pack_links_to_its_request(self):
        rows = [req(1, "Love Bites", "Lynsay Sands")]
        books = [book("A Quick Bite", "Lynsay Sands"), book("Love Bites", "Lynsay Sands"),
                 book("Single White Vampire", "Lynsay Sands")]
        results = plan_links(rows, books, HASH)
        self.assertEqual([r["status"] for r in results], ["no_request", "linked", "no_request"])
        self.assertEqual(results[1]["requestId"], 1)

    def test_only_requested_rows_are_linked(self):
        for status in ("In Progress", "Fulfilled"):
            results = plan_links([req(1, "Love Bites", "Lynsay Sands", status=status)],
                                 [book("Love Bites", "Lynsay Sands")], HASH)
            self.assertEqual(results[0]["status"], "no_request")

    def test_a_resend_reports_already_linked(self):
        rows = [req(1, "Love Bites", "Lynsay Sands", status="In Progress", torrent_hash=HASH)]
        results = plan_links(rows, [book("Love Bites", "Lynsay Sands")], HASH)
        self.assertEqual((results[0]["status"], results[0]["requestId"]), ("already_linked", 1))

    def test_ambiguous_matches_are_left_for_thea(self):
        two_requests = [req(1, "Love Bites", "Lynsay Sands"), req(2, "Love Bites", "Lynsay Sands")]
        self.assertEqual(plan_links(two_requests, [book("Love Bites", "Lynsay Sands")], HASH)[0]
                         ["status"], "ambiguous")
        two_books = [book("Love Bites", "Lynsay Sands"), book("Love Bites", "Lynsay Sands")]
        self.assertEqual([r["status"] for r in plan_links(
            [req(1, "Love Bites", "Lynsay Sands")], two_books, HASH)], ["ambiguous", "ambiguous"])

    def test_a_longer_title_in_the_pack_does_not_link(self):
        results = plan_links([req(1, "Twice", "Lynsay Sands")],
                             [book("Twice Bitten", "Lynsay Sands")], HASH)
        self.assertEqual(results[0]["status"], "no_request")

    def test_a_different_author_does_not_link(self):
        results = plan_links([req(1, "Love Bites", "Lynsay Sands")],
                             [book("Love Bites", "Someone Else Entirely")], HASH)
        self.assertEqual(results[0]["status"], "no_request")


class FakeCog:
    def __init__(self, rows, config=None):
        self.rows = rows
        self.config = config or {}
        self.marked = []
        self.db = self

    def list_requests(self):
        return self.rows

    async def mark_downloading(self, request_id, torrent_hash):
        self.marked.append((request_id, torrent_hash))
        return {"id": request_id}, None


class BuildTests(unittest.TestCase):
    def test_off_unless_enabled_with_a_secret_and_an_address(self):
        self.assertIsNone(build_intake(FakeCog([])))
        cfg = {"enabled": True, "secret": SECRET}
        self.assertIsNone(build_intake(FakeCog([], {"intake": cfg})))
        cfg["allowed_ips"] = [LIBRARIAN]
        self.assertIsNotNone(build_intake(FakeCog([], {"intake": cfg})))


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def start(self, rows, dry_run):
        self.cog = FakeCog(rows)
        server = IntakeServer(self.cog, {"secret": SECRET, "allowed_ips": ["127.0.0.1"],
                                         "dry_run": dry_run})
        self.client = TestClient(TestServer(server.build_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def post(self, body, headers=AUTH):
        resp = await self.client.post("/intake/books", data=json.dumps(body), headers=headers)
        return resp.status, await resp.json()

    PACK = {"torrent": {"hash": HASH, "name": "Argeneau 1-16"},
            "books": [book("A Quick Bite", "Lynsay Sands"), book("Love Bites", "Lynsay Sands")]}

    async def test_live_marks_the_matching_request_downloading(self):
        await self.start([req(7, "Love Bites", "Lynsay Sands")], dry_run=False)
        status, data = await self.post(self.PACK)
        self.assertEqual(status, 200)
        self.assertEqual([b["status"] for b in data["books"]], ["no_request", "linked"])
        self.assertEqual(self.cog.marked, [(7, HASH.lower())])

    async def test_dry_run_reports_but_changes_nothing(self):
        await self.start([req(7, "Love Bites", "Lynsay Sands")], dry_run=True)
        status, data = await self.post(self.PACK)
        self.assertEqual(data["books"][1]["status"], "would_link")
        self.assertEqual(self.cog.marked, [])

    async def test_wrong_secret_and_proxied_calls_get_403(self):
        await self.start([req(7, "Love Bites", "Lynsay Sands")], dry_run=False)
        self.assertEqual((await self.post(self.PACK, {"Authorization": "Bearer x"}))[0], 403)
        self.assertEqual((await self.post(self.PACK, {**AUTH, "X-Real-IP": "1.2.3.4"}))[0], 403)
        resp = await self.client.get("/intake/health")
        self.assertEqual(resp.status, 403)
        self.assertEqual(self.cog.marked, [])

    async def test_bad_json_is_a_400(self):
        await self.start([], dry_run=False)
        resp = await self.client.post("/intake/books", data="not json", headers=AUTH)
        self.assertEqual(resp.status, 400)


class CogTests(unittest.TestCase):
    """mark_downloading on the real cog and database: no email, link stored."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db = AudiobooksDB(self.tmp.name)
        self.db.get_or_create_user("alice@example.com", "Alice")
        self.row = self.db.create_requests("alice@example.com", [
            {"bookName": "Love Bites", "author": "Lynsay Sands", "linkToBook": "https://x"}])[0]
        self.sent = []

        async def fake_notifier(email, subject, body):
            self.sent.append(subject)
            return True

        from cogs.audiobooks import Audiobooks

        class FakeBot:
            config = {}

        self.cog = Audiobooks(FakeBot(), db=self.db, notifier=fake_notifier, config_override={})

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_downloading_sets_in_progress_and_the_hash_without_an_email(self):
        row, error = asyncio.run(self.cog.mark_downloading(self.row["id"], HASH))
        self.assertIsNone(error)
        self.assertEqual((row["status"], row["torrentHash"]), ("In Progress", HASH))
        self.assertEqual(self.sent, [])
        _, error = asyncio.run(self.cog.mark_downloading(self.row["id"], HASH))
        self.assertEqual(error, "already_in_progress")

    def test_the_library_can_close_a_requested_row_with_one_email(self):
        _, _, error = asyncio.run(self.cog.admin_fulfilled(self.row["id"]))
        self.assertEqual(error, "must_fulfill_first")  # the dashboard rule is unchanged
        row, sent, error = asyncio.run(
            self.cog.admin_fulfilled(self.row["id"], allow_requested=True))
        self.assertIsNone(error)
        self.assertEqual(row["status"], "Fulfilled")
        self.assertEqual(self.sent, ["Your audiobook request has been fulfilled"])

    def test_an_old_database_gets_the_column_and_a_backup(self):
        import glob
        import sqlite3

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = os.path.join(tmp, "old.db")
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, requester TEXT, "
                         "bookName TEXT, author TEXT NOT NULL DEFAULT '', linkToBook TEXT, "
                         "status TEXT, discordMessageId TEXT, discordChannelId TEXT, "
                         "createdAt TEXT, updatedAt TEXT)")
            conn.execute("INSERT INTO requests VALUES (1, 'a@example.com', 'Kept', '', "
                         "'https://x', 'Requested', NULL, NULL, 'now', 'now')")
            conn.commit()
            conn.close()
            old = AudiobooksDB(path)
            self.assertIn("torrentHash", old._table_columns("requests"))
            self.assertEqual(old.get_request(1)["bookName"], "Kept")
            old.close()
            self.assertEqual(len(glob.glob(path + ".pre-torrenthash-*.bak")), 1)


if __name__ == "__main__":
    unittest.main()
