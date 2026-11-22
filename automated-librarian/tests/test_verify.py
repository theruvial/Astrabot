"""Tag check after download: moves go through Transmission only; unsure => leave it alone."""
import shutil, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import automated_librarian as w, tags, verify
from tags import Tags


class FakeTr:
    """Just enough Transmission: set-location(move) really moves the files, like the real one."""
    def __init__(self): self.t, self.log = {}, []
    def add(self, h, downloadDir, files, pct=1.0):
        self.t[h] = {"downloadDir": str(downloadDir), "files": [{"name": n} for n in files], "percentDone": pct}
    def call(self, m, **a):
        self.log.append((m, a))
        if m == "torrent-get":
            return {"torrents": [dict(self.t[h], error=0, errorString="") for h in a["ids"] if h in self.t]}
        t = self.t[a["ids"][0]]
        if m == "torrent-set-location":
            old, new = Path(t["downloadDir"]), Path(a["location"])
            names = [f["name"] for f in t["files"]]
            if "/" in names[0]:
                shutil.move(str(old / names[0].split("/")[0]), str(new / names[0].split("/")[0]))
            else:
                for n in names: shutil.move(str(old / n), str(new / n))
            t["downloadDir"] = str(new)
        elif m == "torrent-rename-path":
            root = Path(t["downloadDir"]); (root / a["path"]).rename(root / a["name"])
            t["files"] = [{"name": a["name"] + f["name"][len(a["path"]):]} for f in t["files"]]
        return {}


class Post:
    def __init__(self): self.calls = []
    def track(self, *a, **k): self.calls.append(a)


class Base(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp()); self.root, self.staging = d / "Audiobooks", d / "staging"
        for p in ("genaShowalter/theImmortal/Rise 02 - The Immortal", "lynsaySands/Argeneau/Love Bites",
                  "jeanieneFrost/Night Huntress/First Drop"):
            (self.root / p).mkdir(parents=True)
        self.staging.mkdir()
        self.lib, self.tr, self.post, self.tags = w.Library(self.root), FakeTr(), Post(), {}
        def decide(name):
            a, t = name.split(" - ", 1)
            f, s = self.lib.find_author(a)
            return w.Decision(author=f, title=t, author_display=a, confidence="high") if s >= 0.88 else None
        self.v = verify.Verifier(self.tr, self.root, self.staging, decide, lambda x: w.final_path(x, self.lib),
                                 lambda f: f, post=self.post, probe=lambda p: self.tags.get(Path(p).name),
                                 state=d / "v.json", confirm=lambda a, t: self.confirm(a, t))
        self.confirm = lambda a, t: ("confirmed", a)        # default: the catalogue agrees with the tags

    def book(self, rel, fname="b.m4b", base=None):
        p = (base or self.root) / rel; p.mkdir(parents=True, exist_ok=True); (p / fname).write_bytes(b"x"); return p


