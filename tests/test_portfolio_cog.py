"""Tests for the portfolio cog's CV watcher: staging, approval, blocking,
auto-publish and restarts. No network, Discord or bot needed: the fetch is
a fake and the bot owner is a fake that records what it is sent.

Run from the repo root:  python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs import portfolio_cv as cv
from cogs.portfolio import Portfolio


def make_cv(extra: str = "") -> str:
    filler = " ".join(["Built and ran things end to end."] * 30)
    return (
        "# Thea Arcadia\n\n**Junior Software Developer** · Open to remote\n\n"
        f"## Summary\n\n{filler}\n\n## Technical Skills\n\n- **Languages:** Python\n\n"
        f"## Experience\n\n- Did work. {extra}\n\n## Projects\n\n- Astrabot\n\n"
        "## Education\n\nGED\n"
    )


class FakeBot:
    def __init__(self, data_dir, **portfolio):
        self.config = {"portfolio": {"data_dir": data_dir, "cv_watch": False,
                                     "owner_id": 42, **portfolio}}
        self.owner = SimpleNamespace(id=42, send=AsyncMock())
        self.fetch_user = AsyncMock(return_value=self.owner)


class CVRulesTests(unittest.TestCase):
    def test_a_normal_cv_passes(self):
        self.assertEqual(cv.looks_safe_to_publish("", make_cv()), (True, ""))

    def test_an_empty_or_tiny_cv_is_refused(self):
        self.assertFalse(cv.looks_safe_to_publish("", "")[0])
        self.assertFalse(cv.looks_safe_to_publish(make_cv(), "# Thea\n## Summary\nhi")[0])

    def test_missing_sections_are_named_in_the_reason(self):
        broken = make_cv().replace("## Projects", "## Things")
        ok, reason = cv.looks_safe_to_publish("", broken)
        self.assertFalse(ok)
        self.assertIn("Projects", reason)

    def test_merge_conflict_markers_are_refused(self):
        ok, reason = cv.looks_safe_to_publish("", make_cv("\n<<<<<<< HEAD\n"))
        self.assertFalse(ok)
        self.assertIn("conflict", reason)

    def test_script_tags_are_refused(self):
        ok, reason = cv.looks_safe_to_publish("", make_cv("<script>alert(1)</script>"))
        self.assertFalse(ok)
        self.assertIn("HTML", reason)

    def test_a_big_shrink_is_refused(self):
        big = make_cv("x " * 2000)
        ok, reason = cv.looks_safe_to_publish(big, make_cv())
        self.assertFalse(ok)
        self.assertIn("half", reason)
        self.assertTrue(cv.looks_safe_to_publish(make_cv(), make_cv("small edit"))[0])


class DecisionTests(unittest.TestCase):
    def test_same_text_is_a_skip_even_with_different_line_endings(self):
        text = make_cv()
        self.assertEqual(cv.decide_action(text, text.replace("\n", "\r\n") + "  \n", False)[0], "skip")

    def test_changes_are_staged_or_published(self):
        self.assertEqual(cv.decide_action(make_cv(), make_cv("new"), False)[0], "stage")
        self.assertEqual(cv.decide_action(make_cv(), make_cv("new"), True)[0], "publish")
        self.assertEqual(cv.decide_action("", "tiny", True)[0], "blocked")

    def test_the_cv_text_is_never_treated_as_a_template_placeholder(self):
        page = cv.build_page("A {{UPDATED}} B {{CV}}", "<p>{{UPDATED}}</p>", "2026-11-11")
        self.assertEqual(page, "A 2026-11-11 B <p>{{UPDATED}}</p>")


class WatcherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def cog(self, **portfolio):
        self.bot = FakeBot(self.tmp.name, **portfolio)
        return Portfolio(self.bot)

    @staticmethod
    def fetching(status, text="", etag="e1"):
        return AsyncMock(return_value=(status, text, etag))

    def data(self, name):
        return Path(self.tmp.name) / name

    async def test_the_first_change_is_staged_and_the_site_does_not_move(self):
        cog = self.cog()
        before = cog._html
        result = await cog.check_once(self.fetching(200, make_cv()))
        self.assertEqual(result, "staged")
        self.assertEqual(cog._html, before)
        self.assertFalse(self.data("portfolio_live.html").exists())
        self.bot.owner.send.assert_awaited_once()
        message = self.bot.owner.send.await_args
        self.assertIn("waiting for approval", message.args[0])
        self.assertIsNotNone(message.kwargs.get("view"))

    async def test_approve_publishes_and_survives_a_restart(self):
        cog = self.cog()
        text = make_cv()
        await cog.check_once(self.fetching(200, text))
        version = cv.content_hash(text)
        self.assertIn("updated", cog.resolve_pending(version, True))
        self.assertIn("<h1>Thea Arcadia</h1>", cog._html)
        self.assertTrue(self.data("portfolio_live.html").exists())
        # Same CV again: nothing to do.
        self.assertEqual(await cog.check_once(self.fetching(200, text)), "skip")
        # A new Portfolio (a restart) serves the approved page, not the fallback.
        again = self.cog()
        self.assertIn("<h1>Thea Arcadia</h1>", again._html)

    async def test_a_restart_rebuilds_the_page_from_the_approved_cv(self):
        cog = self.cog(cv_auto_publish=True)
        await cog.check_once(self.fetching(200, make_cv()))
        self.data("portfolio_live.html").write_text("STALE PAGE", encoding="utf-8")
        again = self.cog()
        self.assertIn("<h1>Thea Arcadia</h1>", again._html)
        self.assertNotIn("STALE", again._html)
        self.assertNotIn("STALE", self.data("portfolio_live.html").read_text(encoding="utf-8"))

    async def test_the_pronouns_line_renders_as_small_text_on_the_page(self):
        cog = self.cog(cv_auto_publish=True)
        await cog.check_once(self.fetching(200, make_cv().replace(
            "# Thea Arcadia", "# Thea Arcadia <sub>(she/her)</sub>")))
        self.assertIn("<h1>Thea Arcadia <sub>(she/her)</sub></h1>", cog._html)

    async def test_reject_keeps_the_site_and_the_same_version_is_not_asked_again(self):
        cog = self.cog()
        text = make_cv()
        await cog.check_once(self.fetching(200, text))
        before = cog._html
        self.assertIn("Rejected", cog.resolve_pending(cv.content_hash(text), False))
        self.assertEqual(cog._html, before)
        self.assertEqual(await cog.check_once(self.fetching(200, text)), "already-asked")
        self.assertEqual(self.bot.owner.send.await_count, 1)

    async def test_the_approval_goes_to_the_configured_person_not_the_app_owner(self):
        cog = self.cog()
        await cog.check_once(self.fetching(200, make_cv()))
        self.bot.fetch_user.assert_awaited_with(42)
        self.assertEqual(cog.owner_id, 42)

    async def test_the_default_owner_is_thea_when_nothing_is_configured(self):
        bot = FakeBot(self.tmp.name)
        del bot.config["portfolio"]["owner_id"]
        self.assertEqual(Portfolio(bot).owner_id, 151408413744693248)

    async def test_a_closed_dm_is_logged_and_does_not_break_the_check(self):
        cog = self.cog()
        self.bot.owner.send.side_effect = RuntimeError("403 Forbidden (error code: 50007)")
        self.assertEqual(await cog.check_once(self.fetching(200, make_cv())), "staged")

    async def test_a_dm_that_failed_is_sent_again_on_the_next_check(self):
        cog = self.cog()
        self.bot.owner.send.side_effect = RuntimeError("403 Forbidden (error code: 50007)")
        await cog.check_once(self.fetching(200, make_cv()))
        self.assertFalse(cog._state.get("pending_sent"))
        self.bot.owner.send.side_effect = None          # DMs work now
        self.bot.owner.send.reset_mock()
        self.assertEqual(await cog.check_once(self.fetching(304)), "unchanged")
        self.bot.owner.send.assert_awaited_once()
        self.assertTrue(cog._state.get("pending_sent"))
        await cog.check_once(self.fetching(304))        # and not again after that
        self.bot.owner.send.assert_awaited_once()

    async def test_an_out_of_date_button_changes_nothing(self):
        cog = self.cog()
        await cog.check_once(self.fetching(200, make_cv("one")))
        await cog.check_once(self.fetching(200, make_cv("two")))   # newer change replaces the pending one
        before = cog._html
        stale = cv.content_hash(make_cv("one"))
        self.assertIn("out of date", cog.resolve_pending(stale, True))
        self.assertEqual(cog._html, before)
        self.assertIn("updated", cog.resolve_pending(cv.content_hash(make_cv("two")), True))

    async def test_a_broken_cv_is_blocked_once_and_the_owner_is_told_why(self):
        cog = self.cog()
        before = cog._html
        self.assertEqual(await cog.check_once(self.fetching(200, "# Thea\n")), "blocked")
        self.assertEqual(await cog.check_once(self.fetching(200, "# Thea\n")), "already-asked")
        self.assertEqual(cog._html, before)
        self.bot.owner.send.assert_awaited_once()
        self.assertIn("nearly empty", self.bot.owner.send.await_args.args[0])

    async def test_unchanged_and_errors_do_nothing(self):
        cog = self.cog()
        self.assertEqual(await cog.check_once(self.fetching(304)), "unchanged")
        self.assertEqual(await cog.check_once(self.fetching(0)), "error")
        self.bot.owner.send.assert_not_awaited()

    async def test_the_etag_is_remembered_for_the_next_check(self):
        cog = self.cog()
        await cog.check_once(self.fetching(200, make_cv(), etag="abc"))
        fetch = self.fetching(304)
        await cog.check_once(fetch)
        fetch.assert_awaited_once_with("abc")

    async def test_auto_publish_skips_the_buttons(self):
        cog = self.cog(cv_auto_publish=True)
        self.assertEqual(await cog.check_once(self.fetching(200, make_cv())), "published")
        self.assertIn("<h1>Thea Arcadia</h1>", cog._html)
        self.assertIsNone(self.bot.owner.send.await_args.kwargs.get("view"))

    async def test_reverting_to_an_older_approved_cv_is_offered_again(self):
        cog = self.cog(cv_auto_publish=True)
        first, second = make_cv("first"), make_cv("second")
        await cog.check_once(self.fetching(200, first))
        await cog.check_once(self.fetching(200, second))
        self.assertEqual(await cog.check_once(self.fetching(200, first)), "published")
        self.assertIn("first", cog._html)

    async def test_a_cv_with_html_in_it_cannot_inject_script_into_the_page(self):
        cog = self.cog(cv_auto_publish=True)
        evil = make_cv('[x](javascript:alert(1)) **<b>bold</b>**')
        await cog.check_once(self.fetching(200, evil))
        self.assertNotIn("javascript:", cog._html)
        self.assertNotIn("<b>bold</b>", cog._html)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", cog._html)


if __name__ == "__main__":
    unittest.main()
