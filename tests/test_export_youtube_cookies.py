"""Tests for the cookie export helper's pure rules (no browser needed).

Run from the repo root:  python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import export_youtube_cookies as ex


class ExportCookiesTests(unittest.TestCase):
    def test_only_youtube_and_google_domains_are_kept(self):
        for good in (".youtube.com", "youtube.com", "www.youtube.com", ".google.com", "accounts.google.com"):
            self.assertTrue(ex.wanted(good), good)
        for bad in (".notyoutube.com", "youtube.com.evil.example", ".example.com", "", None, "google.com.au"):
            self.assertFalse(ex.wanted(bad), bad)

    def test_signed_in_needs_a_login_cookie(self):
        self.assertTrue(ex.signed_in(["PREF", "SAPISID"]))
        self.assertTrue(ex.signed_in(["LOGIN_INFO"]))
        self.assertFalse(ex.signed_in(["PREF", "VISITOR_INFO1_LIVE", "YSC"]))
        self.assertFalse(ex.signed_in([]))

    def test_cookie_files_are_git_ignored(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
            lines = {line.strip() for line in f}
        self.assertIn("cookies.txt", lines)


if __name__ == "__main__":
    unittest.main()