class Cases(Base):
    def test_misfiled_book_is_moved_by_transmission(self):
        old = self.book("genaShowalter/theImmortal/Who Loved Me")
        self.tr.add("h1", old, ["b.m4b"])
        self.tags["b.m4b"] = Tags("Who Loved Me", ["Lynsay Sands"])
        self.v.track("h1", "n", "placed", "genaShowalter", "Gena Showalter", "Who Loved Me")
        self.v.tick()
        new = self.root / "lynsaySands/Who Loved Me"
        self.assertTrue((new / "b.m4b").exists())
        self.assertFalse(old.exists(), "old empty folder removed")
        self.assertEqual(self.tr.t["h1"]["downloadDir"], str(new))
        ms = [m for m, _ in self.tr.log]
        self.assertIn("torrent-set-location", ms); self.assertIn("torrent-verify", ms)
        self.assertTrue([a for m, a in self.tr.log if m == "torrent-set-location"][0]["move"] is True)
        self.assertEqual(self.post.calls[0][1], "lynsaySands/Who Loved Me")
        self.assertEqual(self.v.jobs, {})

    def test_matching_tags_leave_it(self):
        old = self.book("lynsaySands/Argeneau/Love Bites 2"); self.tr.add("h", old, ["b.m4b"])
        self.tags["b.m4b"] = Tags("Love Bites", ["Lynsay Sands"])
        self.v.track("h", "n", "placed", "lynsaySands", "Lynsay Sands", "Love Bites 2"); self.v.tick()
        self.assertNotIn("torrent-set-location", [m for m, _ in self.tr.log])
        self.assertEqual(self.post.calls[0][1], "lynsaySands/Argeneau/Love Bites 2")

    def test_coauthor_tag_never_moves(self):
        for folder, disp in (("lynsaySands", "Lynsay Sands"), ("jeanieneFrost", "Jeaniene Frost")):
            old = self.book(f"{folder}/Bite Before Christmas"); self.tr.add(folder, old, ["b.m4b"])
            self.tags["b.m4b"] = Tags("Bite Before Christmas", ["Lynsay Sands", "Jeaniene Frost"])
            self.v.track(folder, "n", "placed", folder, disp, "Bite Before Christmas"); self.v.tick()
        self.assertNotIn("torrent-set-location", [m for m, _ in self.tr.log])

    def test_filed_by_first_listed_author(self):
        old = self.book("genaShowalter/theImmortal/Joint"); self.tr.add("h", old, ["b.m4b"])
        self.tags["b.m4b"] = Tags("Joint", ["Jeaniene Frost", "Lynsay Sands"])
        self.v.track("h", "n", "placed", "genaShowalter", "Gena Showalter", "Joint"); self.v.tick()
        self.assertTrue((self.root / "jeanieneFrost/Joint/b.m4b").exists())

    def test_no_tags_leaves_it(self):
        old = self.book("genaShowalter/theImmortal/Mystery"); self.tr.add("h", old, ["b.m4b"])
        self.v.track("h", "n", "placed", "genaShowalter", "Gena Showalter", "Mystery"); self.v.tick()
        self.assertTrue((old / "b.m4b").exists())
        self.assertNotIn("torrent-set-location", [m for m, _ in self.tr.log])

    def test_staged_with_clear_tags_is_filed(self):
        old = self.book("Unknown", base=self.staging); self.tr.add("h", old, ["b.m4b"])
        self.tags["b.m4b"] = Tags("Fresh Book", ["Lynsay Sands"])
        self.v.track("h", "n", "staged", title="x"); self.v.tick()
        self.assertTrue((self.root / "lynsaySands/Fresh Book/b.m4b").exists())
        self.assertTrue(self.staging.exists(), "staging root itself is never removed")
        self.assertEqual(self.post.calls[0][1], "lynsaySands/Fresh Book")

    def test_staged_with_no_confident_place_stays(self):
        old = self.book("Unknown", base=self.staging); self.tr.add("h", old, ["b.m4b"])
        self.tags["b.m4b"] = Tags("Fresh", ["Totally New Author"])
        self.v.track("h", "n", "staged"); self.v.tick()
        self.assertTrue((old / "b.m4b").exists()); self.assertEqual(self.post.calls, [])

    def test_existing_target_is_refused(self):
        self.book("lynsaySands/Taken")
        old = self.book("genaShowalter/theImmortal/Taken"); self.tr.add("h", old, ["b.m4b"])
        self.tags["b.m4b"] = Tags("Taken", ["Lynsay Sands"])
        self.v.track("h", "n", "placed", "genaShowalter", "Gena Showalter", "Taken"); self.v.tick()
        self.assertTrue((old / "b.m4b").exists())
        self.assertNotIn("torrent-set-location", [m for m, _ in self.tr.log])

    def test_unfinished_download_waits(self):
        old = self.book("genaShowalter/theImmortal/Wip"); self.tr.add("h", old, ["b.m4b"], pct=0.4)
        self.v.track("h", "n", "placed", "genaShowalter", "Gena Showalter", "Wip"); self.v.tick()
        self.assertIn("h", self.v.jobs); self.assertEqual(self.post.calls, [])

    def test_multifile_single_book_moves_and_renames_root(self):
        base = self.root / "genaShowalter/theImmortal"; (base / "Old Name").mkdir(parents=True)
        for n in ("01.mp3", "02.mp3"): (base / "Old Name" / n).write_bytes(b"x")
        self.tr.add("h", base, ["Old Name/01.mp3", "Old Name/02.mp3"])
        self.tags["01.mp3"] = Tags("Real Title", ["Lynsay Sands"])
        self.v.track("h", "n", "placed", "genaShowalter", "Gena Showalter", "Old Name", multi=True); self.v.tick()
        self.assertTrue((self.root / "lynsaySands/Real Title/01.mp3").exists())
        self.assertFalse((base / "Old Name").exists())
        self.assertEqual(self.tr.t["h"]["files"][0]["name"], "Real Title/01.mp3")

    def test_moves_off_only_logs(self):
        self.v.move = False
        old = self.book("genaShowalter/theImmortal/Stay"); self.tr.add("h", old, ["b.m4b"])
        self.tags["b.m4b"] = Tags("Stay", ["Lynsay Sands"])
        self.v.track("h", "n", "placed", "genaShowalter", "Gena Showalter", "Stay"); self.v.tick()
        self.assertTrue((old / "b.m4b").exists())


