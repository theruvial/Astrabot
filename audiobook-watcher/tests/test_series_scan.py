"""Regression: a series folder name that merely starts a title must not claim the book.
Real case (2026-11-20): 'The Immortal Who Loved Me ...' was filed under genaShowalter/theImmortal."""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import audiobook_watcher as w


def lib_with(tree):
    root = Path(tempfile.mkdtemp())
    for p in tree:
        (root / p).mkdir(parents=True)
    return w.Library(root)


TREE = ["genaShowalter/theImmortal/Rise of the Warlords 02 - The Immortal",
        "jamesHunter/Discount Dan's Backroom Bargains/Kiosk Kingdom",
        "lynsaySands/Argeneau/Love Bites"]


class SeriesScan(unittest.TestCase):
    def setUp(self):
        self.lib = lib_with(TREE)

    def test_series_name_starting_a_title_is_not_evidence(self):
        for name in ("The Immortal Who Loved Me An Argeneau Novel (Unabridged)",
                     "The Immortal Who Loved Me"):
            self.assertIsNone(w.series_scan(name, self.lib), name)

    def test_real_series_forms_still_match(self):
        for name, title in (("Discount Dan's Backroom Bargains, Book 3 - Kiosk Kingdom", "Kiosk Kingdom"),
                            ("Kiosk Kingdom_ Discount Dan's Backroom Bargains, Book 3", "Kiosk Kingdom"),
                            ("Discount Dan's Backroom Bargains 3 - Kiosk Kingdom", "Kiosk Kingdom")):
            d = w.series_scan(name, self.lib)
            self.assertIsNotNone(d, name)
            self.assertEqual((d.author, d.title), ("jamesHunter", title), name)

    def test_series_as_part_of_longer_word_is_ignored(self):
        self.assertIsNone(w.series_scan("Theimmortalsomething - Foo", self.lib))


if __name__ == "__main__":
    unittest.main()
