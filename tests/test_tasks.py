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

    async def send(self, content=None, ephemeral=False, file=None):
        self.sent.append({"content": content, "ephemeral": ephemeral, "file": file})


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

    async def create_thread(self, *, name, auto_archive_duration=4320,
                            slowmode_delay=None, reason=None):
        # Same signature as discord.Message.create_thread: it has no
        # ``type`` option, so passing one raises TypeError like the real thing.
        self.thread = FakeThread(999)
        return self.thread


class FakeThread:
    def __init__(self, thread_id=999):
        self.id = thread_id
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakePartial:
    def __init__(self, channel, message_id):
        self.channel = channel
        self.id = message_id

    async def delete(self):
        self.channel.deleted_ids.append(self.id)


class FakeChannel:
    def __init__(self, channel_id):
        self.id = channel_id
        self.name = "general"
        self.sent = []
        self.deleted_ids = []
        self.guild = FakeGuild(guild_id=777)

    _counter = 200

    @classmethod
    def next_id(cls):
        # Real Discord message ids are unique across channels.
        cls._counter += 1
        return cls._counter

    def get_partial_message(self, message_id):
        return FakePartial(self, message_id)

    async def send(self, embed=None, view=None):
        message = FakeMessage(message_id=FakeChannel.next_id(), content="")
        message.embeds = [embed] if embed is not None else []
        message.channel = self
        self.sent.append({"embed": embed, "view": view, "message": message})
        return message


class FakeGuild:
    def __init__(self, owner_id=1, guild_id=1):
        self.owner_id = owner_id
        self.id = guild_id


