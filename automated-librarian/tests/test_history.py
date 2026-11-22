"""The running log of the last 30 books."""
import logging, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import history
from test_verify import Base, Tags


class Ring(unittest.TestCase):
    def setUp(self): self.p = Path(tempfile.mkdtemp()) / "r.json"

    def test_keeps_only_the_newest_n_and_survives_a_restart(self):
        h = history.History(self.p, limit=3)
        for i in range(5):
            h.record(f"k{i}", f"book {i}", "placed", "filed"); 
        self.assertEqual([e["name"] for e in h.recent()], ["book 4", "book 3", "book 2"])
        again = history.History(self.p, limit=3)
        self.assertEqual(len(again.entries), 3)

    def test_events_accumulate_on_one_entry_and_render_shows_the_tags(self):
        h = history.History(self.p)
        h.record("k", "Some Book", "placed", "filed at a/b/c", decided_by="llm")
        h.record("k", "Some Book", "ffprobe", "tags read", authors=["Lynsay Sands"], album="Some Book")
        self.assertEqual(len(h.entries), 1); self.assertEqual(len(h.entries["k"]["events"]), 2)
        out = h.render()
        self.assertIn("Some Book", out); self.assertIn("authors: ['Lynsay Sands']", out); self.assertIn("decided_by: llm", out)

    def test_never_raises_if_the_disk_is_unwritable(self):
        h = history.History(Path("/proc/nope/recent.json")); h.record("k", "x", "s", "m")

    def test_log_lines_are_attached_to_the_focused_book(self):
        history.configure(self.p, loggers=("automated-librarian.verify",))
        lg = logging.getLogger("automated-librarian.verify"); lg.setLevel(logging.INFO)
        history.record("k", "Book", "placed", "filed")
        history.focus("k"); lg.warning("tags say X but folder is Y")
        history.focus(None); lg.warning("someone else's line")
        msgs = [e["msg"] for e in history._h.entries["k"]["events"]]
        self.assertIn("tags say X but folder is Y", msgs); self.assertNotIn("someone else's line", msgs)


class WithVerifier(Base):
    def test_a_move_is_fully_recorded(self):
        history.configure(Path(tempfile.mkdtemp()) / "r.json", loggers=("automated-librarian.verify",))
        logging.getLogger("automated-librarian.verify").setLevel(logging.INFO)
        old = self.book("genaShowalter/theImmortal/Who Loved Me"); self.tr.add("h1", old, ["b.m4b"])
        self.tags["b.m4b"] = Tags("Who Loved Me", ["Lynsay Sands"], "Who Loved Me")
        self.v.track("h1", "Who Loved Me.m4b", "placed", "genaShowalter", "Gena Showalter", "Who Loved Me"); self.v.tick()
        steps = {e["step"]: e for e in history._h.entries["h1"]["events"]}   # last event per step
        self.assertEqual(steps["ffprobe"]["data"]["authors"], ["Lynsay Sands"])
        self.assertEqual(steps["ffprobe"]["data"]["filed_under"], "genaShowalter")
        self.assertEqual(steps["catalogue"]["msg"], "confirmed")
        self.assertIn("lynsaySands", steps["move"]["data"]["to"] if "data" in steps["move"] else
                      [e for e in history._h.entries["h1"]["events"] if e["step"] == "move"][0]["data"]["to"])
        self.assertIn("warning", steps)                      # the verifier's own 'moving via Transmission' line
        self.assertTrue(any(e["step"] == "final" and "lynsaySands" in e["msg"] for e in history._h.entries["h1"]["events"]))


if __name__ == "__main__":
    unittest.main()
