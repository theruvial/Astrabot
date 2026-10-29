"""Tests for the audiobook-request DB and provider logic.

Run from the repo root:  python -m unittest discover -s tests -v
No Discord token, network, or email provider is needed: provider checks
use a fake fetcher returning canned HTML.
"""

import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs.audiobooks_db import AudiobooksDB, mask_email, normalize_email
from cogs.audiobooks_providers import check_link, extract_series_books, provider_for


class EmailTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_email("  Thea@Example.COM "), "thea@example.com")
        self.assertEqual(normalize_email("thea@example.com"), "thea@example.com")

    def test_rejects_invalid(self):
        self.assertIsNone(normalize_email("not-an-email"))
        self.assertIsNone(normalize_email("thea@"))
        self.assertIsNone(normalize_email("thea@example"))
        self.assertIsNone(normalize_email(""))

    def test_mask(self):
        self.assertEqual(mask_email("thea@example.com"), "t***@example.com")


class DBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db = AudiobooksDB(self.tmp.name)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_user_and_session_roundtrip(self):
        user = self.db.get_or_create_user("thea@example.com", "Thea")
        self.assertEqual(user["firstName"], "Thea")
        # Idempotent: no duplicate, same user back.
        again = self.db.get_or_create_user("thea@example.com", "Thea")
        self.assertEqual(again["email"], "thea@example.com")
        token = self.db.create_session("thea@example.com")
        self.assertEqual(self.db.session_email(token), "thea@example.com")
        self.db.delete_session(token)
        self.assertIsNone(self.db.session_email(token))

    def test_requests_and_cancel(self):
        self.db.get_or_create_user("thea@example.com", "Thea")
        rows = self.db.create_requests("thea@example.com", [
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

class SeriesPrimaryTests(unittest.TestCase):
    def test_primary_count_truncates(self):
        html = ("<p>3 primary works \u2022 4 total works</p>"
                '<a href="/book/show/1">Book One</a>'
                '<a href="/book/show/2">Book Two</a>'
                '<a href="/book/show/3">Book Three</a>'
                '<a href="/book/show/4">The Complete Collection</a>')
        books = extract_series_books(html, "Goodreads")
        self.assertEqual([b["bookName"] for b in books],
                         ["Book One", "Book Two", "Book Three"])

    def test_range_heading_skipped_even_without_count(self):
        html = ("Book 1 <a href=\"/book/show/1\">Vol One</a> "
                "Book 2 <a href=\"/book/show/2\">Vol Two</a> "
                "Book 1-2 <a href=\"/book/show/3\">Omnibus</a>")
        books = extract_series_books(html, "Goodreads")
        self.assertEqual([b["bookName"] for b in books], ["Vol One", "Vol Two"])

    def test_decimal_entries_kept_up_to_count(self):
        html = ("<p>3 primary works \u2022 3 total works</p>"
                "Book 1 <a href=\"/book/show/1\">One</a> "
                "Book 1.5 <a href=\"/book/show/2\">Novella</a> "
                "Book 2 <a href=\"/book/show/3\">Two</a>")
        books = extract_series_books(html, "Goodreads")
        self.assertEqual(len(books), 3)

    def test_related_series_cut_off(self):
        html = ('<a href="/book/show/1">Main One</a>'
                '<a href="/book/show/2">Main Two</a>'
                'Related series <a href="/book/show/9">Other Series Book</a>')
        books = extract_series_books(html, "Goodreads")
        self.assertEqual([b["bookName"] for b in books],
                         ["Main One", "Main Two"])

    def test_non_goodreads_unchanged(self):
        self.assertEqual(extract_series_books('<a href="/book/show/1">X</a>', "Audible"), [])



if __name__ == "__main__":
    unittest.main()