class Guards(Base):
    def test_name_named_author_beats_a_narrator_tag(self):
        old = self.book("jeanieneFrost/Night Huntress/Deathly"); self.tr.add("h", old, ["b.m4b"])
        self.tags["b.m4b"] = Tags("Deathly", ["Stephen Fry"])
        self.v.track("h", "n", "placed", "jeanieneFrost", "Jeaniene Frost", "Deathly", strong=True); self.v.tick()
        self.assertTrue((old / "b.m4b").exists())
        self.assertNotIn("torrent-set-location", [m for m, _ in self.tr.log])
        self.assertEqual(len(self.post.calls), 1, "still handed to Audiobookshelf where it is")

    def test_no_new_author_folders_from_a_placed_move(self):
        old = self.book("genaShowalter/theImmortal/Narrated"); self.tr.add("h", old, ["b.m4b"])
        self.tags["b.m4b"] = Tags("Narrated", ["Stephen Fry"])
        self.v._decide_orig = self.v.decide
        self.v.decide = lambda n: w.Decision(author="stephenFry", title="Narrated", author_display="Stephen Fry")
        self.v.track("h", "n", "placed", "genaShowalter", "Gena Showalter", "Narrated"); self.v.tick()
        self.assertTrue((old / "b.m4b").exists())
        self.assertFalse((self.root / "stephenFry").exists())


class Catalogue(Base):
    def go(self, status, canon, kind="placed", folder="genaShowalter", disp="Gena Showalter", tg=("Lynsay Sands",)):
        base = self.root / "genaShowalter/theImmortal" if kind == "placed" else self.staging
        old = self.book("genaShowalter/theImmortal/Bk" if kind == "placed" else "Bk", base=None if kind == "placed" else self.staging)
        self.tr.add("h", old, ["b.m4b"]); self.tags["b.m4b"] = Tags("Bk", list(tg))
        self.confirm = lambda a, t: (status, canon)
        self.v.track("h", "n", kind, folder if kind == "placed" else "", disp if kind == "placed" else "", "Bk")
        self.v.tick(); return old

    def test_unknown_leaves_it(self):
        old = self.go("unknown", []); self.assertTrue((old / "b.m4b").exists())

    def test_catalogue_overrides_narrator_tag_when_it_names_the_folder_author(self):
        old = self.go("contradicted", ["Gena Showalter"], tg=("Stephen Fry",)); self.assertTrue((old / "b.m4b").exists())

    def test_three_way_disagreement_leaves_it(self):
        old = self.go("contradicted", ["Jeaniene Frost"]); self.assertTrue((old / "b.m4b").exists())
        self.assertNotIn("torrent-set-location", [m for m, _ in self.tr.log])

    def test_staged_contradicted_uses_catalogue_author(self):
        self.go("contradicted", ["Jeaniene Frost"], kind="staged", tg=("Stephen Fry",))
        self.assertTrue((self.root / "jeanieneFrost/Bk/b.m4b").exists())

    def test_confirmed_uses_catalogue_first_author(self):
        self.go("confirmed", ["Jeaniene Frost", "Lynsay Sands"])
        self.assertTrue((self.root / "jeanieneFrost/Bk/b.m4b").exists())


class BookCheckUnit(unittest.TestCase):
    def test_statuses(self):
        import bookcheck as bc
        res = {"Harry Potter and the Deathly Hallows": [
            {"title": "Harry Potter and the Deathly Hallows, Book 7", "author": "J.K. Rowling", "narrator": "Jim Dale"},
            {"title": "Harry Potter and the Deathly Hallows (Narrated by Stephen Fry)", "author": "J.K. Rowling",
             "narrator": "Stephen Fry"}],
            "Joint": [{"title": "Joint", "author": "Lynsay Sands, Jeaniene Frost"}],
            "Common": [{"title": "Common", "author": "A One"}, {"title": "Common", "author": "B Two"}]}
        c = bc.BookCheck(lambda t, a: res.get(t, []))
        self.assertEqual(tuple(c(["Stephen Fry"], "Harry Potter and the Deathly Hallows"))[:2], ("contradicted", ["J.K. Rowling"]))
        self.assertEqual(tuple(c(["Jeaniene Frost"], "Joint"))[:2], ("confirmed", ["Lynsay Sands", "Jeaniene Frost"]))
        self.assertEqual(c(["Nobody"], "Common")[0], "unknown")
        # tag author is NOT a known narrator of that book -> the tags may just be wrong -> unknown, not contradicted
        self.assertEqual(c(["Robert Jordan"], "Harry Potter and the Deathly Hallows")[0], "unknown")
        self.assertEqual(c(["X"], "Missing")[0], "unknown")
        self.assertEqual(bc.BookCheck(None, lambda a, t: (a, t))(["Lynsay Sands"], "T")[0], "confirmed")
        # Audible ran and found nothing: Open Library must NOT be allowed to 'confirm' a narrator
        self.assertEqual(bc.BookCheck(lambda t, a: [], lambda a, t: (a, t))(["Phil Gigante"], "Iced")[0], "unknown")
        # Audible unreachable: Open Library may still confirm
        self.assertEqual(bc.BookCheck(lambda t, a: 1 / 0, lambda a, t: (a, t))(["Lynsay Sands"], "T")[0], "confirmed")
        self.assertEqual(bc.BookCheck(lambda t, a: 1 / 0)(["A"], "T")[0], "unknown")


