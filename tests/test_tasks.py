"""Tests for the tasks cog.

Run from the repo root:  python -m unittest discover -s tests -v
No network, Discord token, or bot needed: HTTP is stubbed and
interactions are fakes that record what was sent, like
tests/test_wiki.py.
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cogs.tasks as tasks_mod
from cogs.tasks import (
    Tasks,
    WikiApiError,
    build_review_embed,
    build_task_embed,
    classifier_config,
    normalize_task_id,
    normalize_text,
    wiki_api_config,
)


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class FakeUser:
    def __init__(self, user_id=151408413744693248, name="Thea"):
        self.id = user_id
        self.display_name = name
        self.bot = False


class FakeResponse:
    def __init__(self):
        self.sent = []
        self.deferred = False
        self.edits = []

    async def send_message(self, content=None, embed=None, ephemeral=False, view=None):
        self.sent.append(
            {"content": content, "embed": embed, "ephemeral": ephemeral, "view": view}
        )

    async def defer(self, ephemeral=False):
        self.deferred = True

    async def edit_message(self, embed=None, view=None):
        self.edits.append({"embed": embed, "view": view})


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, ephemeral=False):
        self.sent.append({"content": content, "ephemeral": ephemeral})


class FakeMessage:
    def __init__(self, message_id=111, content="fix the door", author=None,
                 channel_id=tasks_mod.WATCH_CHANNEL_ID):
        self.id = message_id
        self.content = content
        self.author = author or FakeUser()
        self.channel = FakeChannel(channel_id)
        self.jump_url = f"https://discord.com/channels/1/{channel_id}/{message_id}"
        self.embeds = []
        self.deleted = False
        self.edits = []
        self.thread = None

    async def edit(self, embed=None, view=None):
        self.edits.append({"embed": embed, "view": view})
        if embed is not None:
            self.embeds = [embed]

    async def delete(self):
        self.deleted = True

    async def create_thread(self, name=None, auto_archive_duration=None, type=None):
        self.thread = FakeThread(999)
        return self.thread


class FakeThread:
    def __init__(self, thread_id=999):
        self.id = thread_id
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeChannel:
    def __init__(self, channel_id):
        self.id = channel_id
        self.name = "general"
        self.sent = []

    async def send(self, embed=None, view=None):
        message = FakeMessage(message_id=200 + len(self.sent), content="")
        message.embeds = [embed] if embed is not None else []
        message.channel = self
        self.sent.append({"embed": embed, "view": view, "message": message})
        return message


class FakeGuild:
    def __init__(self, owner_id=1):
        self.owner_id = owner_id


class FakeInteraction:
    def __init__(self, user=None, message=None, guild=None):
        self.user = user or FakeUser()
        self.message = message
        self.guild = guild
        self.response = FakeResponse()
        self.followup = FakeFollowup()


class FakeBot:
    def __init__(self):
        self.channels = {}
        self.views = []
        self._is_owner = False

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    def add_view(self, view):
        self.views.append(view)

    async def is_owner(self, user):
        return self._is_owner

    async def fetch_channel(self, channel_id):
        raise ValueError("not found")


def make_cog(tmpdir):
    bot = FakeBot()
    bot.channels[tasks_mod.FEEDBACK_CHANNEL_ID] = FakeChannel(tasks_mod.FEEDBACK_CHANNEL_ID)
    cog = Tasks(
        bot,
        config_override={"admin_user_ids": [151408413744693248]},
        labels_path=os.path.join(tmpdir, "task_labels.jsonl"),
        overrides_path=os.path.join(tmpdir, "task_overrides.json"),
        state_path=os.path.join(tmpdir, "tasks_state.json"),
        retrain_marker_path=os.path.join(tmpdir, "last_retrain.json"),
    )
    return cog, bot


class HelpersTests(unittest.TestCase):
    def test_normalize_text(self):
        self.assertEqual(normalize_text("  Fix   the Door "), "fix the door")

    def test_normalize_task_id(self):
        self.assertEqual(normalize_task_id("1"), "task-0001")
        self.assertEqual(normalize_task_id("task-1"), "task-0001")
        self.assertEqual(normalize_task_id("task-0001"), "task-0001")
        self.assertEqual(normalize_task_id("42"), "task-0042")

    def test_api_config_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            url, key = wiki_api_config()
            self.assertEqual(url, "http://127.0.0.1:13382")
            self.assertIsNone(key)
            self.assertEqual(classifier_config(), "http://127.0.0.1:13383")

    def test_build_task_embed_has_task_id_field(self):
        embed = build_task_embed("fix the door", wiki_task_id="task-0001")
        values = {f.name: f.value for f in embed.fields}
        self.assertEqual(values.get("Task ID"), "task-0001")
        self.assertEqual(values.get("Status"), "open")

    def test_build_review_embed_has_no_claim(self):
        embed = build_review_embed("salvage is broken", 0.5, "score_band")
        self.assertEqual(embed.title, "Needs review")
        self.assertIn("score_band", [f.value for f in embed.fields])


class RoutingTests(unittest.TestCase):
    def test_auto_task_creates_wiki_task_and_posts_embed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            channel = bot.channels[tasks_mod.FEEDBACK_CHANNEL_ID]
            message = FakeMessage(content="fix the door")

            async def fake_classify(text):
                return {"ok": True, "pieces": [
                    {"text": "fix the door", "score": 0.9,
                     "decision": "auto_task", "trigger": "none"},
                ]}

            async def fake_wiki(method, path, **kwargs):
                if method == "GET":
                    return {"ok": True, "tasks": {"items": [], "counts": {}}}
                if method == "POST":
                    return {"ok": True, "task": {
                        "id": "task-0001", "title": "fix the door", "status": "open"}}
                raise AssertionError(method)

            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_classify", side_effect=fake_classify), \
                    patch.object(cog, "_wiki_api", side_effect=fake_wiki):
                run(cog.on_message(message))
            self.assertEqual(len(channel.sent), 1)
            embed = channel.sent[0]["embed"]
            self.assertEqual(embed.title, "fix the door")
            self.assertEqual(len(cog.state), 1)

    def test_review_posts_review_embed_and_thread(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            channel = bot.channels[tasks_mod.FEEDBACK_CHANNEL_ID]
            message = FakeMessage(content="asteroid field is not an asteroid field")

            async def fake_classify(text):
                return {"ok": True, "pieces": [
                    {"text": "asteroid field is not an asteroid field",
                     "score": 0.5, "decision": "review", "trigger": "score_band"},
                ]}

            async def fake_wiki(method, path, **kwargs):
                return {"ok": True, "tasks": {"items": [], "counts": {}}}

            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_classify", side_effect=fake_classify), \
                    patch.object(cog, "_wiki_api", side_effect=fake_wiki):
                run(cog.on_message(message))
            self.assertEqual(len(channel.sent), 1)
            self.assertEqual(channel.sent[0]["embed"].title, "Needs review")
            posted = channel.sent[0]["message"]
            self.assertIsNotNone(posted.thread)
            record = list(cog.state.values())[0]
            self.assertEqual(record["kind"], "review")
            self.assertEqual(record["thread_id"], 999)

    def test_auto_drop_posts_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            channel = bot.channels[tasks_mod.FEEDBACK_CHANNEL_ID]
            message = FakeMessage(content="hi")

            async def fake_classify(text):
                return {"ok": True, "pieces": [
                    {"text": "hi", "score": 0.1,
                     "decision": "auto_drop", "trigger": "none"},
                ]}

            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_classify", side_effect=fake_classify):
                run(cog.on_message(message))
            self.assertEqual(len(channel.sent), 0)

    def test_wrong_channel_and_bot_messages_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            channel = bot.channels[tasks_mod.FEEDBACK_CHANNEL_ID]
            other = FakeMessage(content="fix the door", channel_id=123)
            bot_message = FakeMessage(content="fix the door")
            bot_message.author.bot = True

            async def fake_classify(text):
                raise AssertionError("classifier must not be called")

            with patch.object(cog, "_classify", side_effect=fake_classify):
                run(cog.on_message(other))
                run(cog.on_message(bot_message))
            self.assertEqual(len(channel.sent), 0)

    def test_classifier_down_logs_and_does_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            channel = bot.channels[tasks_mod.FEEDBACK_CHANNEL_ID]
            message = FakeMessage(content="fix the door")
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_classify", return_value=None):
                run(cog.on_message(message))
            self.assertEqual(len(channel.sent), 0)

    def test_duplicate_piece_is_not_posted_twice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            channel = bot.channels[tasks_mod.FEEDBACK_CHANNEL_ID]

            async def fake_classify(text):
                return {"ok": True, "pieces": [
                    {"text": "fix the door", "score": 0.9,
                     "decision": "auto_task", "trigger": "none"},
                ]}

            async def fake_wiki(method, path, **kwargs):
                if method == "GET":
                    return {"ok": True, "tasks": {"items": [], "counts": {}}}
                return {"ok": True, "task": {
                    "id": "task-0001", "title": "fix the door", "status": "open"}}

            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_classify", side_effect=fake_classify), \
                    patch.object(cog, "_wiki_api", side_effect=fake_wiki):
                run(cog.on_message(FakeMessage(content="fix the door")))
                run(cog.on_message(FakeMessage(content="fix the door")))
            self.assertEqual(len(channel.sent), 1)


class ReviewButtonTests(unittest.TestCase):
    def _review_message(self, cog):
        message = FakeMessage(message_id=300, content="")
        message.embeds = [build_review_embed(
            "asteroid field is not an asteroid field", 0.5, "score_band",
            FakeMessage(content="asteroid field is not an asteroid field"))]
        cog.state["300"] = {
            "kind": "review", "wiki_task_id": None, "source_message_id": 111,
            "piece": "asteroid field is not an asteroid field",
            "score": 0.5, "trigger": "score_band", "thread_id": 999,
            "status": "review",
        }
        return message

    def test_review_is_task_creates_wiki_task_and_deletes_thread(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            message = self._review_message(cog)
            bot.fetch_channel = AsyncMock(return_value=FakeThread(999))
            interaction = FakeInteraction(message=message)

            async def fake_wiki(method, path, **kwargs):
                return {"ok": True, "task": {
                    "id": "task-0002",
                    "title": "asteroid field is not an asteroid field",
                    "status": "open"}}

            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=fake_wiki):
                run(cog.handle_review_button(interaction, "is_task"))
            self.assertEqual(len(message.edits), 1)
            self.assertEqual(message.edits[0]["embed"].title,
                             "asteroid field is not an asteroid field")
            record = cog.state.get("300")
            self.assertEqual(record["kind"], "task")
            self.assertEqual(record["wiki_task_id"], "task-0002")
            labels = open(cog.labels_path, encoding="utf-8").read().strip().splitlines()
            self.assertEqual(len(labels), 1)
            self.assertEqual(json.loads(labels[0])["label"], 1)
            overrides = json.loads(open(cog.overrides_path, encoding="utf-8").read())
            self.assertEqual(overrides["asteroid field is not an asteroid field"], 1)

    def test_review_not_task_deletes_message_and_thread(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            message = self._review_message(cog)
            thread = FakeThread(999)
            bot.fetch_channel = AsyncMock(return_value=thread)
            interaction = FakeInteraction(message=message)
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}):
                run(cog.handle_review_button(interaction, "not_task"))
            self.assertTrue(message.deleted)
            self.assertTrue(thread.deleted)
            self.assertNotIn("300", cog.state)
            labels = open(cog.labels_path, encoding="utf-8").read().strip().splitlines()
            self.assertEqual(json.loads(labels[0])["label"], 0)

    def test_review_non_admin_is_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            message = self._review_message(cog)
            other = FakeUser(user_id=999999, name="NotAdmin")
            interaction = FakeInteraction(user=other, message=message)
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}):
                run(cog.handle_review_button(interaction, "not_task"))
            self.assertEqual(len(interaction.response.sent), 1)
            self.assertTrue(interaction.response.sent[0]["ephemeral"])
            self.assertFalse(message.deleted)


class TaskButtonTests(unittest.TestCase):
    def _task_message(self, cog):
        message = FakeMessage(message_id=400, content="")
        message.embeds = [build_task_embed(
            "fix the door", wiki_task_id="task-0001")]
        cog.state["400"] = {
            "kind": "task", "wiki_task_id": "task-0001",
            "source_message_id": 111, "piece": "fix the door",
            "score": 0.9, "trigger": "none", "thread_id": None,
            "status": "open",
        }
        return message

    def _claim_fake(self, current_task, calls):
        """Fake wiki API: GET returns the task, PATCH applies assignees."""
        async def fake_wiki(method, path, **kwargs):
            calls.append((method, path, kwargs.get("json")))
            if method == "GET":
                return {"ok": True, "task": dict(current_task)}
            patched = dict(current_task)
            patched["assignees"] = kwargs.get("json", {}).get("assignees", [])
            patched["assignee"] = (
                patched["assignees"][0] if patched["assignees"] else "")
            return {"ok": True, "task": patched}
        return fake_wiki

    def test_claim_adds_first_assignee(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            message = self._task_message(cog)
            interaction = FakeInteraction(message=message)
            calls = []
            fake = self._claim_fake(
                {"id": "task-0001", "title": "fix the door",
                 "status": "open", "assignees": []}, calls)
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=fake):
                run(cog.handle_task_button(interaction, "claim"))
            self.assertIn(
                ("GET", "/api/tasks/task-0001", None), calls)
            self.assertIn(
                ("PATCH", "/api/tasks/task-0001", {"assignees": ["Thea"]}),
                calls)
            self.assertEqual(len(message.edits), 1)
            values = {f.name: f.value for f in message.edits[0]["embed"].fields}
            self.assertEqual(values.get("Assignees"), "Thea")
            self.assertEqual(interaction.followup.sent[0]["content"], "Claimed.")

    def test_claim_second_user_joins_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            message = self._task_message(cog)
            justin = FakeUser(user_id=222222, name="Justin")
            interaction = FakeInteraction(user=justin, message=message)
            calls = []
            fake = self._claim_fake(
                {"id": "task-0001", "title": "fix the door",
                 "status": "open", "assignees": ["Thea"]}, calls)
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=fake):
                run(cog.handle_task_button(interaction, "claim"))
            self.assertIn(
                ("PATCH", "/api/tasks/task-0001",
                 {"assignees": ["Thea", "Justin"]}), calls)
            values = {f.name: f.value for f in message.edits[0]["embed"].fields}
            self.assertEqual(values.get("Assignees"), "Thea, Justin")

    def test_claim_by_assigned_user_unclaims(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            message = self._task_message(cog)
            interaction = FakeInteraction(message=message)
            calls = []
            fake = self._claim_fake(
                {"id": "task-0001", "title": "fix the door",
                 "status": "open", "assignees": ["Thea"]}, calls)
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=fake):
                run(cog.handle_task_button(interaction, "claim"))
            self.assertIn(
                ("PATCH", "/api/tasks/task-0001", {"assignees": []}), calls)
            values = {f.name: f.value for f in message.edits[0]["embed"].fields}
            self.assertNotIn("Assignees", values)
            self.assertEqual(interaction.followup.sent[0]["content"], "Unclaimed.")

    def test_unclaim_keeps_other_assignees(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            message = self._task_message(cog)
            justin = FakeUser(user_id=222222, name="Justin")
            interaction = FakeInteraction(user=justin, message=message)
            calls = []
            fake = self._claim_fake(
                {"id": "task-0001", "title": "fix the door",
                 "status": "open", "assignees": ["Thea", "Justin"]}, calls)
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=fake):
                run(cog.handle_task_button(interaction, "claim"))
            self.assertIn(
                ("PATCH", "/api/tasks/task-0001", {"assignees": ["Thea"]}),
                calls)
            values = {f.name: f.value for f in message.edits[0]["embed"].fields}
            self.assertEqual(values.get("Assignees"), "Thea")

    def test_claim_reads_legacy_single_assignee(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            message = self._task_message(cog)
            justin = FakeUser(user_id=222222, name="Justin")
            interaction = FakeInteraction(user=justin, message=message)
            calls = []
            fake = self._claim_fake(
                {"id": "task-0001", "title": "fix the door",
                 "status": "open", "assignee": "Thea"}, calls)
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=fake):
                run(cog.handle_task_button(interaction, "claim"))
            self.assertIn(
                ("PATCH", "/api/tasks/task-0001",
                 {"assignees": ["Thea", "Justin"]}), calls)
            values = {f.name: f.value for f in message.edits[0]["embed"].fields}
            self.assertEqual(values.get("Assignees"), "Thea, Justin")

    def test_embed_lists_multiple_assignees(self):
        embed = build_task_embed(
            "fix the door", wiki_task_id="task-0001",
            assignees=["Thea", "Justin"])
        values = {f.name: f.value for f in embed.fields}
        self.assertEqual(values.get("Assignees"), "Thea, Justin")

    def test_not_task_deletes_wiki_task_and_saves_label(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            message = self._task_message(cog)
            interaction = FakeInteraction(message=message)
            calls = []

            async def fake_wiki(method, path, **kwargs):
                calls.append((method, path))
                return {"ok": True, "deleted": "task-0001"}

            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=fake_wiki):
                run(cog.handle_task_button(interaction, "not_task"))
            self.assertIn(("DELETE", "/api/tasks/task-0001"), calls)
            labels = open(cog.labels_path, encoding="utf-8").read().strip().splitlines()
            self.assertEqual(json.loads(labels[0])["label"], 0)
            self.assertNotIn("400", cog.state)

    def test_wiki_error_is_surfaced_ephemerally(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            message = self._task_message(cog)
            interaction = FakeInteraction(message=message)

            async def fake_wiki(method, path, **kwargs):
                raise WikiApiError(404, "task not found")

            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=fake_wiki):
                run(cog.handle_task_button(interaction, "done"))
            self.assertEqual(len(interaction.followup.sent), 1)
            self.assertIn("task not found", interaction.followup.sent[0]["content"])


class SlashCommandTests(unittest.TestCase):
    def test_task_add_requires_title(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            interaction = FakeInteraction()
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}):
                run(cog.task_add.callback(cog, interaction, title="   "))
            self.assertEqual(len(interaction.response.sent), 1)
            self.assertIn("title is required", interaction.response.sent[0]["content"].lower())

    def test_task_add_posts_to_wiki(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            interaction = FakeInteraction()

            async def fake_wiki(method, path, **kwargs):
                self.assertEqual(method, "POST")
                self.assertEqual(kwargs["json"]["title"], "Write the Rival page")
                return {"ok": True, "task": {"id": "task-0003", "title": "Write the Rival page"}}

            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=fake_wiki):
                run(cog.task_add.callback(
                    cog, interaction, title="Write the Rival page"))
            self.assertIn("task-0003", interaction.response.sent[0]["content"])

    def test_task_add_non_admin_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            other = FakeUser(user_id=999999, name="NotAdmin")
            interaction = FakeInteraction(user=other)
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}):
                run(cog.task_add.callback(cog, interaction, title="x"))
            self.assertTrue(interaction.response.sent[0]["ephemeral"])

    def test_task_delete_unknown_id_surfaces_404(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            interaction = FakeInteraction()

            async def fake_wiki(method, path, **kwargs):
                raise WikiApiError(404, "task not found")

            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=fake_wiki):
                run(cog.task_delete.callback(cog, interaction, task_id="99"))
            self.assertIn("task not found", interaction.response.sent[0]["content"])

    def test_task_stats_reports_retrain_count(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            cog._append_label(text="fix the door", label=1, source_message_id=1,
                              score=0.9, trigger="none", reviewer_id=1)
            cog._append_label(text="salvage is broken", label=1, source_message_id=2,
                              score=0.9, trigger="none", reviewer_id=1)
            interaction = FakeInteraction()
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_classifier_health", return_value={"ok": True, "model_loaded": True}):
                run(cog.task_stats.callback(cog, interaction))
            content = interaction.response.sent[0]["content"]
            self.assertIn("Labels since last retrain: 2", content)
            self.assertIn("Retrain ready: no", content)


if __name__ == "__main__":
    unittest.main()
