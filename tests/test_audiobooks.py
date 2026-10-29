"""Tests for the audiobook-request DB and provider logic.

Run from the repo root:  python -m unittest discover -s tests -v
No Discord token, network, or SMS provider is needed: provider checks
use a fake fetcher returning canned HTML.
"""

import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs.audiobooks_db import AudiobooksDB, format_phone, normalize_phone
from cogs.audiobooks_providers import check_link, provider_for


class PhoneTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_phone("727-266-9924"), "7272669924")
        self.assertEqual(normalize_phone("(727) 266-9924"), "7272669924")
        self.assertEqual(normalize_phone("7272669924"), "7272669924")

    def test_rejects_short_and_11_digit(self):
        self.assertIsNone(normalize_phone("727-266-992"))
        self.assertIsNone(normalize_phone("17272669924"))
        self.assertIsNone(normalize_phone(""))

    def test_format(self):
        self.assertEqual(format_phone("7272669924"), "727-266-9924")


class DBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db = AudiobooksDB(self.tmp.name)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_user_and_session_roundtrip(self):
        user = self.db.get_or_create_user("7272669924", "Thea")
        self.assertEqual(user["firstName"], "Thea")
        # Idempotent: no duplicate, same user back.
        again = self.db.get_or_create_user("7272669924", "Thea")
        self.assertEqual(again["phoneNumber"], "7272669924")
        token = self.db.create_session("7272669924")
        self.assertEqual(self.db.session_phone(token), "7272669924")
        self.db.delete_session(token)
        self.assertIsNone(self.db.session_phone(token))

    def test_requests_and_cancel(self):
        self.db.get_or_create_user("7272669924", "Thea")
        rows = self.db.create_requests("7272669924", [
            {"bookName": "Dune", "linkToBook": "https://www.audible.com/pd/Dune-Audiobook/B00TEST"},
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "Requested")
        listed = self.db.list_requests()
        self.assertEqual(listed[0]["requesterName"], "Thea")
        updated = self.db.set_request_status(rows[0]["id"], "Cancelled")
        self.assertEqual(updated["status"], "Cancelled")
        self.db.delete_request(rows[0]["id"])
        self.assertEqual(self.db.list_requests(), [])


def fake_fetch_factory(pages):
    async def fetch(url, timeout=10.0):
        return pages.get(url)
    return fetch


class ProviderTests(unittest.TestCase):
    def test_allowlist(self):
        self.assertEqual(provider_for("https://www.audible.com/pd/x"), "Audible")
        self.assertEqual(provider_for("https://www.goodreads.com/book/show/1"), "Goodreads")
        self.assertIsNone(provider_for("https://example.com/book/1"))

    def test_single_goodreads_with_audiobook(self):
        html = ("<html><head><title>Dune by Frank Herbert</title></head>"
                "<body><p>Audible Audiobook, narrated by Scott Brick, "
                "listening length 21 hours</p></body></html>")
        pages = {"https://www.goodreads.com/book/show/1": html}
        result = asyncio.run(check_link(
            "https://www.goodreads.com/book/show/1",
            fetch=fake_fetch_factory(pages)))
        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["created"][0]["bookName"], "Dune")

    def test_single_goodreads_without_audiobook(self):
        html = ("<html><head><title>Dune by Frank Herbert</title></head>"
                "<body><p>Hardcover, 688 pages</p></body></html>")
        pages = {"https://www.goodreads.com/book/show/1": html}
        result = asyncio.run(check_link(
            "https://www.goodreads.com/book/show/1",
            fetch=fake_fetch_factory(pages)))
        self.assertEqual(result["created"], [])

    def test_series_partial(self):
        series_html = ('<a href="/book/show/1">Dune</a>'
                       '<a href="/book/show/2">Dune Messiah</a>')
        pages = {
            "https://www.goodreads.com/series/1": series_html,
            "https://www.goodreads.com/book/show/1":
                "<title>Dune</title><p>Audible Audiobook, narrated by X</p>",
            "https://www.goodreads.com/book/show/2":
                "<title>Dune Messiah</title><p>Paperback, 256 pages</p>",
        }
        result = asyncio.run(check_link(
            "https://www.goodreads.com/series/1",
            fetch=fake_fetch_factory(pages)))
        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["created"][0]["bookName"], "Dune")
        self.assertEqual(result["skipped"], ["Dune Messiah"])

    def test_spotify_must_be_audiobook_path(self):
        html = "<title>Some Show</title>"
        pages = {"https://open.spotify.com/show/abc": html}
        result = asyncio.run(check_link(
            "https://open.spotify.com/show/abc",
            fetch=fake_fetch_factory(pages)))
        self.assertEqual(result["created"], [])


if __name__ == "__main__":
    unittest.main()