class Series(Base):
    def test_moved_book_lands_in_author_series_title(self):
        from bookcheck import Verdict
        self.v.series_fn = lambda author, s: "Argeneau" if "Argeneau" in s else s     # snap to the existing folder
        self.confirm = lambda a, t: Verdict("confirmed", ["Lynsay Sands"], "An Argeneau Novel", "Who Loved Me")
        old = self.book("genaShowalter/theImmortal/Who Loved Me An Argeneau Novel"); self.tr.add("h", old, ["b.m4b"])
        self.tags["b.m4b"] = Tags("Who Loved Me: An Argeneau Novel", ["Lynsay Sands"])
        self.v.track("h", "n", "placed", "genaShowalter", "Gena Showalter", "x"); self.v.tick()
        self.assertTrue((self.root / "lynsaySands/Argeneau/Who Loved Me/b.m4b").exists())
        self.assertEqual(self.post.calls[0][1], "lynsaySands/Argeneau/Who Loved Me")


class CleanTitles(unittest.TestCase):
    def test_catalogue_cleaning(self):
        import bookcheck as bc
        self.assertEqual(bc.clean_catalogue_title("Harry Potter and the Deathly Hallows, Book 7"), "Harry Potter and the Deathly Hallows")
        self.assertEqual(bc.clean_catalogue_title("Harry Potter and the Deathly Hallows (Narrated by Stephen Fry)"), "Harry Potter and the Deathly Hallows")
        self.assertEqual(bc.clean_series("Harry Potter (Narrated by Stephen Fry)"), "Harry Potter")
        self.assertEqual(bc.clean_catalogue_title("Love Bites"), "Love Bites")


class Exempt(Base):
    def test_mixed_authors_folder_is_never_touched(self):
        (self.root / "mixedAuthors/Alien Trilogy").mkdir(parents=True)
        old = self.book("mixedAuthors/Alien Trilogy/Out of the Shadows"); self.tr.add("h", old, ["b.m4b"])
        self.tags["b.m4b"] = Tags("Out of the Shadows", ["Lynsay Sands"])      # catalogue would 'confirm' this
        self.v.track("h", "n", "placed", "mixedAuthors", "Mixed Authors", "Out of the Shadows"); self.v.tick()
        self.assertTrue((old / "b.m4b").exists())
        self.assertNotIn("torrent-set-location", [m for m, _ in self.tr.log])
        self.assertEqual(len(self.post.calls), 1)


class AlbumIsTheBook(unittest.TestCase):
    def test_book_title_comes_from_the_album(self):
        import tags as tg
        self.assertEqual(tg._book_title("The Eye of the World (The Wheel of Time #1)"), "The Eye of the World")
        self.assertEqual(tg._book_title("Love Bites (Unabridged)"), "Love Bites")
        self.assertEqual(tg._book_title("Dune"), "Dune")


class SnapSeries(unittest.TestCase):
    def test_filler_words_do_not_make_a_new_series_folder(self):
        self.assertEqual(w.snap_series("The Mistborn Saga", ["Mistborn Trilogy", "Other"]), "Mistborn Trilogy")
        self.assertEqual(w.snap_series("An Argeneau Novel", ["Argeneau"]), "Argeneau")
        self.assertEqual(w.snap_series("Shatter Me", ["Mistborn Trilogy"]), "Shatter Me")
        self.assertEqual(w.snap_series("The Series", ["Saga"]), "The Series")      # all filler: no false match


class TagParsing(unittest.TestCase):
    def test_split_and_match(self):
        self.assertEqual(tags.split_authors("Lynsay Sands, Jeaniene Frost"), ["Lynsay Sands", "Jeaniene Frost"])
        self.assertEqual(tags.split_authors("Dennis E. Taylor"), ["Dennis E. Taylor"])
        self.assertTrue(tags.author_matches("James SA Corey", ["James S. A. Corey"]))
        self.assertTrue(tags.author_matches("Lynsay Sands", ["Jeaniene Frost", "Lynsay Sands"]))
        self.assertFalse(tags.author_matches("Gena Showalter", ["Lynsay Sands"]))


if __name__ == "__main__":
    unittest.main()