class FakeInteraction:
    def __init__(self, user=None, message=None, guild=None):
        self.user = user or FakeUser()
        self.message = message
        self.guild = guild
        self.guild_id = getattr(guild, "id", None)
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
    bot.channels[tasks_mod.TODO_CHANNEL_ID] = FakeChannel(tasks_mod.TODO_CHANNEL_ID)
    bot.channels[tasks_mod.REVIEW_CHANNEL_ID] = FakeChannel(tasks_mod.REVIEW_CHANNEL_ID)
    cog = Tasks(
        bot,
        config_override={"admin_user_ids": [151408413744693248]},
        labels_path=os.path.join(tmpdir, "task_labels.jsonl"),
        overrides_path=os.path.join(tmpdir, "task_overrides.json"),
        state_path=os.path.join(tmpdir, "tasks_state.json"),
        retrain_marker_path=os.path.join(tmpdir, "last_retrain.json"),
        todo_markdown_path=os.path.join(tmpdir, "TODO.md"),
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
            channel = bot.channels[tasks_mod.TODO_CHANNEL_ID]
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
            channel = bot.channels[tasks_mod.REVIEW_CHANNEL_ID]
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

    def test_channels_are_the_agreed_ones(self):
        self.assertEqual(tasks_mod.WATCH_CHANNEL_ID, 1568142915631325355)
        self.assertEqual(tasks_mod.TODO_CHANNEL_ID, 1568332728573759621)
        self.assertEqual(tasks_mod.REVIEW_CHANNEL_ID, 1568329972928610546)

    def test_tasks_and_reviews_land_in_separate_channels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            message = FakeMessage(content="fix the door and maybe the wall")

            async def fake_classify(text):
                return {"ok": True, "pieces": [
                    {"text": "fix the door", "score": 0.9, "decision": "auto_task", "trigger": "none"},
                    {"text": "maybe the wall", "score": 0.5, "decision": "review", "trigger": "score_band"},
                ]}

            async def fake_wiki(method, path, **kwargs):
                if method == "GET":
                    return {"ok": True, "tasks": {"items": []}}
                return {"ok": True, "task": {"id": "task-0005", "title": "fix the door", "status": "open"}}

            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_classify", side_effect=fake_classify), \
                    patch.object(cog, "_wiki_api", side_effect=fake_wiki):
                run(cog.on_message(message))
            todo = bot.channels[tasks_mod.TODO_CHANNEL_ID].sent
            review = bot.channels[tasks_mod.REVIEW_CHANNEL_ID].sent
            self.assertEqual([s["embed"].title for s in todo], ["fix the door"])
            self.assertEqual([s["embed"].title for s in review], ["Needs review"])

    def test_auto_drop_posts_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            channel = bot.channels[tasks_mod.TODO_CHANNEL_ID]
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
            channel = bot.channels[tasks_mod.TODO_CHANNEL_ID]
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
            channel = bot.channels[tasks_mod.TODO_CHANNEL_ID]
            message = FakeMessage(content="fix the door")
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_classify", return_value=None):
                run(cog.on_message(message))
            self.assertEqual(len(channel.sent), 0)

    def test_duplicate_piece_is_not_posted_twice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            channel = bot.channels[tasks_mod.TODO_CHANNEL_ID]

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

    def test_thread_call_matches_the_installed_discord_py(self):
        import inspect
        import discord
        # The cog calls message.create_thread(name=..., auto_archive_duration=...).
        # Binding those to the real signature fails if discord.py rejects them,
        # which a hand-written fake would never notice.
        inspect.signature(discord.Message.create_thread).bind(
            None, name="Review: x", auto_archive_duration=1440)
        inspect.signature(discord.PartialMessage.create_thread).bind(
            None, name="Review: x", auto_archive_duration=1440)

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
            todo = bot.channels[tasks_mod.TODO_CHANNEL_ID]
            self.assertEqual(len(todo.sent), 1)
            self.assertEqual(todo.sent[0]["embed"].title,
                             "asteroid field is not an asteroid field")
            self.assertTrue(message.deleted)
            self.assertEqual(message.edits, [])
            self.assertNotIn("300", cog.state)
            record = cog.state[str(todo.sent[0]["message"].id)]
            self.assertEqual(record["kind"], "task")
            self.assertEqual(record["wiki_task_id"], "task-0002")
            labels = open(cog.labels_path, encoding="utf-8").read().strip().splitlines()
            self.assertEqual(len(labels), 1)
            self.assertEqual(json.loads(labels[0])["label"], 1)
            overrides = json.loads(open(cog.overrides_path, encoding="utf-8").read())
            self.assertEqual(overrides["asteroid field is not an asteroid field"], 1)

    def test_review_is_task_stays_in_place_if_todo_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            del bot.channels[tasks_mod.TODO_CHANNEL_ID]
            message = self._review_message(cog)
            bot.fetch_channel = AsyncMock(return_value=FakeThread(999))
            interaction = FakeInteraction(message=message)

            async def fake_wiki(method, path, **kwargs):
                return {"ok": True, "task": {"id": "task-0003", "title": "x", "status": "open"}}

            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=fake_wiki):
                run(cog.handle_review_button(interaction, "is_task"))
            self.assertFalse(message.deleted)
            self.assertEqual(len(message.edits), 1)
            self.assertEqual(cog.state["300"]["wiki_task_id"], "task-0003")

    def test_confirmed_task_keeps_its_source_link(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            message = self._review_message(cog)
            cog.state["300"]["source_url"] = "https://discord.com/channels/1/2/3"
            cog.state["300"]["source_author"] = "Steve"
            bot.fetch_channel = AsyncMock(return_value=FakeThread(999))
            interaction = FakeInteraction(message=message)

            async def fake_wiki(method, path, **kwargs):
                return {"ok": True, "task": {"id": "task-0004", "title": "x", "status": "open"}}

            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=fake_wiki):
                run(cog.handle_review_button(interaction, "is_task"))
            embed = bot.channels[tasks_mod.TODO_CHANNEL_ID].sent[0]["embed"]
            source = {f.name: f.value for f in embed.fields}["Source"]
            self.assertIn("https://discord.com/channels/1/2/3", source)
            self.assertIn("Steve", source)

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

    def test_review_non_admin_can_rule(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            message = self._review_message(cog)
            thread = FakeThread(999)
            bot.fetch_channel = AsyncMock(return_value=thread)
            other = FakeUser(user_id=999999, name="NotAdmin")
            interaction = FakeInteraction(user=other, message=message)
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}):
                run(cog.handle_review_button(interaction, "not_task"))
            self.assertTrue(message.deleted)
            self.assertTrue(thread.deleted)
            labels = open(cog.labels_path, encoding="utf-8").read().strip().splitlines()
            label = json.loads(labels[0])
            self.assertEqual(label["label"], 0)
            self.assertEqual(label["reviewer_id"], 999999)


