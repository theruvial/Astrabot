"""A multi-book torrent with many authors stays whole in staging; each book is linked under ITS author."""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import automated_librarian as w, packs
from tags import Tags

AUTHORS = ["Lynsay Sands", "James Hunter", "Dennis E. Taylor", "Brandon Sanderson", "Jim Butcher", "Andy Weir",
           "Gena Showalter", "Jeaniene Frost", "Patrick Rothfuss", "Neil Gaiman", "Terry Pratchett",
           "Ann Leckie", "N. K. Jemisin", "Becky Chambers"]
ROOT = "Fantasy Mega Pack"


def make_units(n=30):
    names = [f"{AUTHORS[i % 14]} - Book Title {i + 1}.m4b" for i in range(n)]
    units = [{"kind": "file", "path": nm, "title": nm[:-4].split(" - ", 1)[1]} for nm in names]
    return names, units


class Resolver(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp()); self.root = d / "Audiobooks"
        for p in ("lynsaySands/Argeneau/x", "jamesHunter/y", "dennisETaylor/z"):
            (self.root / p).mkdir(parents=True)
        self.lib = w.Library(self.root)
        self.names, self.units = make_units()

    def probe(self, p):
        nm = Path(p).name; a, t = nm[:-4].split(" - ", 1)
        if a == "Brandon Sanderson":          # narrator in the author tag: the catalogue must override it
            return Tags(t, ["Michael Kramer"])
        return Tags(t, [a])

    def confirm(self, authors, title):
        if authors == ["Michael Kramer"]:
            return "contradicted", ["Brandon Sanderson"]
        if authors == ["Andy Weir"]:
            return "unknown", []              # the catalogue cannot confirm this one
        return "confirmed", authors

    def resolve(self):
        files = [f"{ROOT}/{n}" for n in self.names]
        return w.resolve_mixed(ROOT, Path("/dl"), files, self.units, self.lib, self.confirm, probe=self.probe)

    def test_each_book_goes_to_its_own_author(self):
        specs, books, skipped = self.resolve()
        by = {}
        for link, target in specs:
            by.setdefault(link.split("/")[1], []).append((link, target))
        self.assertEqual(set(by), {"lynsaySands", "jamesHunter", "dennisETaylor", "brandonSanderson", "jimButcher",
                                   "geneShowalter".replace("gene", "gena"), "jeanieneFrost", "patrickRothfuss",
                                   "neilGaiman", "terryPratchett", "annLeckie", "nKJemisin", "beckyChambers"})
        self.assertNotIn("andyWeir", by)
        self.assertEqual(len(skipped), 2)
        self.assertEqual(len(specs) + len(skipped), 30)

    def test_existing_author_folders_are_reused_and_narrator_tag_overridden(self):
        specs, books, _ = self.resolve()
        links = [l for l, _ in specs]
        self.assertTrue(any(l.startswith("Audiobooks/lynsaySands/") for l in links))
        self.assertTrue(any(l.startswith("Audiobooks/brandonSanderson/") for l in links))
        self.assertFalse(any("michaelKramer" in l for l in links))

    def test_targets_point_into_the_untouched_pack_in_staging(self):
        specs, _, _ = self.resolve()
        for link, target in specs:
            self.assertTrue(target.endswith(f"audiobookStaging/{ROOT}/" + Path(target).name))
            self.assertTrue(target.startswith("../"))
            self.assertNotIn("/../", target.replace("../", "", target.count("../")))


class FakeTr:
    def __init__(self, base, files): self.base, self.files = base, files
    def call(self, m, **a):
        return {"torrents": [{"percentDone": 1, "downloadDir": str(self.base), "files": [{"name": f} for f in self.files]}]}


class FakeLinker:
    def __init__(self): self.got = []
    def run(self, specs, apply):
        self.got.append((list(specs), apply)); return {"ok": True, "made": len(specs), "kept": 0, "lines": [], "stderr": ""}


class Rec:
    def __init__(self): self.calls = []
    def track(self, *a, **k): self.calls.append((a, k))
    def send(self, *a): self.calls.append(a)


class Flow(Resolver):
    def test_tracker_resolves_links_and_reports(self):
        d = Path(tempfile.mkdtemp()); post, intake = Rec(), Rec()
        files = [f"{ROOT}/{n}" for n in self.names]; linker = FakeLinker()
        tk = packs.PackTracker(FakeTr("/dl", files), linker, post, state=d / "p.json",
                               resolver=lambda r, b, f, u: w.resolve_mixed(r, b, f, u, self.lib, self.confirm, self.probe),
                               intake=intake)
        packs.STATE_DIR = d
        tk.track_mixed("hash", ROOT, self.units); tk.tick()
        self.assertEqual(tk.jobs, {})
        (specs, apply), = linker.got
        self.assertTrue(apply); self.assertEqual(len(specs), 30 - 2)
        self.assertEqual(len(post.calls), 28)                       # Audiobookshelf told about every linked book
        authors = {c[0][3] for c in post.calls}
        self.assertIn("Brandon Sanderson", authors); self.assertNotIn("Michael Kramer", authors)
        (h, name, sent), = intake.calls
        self.assertEqual(len(sent), 28); self.assertEqual({s["author"] for s in sent} & {"Andy Weir"}, set())


if __name__ == "__main__":
    unittest.main()
