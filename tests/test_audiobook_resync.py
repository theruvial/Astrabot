"""Tests for the requests channel resync: posts stay in place, only what is
wrong is edited, missing posts are added and stale ones deleted.

No real Discord: the channel and messages are small fakes.
"""

import asyncio
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs.audiobooks import Audiobooks, build_embed, plan_resync, view_for_status
from cogs.audiobooks_db import AudiobooksDB

CHANNEL = 555


def row(i, status="Requested", mid=None, cid=CHANNEL):
    return {"id": i, "status": status, "discordMessageId": str(mid) if mid else None,
            "discordChannelId": str(cid) if mid else None}


class PlanTests(unittest.TestCase):
    def test_keeps_posts_of_open_requests_and_posts_the_missing_ones(self):
        rows = [row(3, mid=103), row(2, "In Progress"), row(1, mid=101)]  # newest first
        delete, check, post = plan_resync([101, 103], rows, CHANNEL)
        self.assertEqual(delete, [])
        self.assertEqual([(m, r["id"]) for m, r in check], [(101, 1), (103, 3)])
        self.assertEqual([r["id"] for r in post], [2])

    def test_deletes_posts_of_closed_requests_and_strays(self):
        rows = [row(2, "Fulfilled", mid=102), row(1, mid=101)]
        delete, check, post = plan_resync([101, 102, 999], rows, CHANNEL)
        self.assertEqual(sorted(delete), [102, 999])
        self.assertEqual([m for m, _ in check], [101])
        self.assertEqual(post, [])

    def test_a_post_deleted_by_hand_or_in_another_channel_is_reposted(self):
        rows = [row(2, mid=202, cid=777), row(1, mid=101)]
        delete, check, post = plan_resync([], rows, CHANNEL)
        self.assertEqual([r["id"] for r in post], [1, 2])  # oldest first

    def test_missing_posts_are_posted_oldest_first(self):
        rows = [row(5), row(4), row(3)]
        self.assertEqual([r["id"] for r in plan_resync([], rows, CHANNEL)[2]], [3, 4, 5])


class FakeMessage:
    def __init__(self, mid, embed=None, buttons=()):
        self.id = mid
        self.embeds = [embed] if embed else []
        self.components = [SimpleNamespace(children=[
            SimpleNamespace(custom_id=c, disabled=d) for c, d in buttons])]
        self.edited = None
        self.deleted = False

    async def edit(self, embed=None, view=None):
        self.edited = (embed, view)

    async def delete(self):
        self.deleted = True


class FakeChannel:
    id = CHANNEL

    def __init__(self, messages):
        self.messages = messages
        self.sent = []

    async def history(self, limit=None):
        for m in self.messages:
            yield m

    async def send(self, embed=None, view=None):
        msg = FakeMessage(9000 + len(self.sent), embed)
        self.sent.append(msg)
        return msg


class ResyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db = AudiobooksDB(self.tmp.name)
        self.db.get_or_create_user("alice@example.com", "Alice")
        self.rows = self.db.create_requests("alice@example.com", [
            {"bookName": f"Book {n}", "author": "A Writer", "linkToBook": "https://x"}
            for n in ("One", "Two", "Three")])

        class FakeBot:
            config = {}

        self.cog = Audiobooks(FakeBot(), db=self.db, notifier=None, config_override={})

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def posted(self, request_id, mid):
        """A message exactly as the bot would have posted it."""
        self.db.set_discord_message(request_id, mid, CHANNEL)
        current = self.db.get_request(request_id)
        view = view_for_status(self.cog, current["status"])
        return FakeMessage(mid, build_embed(current),
                           [(i.custom_id, i.disabled) for i in view.children])

    def run_resync(self, messages):
        channel = FakeChannel(messages)

        async def get_channel():
            return channel

        self.cog._get_channel = get_channel
        asyncio.run(self.cog.resync_channel())
        return channel

    def test_up_to_date_posts_are_left_alone(self):
        msgs = [self.posted(r["id"], 100 + r["id"]) for r in self.rows]
        channel = self.run_resync(msgs)
        self.assertTrue(all(m.edited is None and not m.deleted for m in msgs))
        self.assertEqual(channel.sent, [])

    def test_a_fixed_title_or_new_status_edits_the_post_in_place(self):
        msgs = [self.posted(r["id"], 100 + r["id"]) for r in self.rows]
        first, second = self.rows[0]["id"], self.rows[1]["id"]
        self.db.update_request(first, "Corrected Title", "https://x")
        self.db.set_request_status(second, "In Progress")
        channel = self.run_resync(msgs)
        edited = {m.id for m in msgs if m.edited}
        self.assertEqual(edited, {100 + first, 100 + second})
        self.assertIn("Corrected Title", msgs[0].edited[0].title)
        self.assertEqual([i.custom_id for i in msgs[1].edited[1].children],
                         ["audiobook:fulfilled", "audiobook:reject"])
        self.assertEqual(channel.sent, [])

    def test_fulfilled_and_stray_posts_go_and_missing_ones_come_back(self):
        keep = self.posted(self.rows[0]["id"], 101)
        done = self.posted(self.rows[1]["id"], 102)
        self.db.set_request_status(self.rows[1]["id"], "Fulfilled")
        stray = FakeMessage(999)
        channel = self.run_resync([keep, done, stray])  # row 3 has no post
        self.assertFalse(keep.deleted)
        self.assertTrue(done.deleted and stray.deleted)
        self.assertEqual(len(channel.sent), 1)
        self.assertEqual(self.db.get_request(self.rows[2]["id"])["discordMessageId"],
                         str(channel.sent[0].id))


if __name__ == "__main__":
    unittest.main()