class TaskButtonTests(unittest.TestCase):
    def _task_message(self, cog):
        message = FakeMessage(message_id=400, content="")
        message.channel = FakeChannel(tasks_mod.TODO_CHANNEL_ID)
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


class DoneAndMarkdownTests(unittest.TestCase):
    TASKS = [
        {"id": "task-0001", "title": "fix the door", "status": "open",
         "priority": "low", "assignees": []},
        {"id": "task-0002", "title": "paint\nthe wall", "status": "in_progress",
         "priority": "high", "assignees": ["Thea", "Sam"]},
        {"id": "task-0003", "title": "already finished", "status": "done",
         "priority": "medium", "assignees": []},
        {"id": "task-0004", "title": "add npcs", "status": "open",
         "priority": "high", "assignees": []},
    ]

    def _add(self, cog, message_id, task_id):
        cog.state[str(message_id)] = {
            "kind": "task", "wiki_task_id": task_id, "source_message_id": 111,
            "piece": task_id, "score": 0.9, "trigger": "none",
            "thread_id": None, "status": "open"}

    def _wiki(self, tasks, calls=None):
        async def fake(method, path, **kwargs):
            if calls is not None:
                calls.append((method, path))
            if method == "GET":
                return {"ok": True, "tasks": {"items": list(tasks), "counts": {}}}
            return {"ok": True, "task": {"id": "task-0002", "status": "done"}}
        return fake

    def _done_message(self, cog):
        message = FakeMessage(message_id=400, content="")
        message.embeds = [build_task_embed("paint the wall", wiki_task_id="task-0002")]
        self._add(cog, 400, "task-0002")
        return message

    def test_done_button_deletes_message_and_keeps_wiki_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            message = self._done_message(cog)
            interaction = FakeInteraction(message=message)
            calls = []
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=self._wiki([], calls)):
                run(cog.handle_task_button(interaction, "done"))
            self.assertTrue(message.deleted)
            self.assertEqual(message.edits, [])
            self.assertNotIn("400", cog.state)
            self.assertIn(("PATCH", "/api/tasks/task-0002"), calls)
            self.assertNotIn(("DELETE", "/api/tasks/task-0002"), calls)
            self.assertIn("removed from #todo", interaction.followup.sent[0]["content"])

    def test_done_button_falls_back_to_edit_if_delete_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            message = self._done_message(cog)

            async def boom():
                raise RuntimeError("no permission")
            message.delete = boom
            interaction = FakeInteraction(message=message)
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=self._wiki([])):
                run(cog.handle_task_button(interaction, "done"))
            self.assertEqual(len(message.edits), 1)
            self.assertIn("400", cog.state)
            self.assertEqual(interaction.followup.sent[0]["content"], "Marked done.")

    def test_in_progress_button_keeps_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            message = self._done_message(cog)
            interaction = FakeInteraction(message=message)
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=self._wiki([])):
                run(cog.handle_task_button(interaction, "in_progress"))
            self.assertFalse(message.deleted)
            self.assertEqual(len(message.edits), 1)

    def test_task_done_command_deletes_the_todo_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            self._add(cog, 401, "task-0002")
            self._add(cog, 402, "task-0001")
            interaction = FakeInteraction()
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=self._wiki([])):
                run(cog.task_done.callback(cog, interaction, "task-0002"))
            channel = bot.channels[tasks_mod.TODO_CHANNEL_ID]
            self.assertEqual(channel.deleted_ids, [401])
            self.assertNotIn("401", cog.state)
            self.assertIn("402", cog.state)

    def test_refresh_clears_messages_of_tasks_done_on_the_wiki(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            self._add(cog, 403, "task-0003")
            self._add(cog, 404, "task-0001")
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=self._wiki(self.TASKS)):
                run(cog.refresh_todo())
            channel = bot.channels[tasks_mod.TODO_CHANNEL_ID]
            self.assertEqual(channel.deleted_ids, [403])
            self.assertEqual(list(cog.state), ["404"])

    def test_markdown_lists_unfinished_tasks_in_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            self._add(cog, 404, "task-0001")
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=self._wiki(self.TASKS)):
                run(cog.refresh_todo())
            text = open(cog.todo_markdown_path, encoding="utf-8").read()
            self.assertIn("**2 open, 1 in progress**", text)
            self.assertNotIn("already finished", text)
            self.assertIn("- [ ] **task-0002** paint the wall (high; Thea, Sam)", text)
            self.assertLess(text.index("task-0004"), text.index("task-0001"))
            self.assertLess(text.index("## In progress"), text.index("## Open"))
            self.assertIn("(low) - [message](https://discord.com/channels/777/"
                          f"{tasks_mod.TODO_CHANNEL_ID}/404)", text)

    def test_markdown_only_rewritten_when_the_list_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}):
                with patch.object(cog, "_wiki_api", side_effect=self._wiki(self.TASKS)):
                    run(cog.refresh_todo())
                    old = os.path.getmtime(cog.todo_markdown_path) - 100
                    os.utime(cog.todo_markdown_path, (old, old))
                    run(cog.refresh_todo())
                    self.assertEqual(os.path.getmtime(cog.todo_markdown_path), old)
                with patch.object(cog, "_wiki_api",
                                  side_effect=self._wiki(self.TASKS[:2])):
                    run(cog.refresh_todo())
                self.assertGreater(os.path.getmtime(cog.todo_markdown_path), old + 50)

    def test_wiki_outage_leaves_file_and_messages_alone(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            self._add(cog, 403, "task-0003")

            async def down(method, path, **kwargs):
                raise WikiApiError(0, "wiki backend unreachable")
            with patch.object(cog, "_wiki_api", side_effect=down):
                run(cog.refresh_todo())
            self.assertFalse(os.path.exists(cog.todo_markdown_path))
            self.assertEqual(bot.channels[tasks_mod.TODO_CHANNEL_ID].deleted_ids, [])
            self.assertIn("403", cog.state)


class PersonalChannelTests(unittest.TestCase):
    THEA = 151408413744693248
    JUSTIN = 120661885573005316
    STEVE = 168897667387097089
    STRANGER = 4242

    def _setup(self, tmpdir):
        cog, bot = make_cog(tmpdir)
        names = {self.THEA: "todo-thea", self.JUSTIN: "todo-justin",
                 self.STEVE: "todo-steve"}
        for user_id, name in names.items():
            channel = FakeChannel(tasks_mod.PERSONAL_CHANNELS[user_id])
            channel.name = name
            bot.channels[channel.id] = channel
        todo = bot.channels[tasks_mod.TODO_CHANNEL_ID]
        todo.name = "todo"
        return cog, bot, todo

    def _task_message(self, cog, channel, message_id=500, claimers=None):
        message = FakeMessage(message_id=message_id, content="")
        message.channel = channel
        source = FakeMessage(message_id=111)
        embed = build_task_embed("fix the door", wiki_task_id="task-0001",
                                 source_message=source)
        message.embeds = [embed]
        cog.state[str(message_id)] = {
            "kind": "task", "wiki_task_id": "task-0001", "source_message_id": 111,
            "piece": "fix the door", "score": 0.9, "trigger": "none",
            "thread_id": None, "status": "open", "channel_id": channel.id,
            "claimers": list(claimers or [])}
        return message

    def _wiki(self, task):
        async def fake(method, path, **kwargs):
            if method == "PATCH":
                body = kwargs.get("json") or {}
                if "assignees" in body:
                    task["assignees"] = body["assignees"]
                if "status" in body:
                    task["status"] = body["status"]
            return {"ok": True, "task": dict(task)}
        return fake

    def _click(self, cog, message, user_id, name, action, task):
        interaction = FakeInteraction(user=FakeUser(user_id, name), message=message)
        with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                patch.object(cog, "_wiki_api", side_effect=self._wiki(task)):
            run(cog.handle_task_button(interaction, action))
        return interaction

    def _task(self):
        return {"id": "task-0001", "title": "fix the door",
                "status": "open", "assignees": []}

    def test_claim_moves_task_to_the_claimers_channel(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot, todo = self._setup(tmpdir)
            message = self._task_message(cog, todo)
            interaction = self._click(cog, message, self.THEA, "Thea", "claim", self._task())
            mine = bot.channels[tasks_mod.PERSONAL_CHANNELS[self.THEA]]
            self.assertEqual(len(mine.sent), 1)
            self.assertTrue(message.deleted)
            self.assertNotIn("500", cog.state)
            new_id = str(mine.sent[0]["message"].id)
            self.assertEqual(cog.state[new_id]["channel_id"], mine.id)
            self.assertEqual(cog.state[new_id]["claimers"], [self.THEA])
            self.assertIn("#todo-thea", interaction.followup.sent[0]["content"])
            fields = {f.name: f.value for f in mine.sent[0]["embed"].fields}
            self.assertEqual(fields["Assignees"], "Thea")
            self.assertIn("Source", fields)

    def test_claim_by_someone_without_a_channel_stays_put(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot, todo = self._setup(tmpdir)
            message = self._task_message(cog, todo)
            self._click(cog, message, self.STRANGER, "Peach", "claim", self._task())
            self.assertFalse(message.deleted)
            self.assertEqual(len(message.edits), 1)
            self.assertEqual(todo.sent, [])
            self.assertIn("500", cog.state)

    def test_second_claimer_does_not_move_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot, todo = self._setup(tmpdir)
            task = self._task()
            message = self._task_message(cog, todo)
            self._click(cog, message, self.THEA, "Thea", "claim", task)
            mine = bot.channels[tasks_mod.PERSONAL_CHANNELS[self.THEA]]
            moved = mine.sent[0]["message"]
            self._click(cog, moved, self.STEVE, "Steve", "claim", task)
            steves = bot.channels[tasks_mod.PERSONAL_CHANNELS[self.STEVE]]
            self.assertEqual(steves.sent, [])
            self.assertFalse(moved.deleted)
            self.assertEqual(cog.state[str(moved.id)]["claimers"], [self.THEA, self.STEVE])

    def test_any_unclaim_sends_it_back_to_todo_even_with_another_claimer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot, todo = self._setup(tmpdir)
            task = self._task()
            message = self._task_message(cog, todo)
            self._click(cog, message, self.THEA, "Thea", "claim", task)
            mine = bot.channels[tasks_mod.PERSONAL_CHANNELS[self.THEA]]
            moved = mine.sent[0]["message"]
            self._click(cog, moved, self.STEVE, "Steve", "claim", task)
            self._click(cog, moved, self.THEA, "Thea", "claim", task)
            steves = bot.channels[tasks_mod.PERSONAL_CHANNELS[self.STEVE]]
            self.assertEqual(steves.sent, [])
            self.assertEqual(len(todo.sent), 1)
            self.assertTrue(moved.deleted)
            back = todo.sent[0]["message"]
            self.assertEqual(cog.state[str(back.id)]["claimers"], [self.STEVE])
            # The next claim sends it to the first remaining claimer's channel.
            self._click(cog, back, self.JUSTIN, "Justin", "claim", task)
            self.assertEqual(len(steves.sent), 1)
            self.assertTrue(back.deleted)

    def test_last_unclaim_sends_it_back_to_todo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot, todo = self._setup(tmpdir)
            task = self._task()
            message = self._task_message(cog, todo)
            self._click(cog, message, self.THEA, "Thea", "claim", task)
            mine = bot.channels[tasks_mod.PERSONAL_CHANNELS[self.THEA]]
            moved = mine.sent[0]["message"]
            self._click(cog, moved, self.THEA, "Thea", "claim", task)
            self.assertEqual(len(todo.sent), 1)
            self.assertTrue(moved.deleted)
            back = todo.sent[0]["message"]
            self.assertEqual(cog.state[str(back.id)]["claimers"], [])
            fields = {f.name: f.value for f in todo.sent[0]["embed"].fields}
            self.assertNotIn("Assignees", fields)

    def test_in_progress_keeps_the_message_in_the_personal_channel(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot, todo = self._setup(tmpdir)
            task = self._task()
            message = self._task_message(cog, todo)
            self._click(cog, message, self.THEA, "Thea", "claim", task)
            mine = bot.channels[tasks_mod.PERSONAL_CHANNELS[self.THEA]]
            moved = mine.sent[0]["message"]
            self._click(cog, moved, self.THEA, "Thea", "in_progress", task)
            self.assertFalse(moved.deleted)
            self.assertEqual(len(moved.edits), 1)
            values = {f.name: f.value for f in moved.edits[0]["embed"].fields}
            self.assertEqual(values["Status"], "in_progress")
            self.assertIn("Source", values)
            self.assertEqual(len(mine.sent), 1)

    def test_done_deletes_the_message_in_the_personal_channel(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot, todo = self._setup(tmpdir)
            task = self._task()
            message = self._task_message(cog, todo)
            self._click(cog, message, self.THEA, "Thea", "claim", task)
            mine = bot.channels[tasks_mod.PERSONAL_CHANNELS[self.THEA]]
            moved = mine.sent[0]["message"]
            self._click(cog, moved, self.THEA, "Thea", "done", task)
            self.assertTrue(moved.deleted)
            self.assertEqual(cog.state, {})

    def test_failed_post_keeps_the_task_where_it_was(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot, todo = self._setup(tmpdir)
            mine = bot.channels[tasks_mod.PERSONAL_CHANNELS[self.THEA]]

            async def boom(embed=None, view=None):
                raise RuntimeError("missing permissions")
            mine.send = boom
            message = self._task_message(cog, todo)
            self._click(cog, message, self.THEA, "Thea", "claim", self._task())
            self.assertFalse(message.deleted)
            self.assertEqual(len(message.edits), 1)
            self.assertIn("500", cog.state)

    def test_every_personal_channel_is_a_distinct_known_channel(self):
        ids = list(tasks_mod.PERSONAL_CHANNELS.values())
        self.assertEqual(len(ids), len(set(ids)))
        for banned in (tasks_mod.TODO_CHANNEL_ID, tasks_mod.WATCH_CHANNEL_ID,
                       tasks_mod.REVIEW_CHANNEL_ID):
            self.assertNotIn(banned, ids)


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

    def test_task_add_non_admin_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            other = FakeUser(user_id=999999, name="NotAdmin")
            interaction = FakeInteraction(user=other)

            async def fake_wiki(method, path, **kwargs):
                return {"ok": True, "task": {"id": "task-0003", "title": "x"}}

            with patch.dict(os.environ, {"WIKI_API_KEY": "testkey"}), \
                    patch.object(cog, "_wiki_api", side_effect=fake_wiki):
                run(cog.task_add.callback(cog, interaction, title="x"))
            self.assertIn("task-0003", interaction.response.sent[0]["content"])

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


class TodoCommandTests(unittest.TestCase):
    def run_todo(self, guild_id, markdown="# To-do\n- fix the door\n", fail_refresh=False):
        with tempfile.TemporaryDirectory() as tmpdir:
            cog, bot = make_cog(tmpdir)
            if markdown is not None:
                with open(cog.todo_markdown_path, "w", encoding="utf-8") as handle:
                    handle.write(markdown)
            interaction = FakeInteraction(guild=FakeGuild(guild_id=guild_id))
            refreshed = []

            async def fake_refresh():
                refreshed.append(True)

            with patch.object(cog, "refresh_todo", side_effect=fake_refresh):
                run(cog.todo.callback(cog, interaction))
            file = interaction.followup.sent[0]["file"] if interaction.followup.sent else None
            data = file.fp.read() if file else None
            return interaction, refreshed, file, data

    def test_serves_the_markdown_file_to_the_requester_only(self):
        interaction, refreshed, file, data = self.run_todo(tasks_mod.TEAM_GUILD_ID)
        self.assertTrue(interaction.response.deferred)
        self.assertEqual(refreshed, [True])
        self.assertEqual(file.filename, "TODO.md")
        self.assertIn(b"fix the door", data)
        self.assertTrue(interaction.followup.sent[0]["ephemeral"])

    def test_other_servers_are_refused(self):
        interaction, refreshed, file, data = self.run_todo(12345)
        self.assertIsNone(file)
        self.assertEqual(refreshed, [])
        self.assertIn("mcdungeons", interaction.response.sent[0]["content"])
        self.assertTrue(interaction.response.sent[0]["ephemeral"])

    def test_a_missing_list_says_so_instead_of_failing(self):
        interaction, refreshed, file, data = self.run_todo(tasks_mod.TEAM_GUILD_ID, markdown=None)
        self.assertIsNone(file)
        self.assertIn("not available", interaction.followup.sent[0]["content"])


if __name__ == "__main__":
    unittest.main()
