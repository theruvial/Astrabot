"""Add-time check of a model-chosen author against the catalogue."""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import automated_librarian as w, bookcheck


def lib():
    root = Path(tempfile.mkdtemp())
    for p in ("lynsaySands/Argeneau/x", "sherrilynKenyon/Dark-Hunter/y", "katieMacAlister/Z/y"):
        (root / p).mkdir(parents=True)
    return w.Library(root)


def dec(author, display, title, score):
    return w.Decision(author=author, title=title, author_display=display, confidence="high", source="llm", score=score)


class Cross(unittest.TestCase):
    def setUp(self): self.lib = lib()

    def check(self, results, d):
        return w.crosscheck_llm(d, self.lib, bookcheck.BookCheck(lambda t, a: results))

    def test_catalogue_overrides_a_wrong_model_author_and_adds_series(self):
        r = [{"title": "Immortal Born", "author": "Lynsay Sands", "series": [{"series": "An Argeneau Novel"}]}]
        d = self.check(r, dec("sherrilynKenyon", "Sherrilyn Kenyon", "Immortal Born", 0.95))
        self.assertEqual((d.author, d.series), ("lynsaySands", "Argeneau"))
        self.assertIn("overrode", d.notes[-1])

    def test_confirmed_keeps_author_and_uses_catalogue_series(self):
        r = [{"title": "Love Bites", "author": "Lynsay Sands", "series": [{"series": "An Argeneau Novel"}]}]
        d = self.check(r, dec("lynsaySands", "Lynsay Sands", "Love Bites", 0.8))
        self.assertEqual((d.author, d.series), ("lynsaySands", "Argeneau"))

    def test_title_shared_by_several_authors_confirms_if_ours_is_among_them(self):
        r = [{"title": "The Stolen Crown", "author": "Susan Higginbotham"}, {"title": "The Stolen Crown", "author": "Other Person"}]
        d = self.check(r, dec("susanHigginbotham", "Susan Higginbotham", "The Stolen Crown", 0.8))
        self.assertEqual(d.author, "susanHigginbotham")

    def test_tie_between_other_authors_is_unknown(self):
        r = [{"title": "Common", "author": "A One"}, {"title": "Common", "author": "B Two"}]
        self.assertIsNone(self.check(r, dec("katieMacAlister", "Katie MacAlister", "Common", 0.8)))
        self.assertIsNotNone(self.check(r, dec("katieMacAlister", "Katie MacAlister", "Common", 0.95)))

    def test_nothing_found_stages_unless_model_is_very_sure(self):
        self.assertIsNone(self.check([], dec("a", "A B", "Obscure", 0.75)))
        d = self.check([], dec("a", "A B", "Obscure", 0.95)); self.assertIn("could not check", d.notes[-1])

    def test_search_failure_is_unknown_not_a_crash(self):
        c = bookcheck.BookCheck(lambda t, a: 1 / 0)
        self.assertIsNone(w.crosscheck_llm(dec("a", "A B", "T", 0.8), self.lib, c))


if __name__ == "__main__":
    unittest.main()
