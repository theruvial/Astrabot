"""Tests for cogs/homeassistant.py: routing, replies, errors, confirm buttons.

No real Home Assistant, Ollama or Discord: the agent is a stand-in object.
"""
import asyncio
import sys
import types
import unittest
from unittest import mock

import aiohttp

from cogs import homeassistant as hac

THEA = 151408413744693248
BOT = types.SimpleNamespace(id=999, bot=True)


def settings(**over):
    s = hac.load_settings({"homeassistant": {"allowed_user_ids": [THEA], "channel_id": 42}}, env={})
    s.update(over)
    return s


def message(author_id=THEA, guild=True, channel_id=7, mentions=(), bot=False, content="hi"):
    channel = types.SimpleNamespace(id=channel_id, guild=object() if guild else None)
    return types.SimpleNamespace(
        author=types.SimpleNamespace(id=author_id, bot=bot, display_name="Thea"),
        guild=channel.guild, channel=channel, mentions=list(mentions), content=content)


class SettingsTests(unittest.TestCase):
    def test_defaults_allow_only_thea_and_read_the_token_from_env(self):
        s = hac.load_settings({}, env={"HA_TOKEN": " tok "})
        self.assertEqual(s["allowed"], frozenset({THEA}))
        self.assertEqual(s["ha_token"], "tok")
        self.assertEqual(s["channel_id"], 0)
        self.assertFalse(s["fake_ha"])

    def test_config_token_wins_and_bad_ids_are_skipped(self):
        s = hac.load_settings({"homeassistant": {"ha_token": "cfg", "allowed_user_ids": ["5", "x"],
                                                 "channel_id": "nope"}}, env={"HA_TOKEN": "env"})
        self.assertEqual(s["ha_token"], "cfg")
        self.assertEqual(s["allowed"], frozenset({5}))
        self.assertEqual(s["channel_id"], 0)

    def test_an_empty_allowlist_means_nobody(self):
        self.assertEqual(hac.load_settings({"homeassistant": {"allowed_user_ids": []}}, env={})["allowed"],
                         frozenset())


class RoutingTests(unittest.TestCase):
    def test_dms_only_from_dm_user_ids(self):
        self.assertTrue(hac.should_handle(message(guild=False), BOT, settings()))
        s = settings(allowed=frozenset({THEA, 5}))
        self.assertFalse(hac.should_handle(message(author_id=5, guild=False), BOT, s))
        self.assertTrue(hac.should_handle(message(author_id=5, guild=False), BOT,
                                          settings(dm_users=frozenset({5}))))

    def test_dm_user_ids_default_to_the_owner(self):
        self.assertEqual(hac.load_settings({}, env={})["dm_users"], frozenset({THEA}))
        self.assertEqual(hac.load_settings({"owner_id": 9}, env={})["dm_users"], frozenset({9}))
        self.assertEqual(hac.load_settings({"homeassistant": {"dm_user_ids": ["3", "x"]}},
                                           env={})["dm_users"], frozenset({3}))

    def test_strangers_and_bots_are_ignored_everywhere(self):
        for msg in (message(author_id=1, guild=False), message(bot=True, guild=False),
                    message(author_id=BOT.id, guild=False), message(author_id=1, channel_id=42)):
            self.assertFalse(hac.should_handle(msg, BOT, settings()))

    def test_only_the_configured_channel(self):
        self.assertTrue(hac.should_handle(message(channel_id=42), BOT, settings()))
        self.assertTrue(hac.should_handle(message(channel_id=42, mentions=[BOT]), BOT, settings()))
        self.assertFalse(hac.should_handle(message(mentions=[BOT]), BOT, settings()))
        self.assertFalse(hac.should_handle(message(), BOT, settings()))
        self.assertFalse(hac.should_handle(message(channel_id=0), BOT, settings(channel_id=0)))

    def test_strip_mention_both_forms(self):
        self.assertEqual(hac.strip_mention("<@999> turn off the fan", 999), "turn off the fan")
        self.assertEqual(hac.strip_mention("hey <@!999>", 999), "hey")
        self.assertEqual(hac.strip_mention("<@123> hi", 999), "<@123> hi")

    def test_conversation_ids(self):
        user = types.SimpleNamespace(id=5)
        self.assertEqual(hac.conversation_id(message(guild=False).channel, user), "discord:dm:5")
        self.assertEqual(hac.conversation_id(message(channel_id=8).channel, user), "discord:8")


def reply(text="Done it.", actions=(), seconds=2.34):
    return types.SimpleNamespace(text=text, actions=list(actions), tool_calls=1, seconds=seconds)


def act(action, targets, options, ok=True, result="ok"):
    return types.SimpleNamespace(action=action, targets=targets, options=options, ok=ok, result=result)


class ReplyTests(unittest.TestCase):
    def test_split_keeps_every_part_under_the_limit_and_loses_nothing(self):
        text = "\n".join(f"line {i} " + "x" * 50 for i in range(100))
        parts = hac.split_message(text)
        self.assertTrue(all(len(p) <= 2000 for p in parts))
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(parts).replace("\n", ""), text.replace("\n", ""))

    def test_split_a_long_word_hard(self):
        parts = hac.split_message("y" * 4500)
        self.assertEqual([len(p) for p in parts], [2000, 2000, 500])

    def test_footer_lists_actions_and_time(self):
        footer = hac.action_footer(reply(actions=[
            act("fan.set_percentage", ["Air Purifier"], {"percentage": 40}),
            act("lock.unlock", ["Front Door"], {}, ok=False, result="declined")]))
        self.assertIn("✅ fan.set\\_percentage → Air Purifier {percentage: 40}", footer)
        self.assertIn("❌ lock.unlock → Front Door (declined)", footer)
        self.assertTrue(footer.endswith("-# 2.3 s"))

    def test_empty_text_says_done(self):
        self.assertTrue(hac.reply_messages(reply(text="  "))[0].startswith("Done."))

    def test_error_texts_never_include_the_exception(self):
        cases = {
            hac.HAError("401 token secret-ish"): "Home Assistant isn't responding.",
            asyncio.TimeoutError(): "That took too long, try again.",
            RuntimeError("Ollama error 500: {}"): "The local model isn't running. Start Strata on Golurk.",
            RuntimeError("Model server error 500: engine stopped"):
                "The local model isn't running. Start Strata on Golurk.",
            aiohttp.ClientConnectorError(mock.Mock(), OSError("refused")):
                "The local model isn't running. Start Strata on Golurk.",
        }
        for exc, want in cases.items():
            self.assertEqual(hac.error_text(exc), want)
        self.assertNotIn("boom", hac.error_text(ValueError("boom")))


class FakeAgent:
    def __init__(self, start_errors=0, chat=None):
        self.start_errors = start_errors
        self.starts = 0
        self.chat_impl = chat or (lambda *a, **k: reply())
        self.calls = []
        self.forgotten = []
        self.thinks = []
        self.toggles = {}
        self.closed = False

    async def start(self):
        self.starts += 1
        if self.starts <= self.start_errors:
            raise hac.HAError("could not reach Home Assistant")

    async def chat(self, cid, text, confirm, speaker, think=None, **kw):
        self.modes_used = getattr(self, "modes_used", []) + [kw.get("mode")]
        self.calls.append((cid, text, speaker))
        self.thinks.append(think)
        result = self.chat_impl(cid, text, confirm, speaker)
        return await result if asyncio.iscoroutine(result) else result

    def forget(self, cid):
        self.forgotten.append(cid)

    def set_thinking(self, cid, on=None):
        new = (not self.toggles.get(cid, False)) if on is None else bool(on)
        self.toggles[cid] = new
        return new

    async def close(self):
        self.closed = True


async def no_confirm(_summary):
    return False


class AnswerTests(unittest.IsolatedAsyncioTestCase):
    def cog(self, agent, **over):
        return hac.HomeAssistant(bot=None, settings=settings(**over), agent=agent)

    async def test_a_failed_start_keeps_the_cog_and_retries_next_message(self):
        agent = FakeAgent(start_errors=1)
        cog = self.cog(agent)
        with self.assertLogs("homeassistant", "WARNING"):
            await cog.cog_load()
        self.assertFalse(cog.started)
        parts = await cog.answer("discord:1", "hi", message().author, no_confirm)
        self.assertEqual(parts[0].split("\n")[0], "Done it.")
        self.assertEqual(agent.starts, 2)
        self.assertEqual(agent.calls, [("discord:1", "hi", "Thea")])

    async def test_ha_still_down_says_so(self):
        cog = self.cog(FakeAgent(start_errors=5))
        with self.assertLogs("homeassistant", "WARNING"):
            parts = await cog.answer("c", "hi", message().author, no_confirm)
        self.assertIn("isn't reachable", parts[0])

    async def test_errors_become_short_messages(self):
        def boom(*_):
            raise RuntimeError("Ollama error 404: model not found")
        cog = self.cog(FakeAgent(chat=boom))
        with self.assertLogs("homeassistant", "ERROR"):
            parts = await cog.answer("c", "hi", message().author, no_confirm)
        self.assertEqual(parts, ["The local model isn't running. Start Strata on Golurk."])

    async def test_the_timeout_is_a_safety_net(self):
        async def slow(*_):
            await asyncio.sleep(5)
        cog = self.cog(FakeAgent(chat=lambda *a: slow()), timeout=0.05)
        with self.assertLogs("homeassistant", "ERROR"):
            parts = await cog.answer("c", "hi", message().author, no_confirm)
        self.assertEqual(parts, ["That took too long, try again."])

    async def test_no_token_turns_it_off_with_a_reason(self):
        cog = self.cog(None, ha_token=None)
        with self.assertLogs("homeassistant", "WARNING"):
            await cog.cog_load()
        self.assertIsNone(cog.agent)
        self.assertIn("HA_TOKEN", (await cog.answer("c", "hi", message().author, no_confirm))[0])

    async def test_unload_closes_the_agent(self):
        agent = FakeAgent()
        with mock.patch.dict(sys.modules):   # unload drops ha_agent; put it back afterwards
            await self.cog(agent).cog_unload()
        self.assertTrue(agent.closed)

    async def test_unload_drops_ha_agent_so_a_cog_reload_reloads_it(self):
        fake = types.ModuleType("x")
        with mock.patch.dict(sys.modules, {"ha_agent": fake, "ha_agent.agent": fake,
                                           "ha_agent_fake_ha": fake, "ha_agentish": fake}):
            await self.cog(FakeAgent()).cog_unload()
            left = {n for n in sys.modules if n.startswith("ha_agent")}
        self.assertEqual(left, {"ha_agentish"})

    async def test_unload_drops_ha_agent_even_if_close_fails(self):
        agent = FakeAgent()

        async def broken():
            raise RuntimeError("boom")

        agent.close = broken
        with mock.patch.dict(sys.modules, {"ha_agent": types.ModuleType("x")}):
            with self.assertLogs("homeassistant", "ERROR"):
                await self.cog(agent).cog_unload()
            self.assertNotIn("ha_agent", sys.modules)


class ThinkingTests(unittest.IsolatedAsyncioTestCase):
    def test_think_choice(self):
        self.assertEqual(hac.think_choice(None), ("toggle", None))
        self.assertEqual(hac.think_choice("  "), ("toggle", None))
        self.assertEqual(hac.think_choice("ON"), ("set", True))
        self.assertEqual(hac.think_choice("off"), ("set", False))
        self.assertEqual(hac.think_choice(" is it going to rain? "), ("once", "is it going to rain?"))

    def test_a_thought_reply_gets_a_brain_and_a_spoiler(self):
        r = reply(seconds=13.04)
        r.thought, r.thinking = True, "The user wants || rain || info."
        parts = hac.reply_messages(r)
        r.tool_calls = 0
        self.assertIn(f"-# {hac.BRAIN} · 13.0 s", hac.reply_messages(r)[0])
        self.assertEqual(parts[-1], f"-# {hac.BRAIN} Reasoning (tap to show)\n"
                                    "||The user wants | | rain | | info.||")

    def test_no_brain_or_spoiler_without_thinking(self):
        r = reply()
        r.thought, r.thinking = False, "stale"
        parts = hac.reply_messages(r)
        self.assertEqual(len(parts), 1)
        self.assertNotIn(hac.BRAIN, parts[0])

    def test_long_reasoning_is_cut_to_fit_one_message(self):
        msg = hac.reasoning_message("x" * 9000)
        self.assertLessEqual(len(msg), 2000)
        self.assertTrue(msg.endswith("…||"))
        self.assertIsNone(hac.reasoning_message("   "))

    async def test_answer_passes_think_through(self):
        agent = FakeAgent()
        cog = hac.HomeAssistant(bot=None, settings=settings(), agent=agent)
        await cog.answer("c", "hi", message().author, no_confirm, think=True)
        await cog.answer("c", "hi", message().author, no_confirm)
        self.assertEqual(agent.thinks, [True, None])

    async def think_command(self, cog, user_id, message=None):
        inter = interaction(user_id)
        inter.channel = types.SimpleNamespace(id=42, guild=object())
        await hac.HomeAssistant.think.callback(cog, inter, message)
        return inter

    async def test_think_toggles_and_sets(self):
        agent = FakeAgent()
        cog = hac.HomeAssistant(bot=None, settings=settings(), agent=agent)
        for arg, want in ((None, "ON"), (None, "OFF"), ("on", "ON"), ("off", "OFF")):
            inter = await self.think_command(cog, THEA, arg)
            self.assertIn(f"now {want}", inter.response.send_message.call_args.args[0])
        self.assertEqual(agent.toggles, {"discord:42": False})

    async def test_think_with_a_message_thinks_once_without_the_toggle(self):
        agent = FakeAgent()
        cog = hac.HomeAssistant(bot=None, settings=settings(), agent=agent)
        inter = interaction(THEA)
        inter.channel = types.SimpleNamespace(id=42, guild=object())
        inter.response.defer = mock.AsyncMock()
        inter.followup = types.SimpleNamespace(send=mock.AsyncMock())
        await hac.HomeAssistant.think.callback(cog, inter, "will it rain?")
        self.assertEqual(agent.calls, [("discord:42", "will it rain?", "Thea")])
        self.assertEqual(agent.thinks, [True])
        self.assertEqual(agent.toggles, {})

    async def test_strangers_cannot_think(self):
        agent = FakeAgent()
        cog = hac.HomeAssistant(bot=None, settings=settings(), agent=agent)
        inter = await self.think_command(cog, 1)
        self.assertEqual(inter.response.send_message.call_args.args[0], "Not allowed.")
        self.assertEqual(agent.toggles, {})


def interaction(user_id):
    resp = types.SimpleNamespace(send_message=mock.AsyncMock(), edit_message=mock.AsyncMock())
    return types.SimpleNamespace(user=types.SimpleNamespace(id=user_id, display_name="Thea"),
                                 response=resp,
                                 message=types.SimpleNamespace(content="Allow **x**?"))


class ConfirmTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_asker_can_press(self):
        view = hac.ConfirmView(THEA)
        other = interaction(1)
        self.assertFalse(await view.interaction_check(other))
        self.assertTrue(other.response.send_message.call_args.kwargs["ephemeral"])
        self.assertTrue(await view.interaction_check(interaction(THEA)))

    async def press(self, button):
        sent = {}

        async def send(content, view):
            sent["view"] = view
            sent["content"] = content
            return mock.AsyncMock()

        confirm = hac.make_confirm(send, THEA, timeout=5)
        task = asyncio.create_task(confirm("homeassistant.restart on Home Assistant"))
        while "view" not in sent:
            await asyncio.sleep(0)
        click = interaction(THEA)
        await getattr(sent["view"], button).callback(click)
        result = await task
        self.assertTrue(all(item.disabled for item in sent["view"].children))
        return result, sent, click

    async def test_allow_returns_true_and_edits_the_message(self):
        result, sent, click = await self.press("allow")
        self.assertTrue(result)
        self.assertIn("homeassistant.restart", sent["content"])
        self.assertIn("Allowed", click.response.edit_message.call_args.kwargs["content"])

    async def test_deny_returns_false(self):
        result, _, click = await self.press("deny")
        self.assertFalse(result)
        self.assertIn("Denied", click.response.edit_message.call_args.kwargs["content"])

    async def test_timeout_returns_false_and_says_so(self):
        prompt = mock.AsyncMock()

        async def send(content, view):
            return prompt

        self.assertFalse(await hac.make_confirm(send, THEA, timeout=0.05)("x"))
        self.assertIn("No answer", prompt.edit.call_args.kwargs["content"])

    async def test_an_error_inside_confirm_is_false_not_raised(self):
        async def send(content, view):
            raise RuntimeError("discord down")
        with self.assertLogs("homeassistant", "ERROR"):
            self.assertFalse(await hac.make_confirm(send, THEA)("x"))


class Place:
    """A fake channel or thread that records what is sent to it."""

    def __init__(self, id, parent_id=None):
        self.id, self.parent_id, self.guild = id, parent_id, object()
        self.sent = []

    async def send(self, content, view=None, allowed_mentions=None):
        self.sent.append(content)
        return content

    def typing(self):
        class Typing:
            async def __aenter__(self_):
                return None

            async def __aexit__(self_, *exc):
                return False
        return Typing()


def chat_message(channel, content="turn off the fan\nplease", thread=None, thread_error=None):
    msg = types.SimpleNamespace(
        author=types.SimpleNamespace(id=THEA, bot=False, display_name="Thea"),
        guild=channel.guild, channel=channel, mentions=[], content=content,
        replies=[], thread_calls=[], id=1000)

    async def reply(content, view=None, allowed_mentions=None):
        msg.replies.append(content)
        return content

    async def create_thread(name, auto_archive_duration):
        msg.thread_calls.append((name, auto_archive_duration))
        if thread_error:
            raise thread_error
        return thread

    msg.reply, msg.create_thread = reply, create_thread
    return msg


class ThreadTests(unittest.IsolatedAsyncioTestCase):
    def cog(self, agent):
        cog = hac.HomeAssistant(types.SimpleNamespace(user=BOT), settings(), agent=agent)
        cog.started = True
        return cog

    def test_agent_place(self):
        self.assertEqual(hac.agent_place(Place(42), 42), "channel")
        self.assertEqual(hac.agent_place(Place(50, parent_id=42), 42), "thread")
        self.assertIsNone(hac.agent_place(Place(50, parent_id=7), 42))
        self.assertIsNone(hac.agent_place(Place(42), 0))

    def test_threads_under_the_channel_need_no_mention(self):
        msg = chat_message(Place(50, parent_id=42))
        self.assertTrue(hac.should_handle(msg, BOT, settings()))
        msg.author = types.SimpleNamespace(id=1, bot=False)
        self.assertFalse(hac.should_handle(msg, BOT, settings()))

    def test_thread_name(self):
        self.assertEqual(hac.thread_name("turn  off the fan\nplease"), "turn off the fan")
        long = hac.thread_name("x " * 80)
        self.assertLessEqual(len(long), 100)
        self.assertTrue(long.endswith("…"))
        self.assertEqual(hac.thread_name("   "), "House request")

    def test_history_turns_setting(self):
        self.assertEqual(hac.load_settings({}, env={})["history_turns"], 6)
        self.assertEqual(hac.load_settings({"homeassistant": {"history_turns": "20"}}, env={})["history_turns"], 20)
        self.assertEqual(hac.load_settings({"homeassistant": {"history_turns": "x"}}, env={})["history_turns"], 6)

    async def test_a_channel_message_opens_a_thread_and_answers_there(self):
        agent, channel, thread = FakeAgent(), Place(42), Place(50, parent_id=42)
        msg = chat_message(channel, thread=thread)
        await self.cog(agent).on_message(msg)
        self.assertEqual(msg.thread_calls, [("turn off the fan", 1440)])
        self.assertEqual(agent.calls[0][0], "discord:50")
        self.assertTrue(thread.sent and thread.sent[0].startswith("Done it."))
        self.assertEqual(msg.replies, [])
        self.assertEqual(channel.sent, [])

    async def test_a_follow_up_in_the_thread_stays_in_it(self):
        agent, thread = FakeAgent(), Place(50, parent_id=42)
        msg = chat_message(thread, content="and the lamp")
        await self.cog(agent).on_message(msg)
        self.assertEqual(msg.thread_calls, [])
        self.assertEqual(agent.calls[0][0], "discord:50")
        self.assertEqual(len(msg.replies), 1)

    async def test_no_thread_permission_answers_in_the_channel(self):
        agent, channel = FakeAgent(), Place(42)
        denied = hac.discord.Forbidden(types.SimpleNamespace(status=403, reason="Forbidden"),
                                       "Missing Permissions")
        msg = chat_message(channel, thread_error=denied)
        with self.assertLogs("homeassistant", "WARNING"):
            await self.cog(agent).on_message(msg)
        self.assertEqual(agent.calls[0][0], "discord:42")
        self.assertEqual(len(msg.replies), 1)

    async def test_confirm_buttons_go_to_the_thread(self):
        async def asks(cid, text, confirm, speaker):
            await confirm("unlock the door")
            return reply()

        agent, thread = FakeAgent(chat=asks), Place(50, parent_id=42)
        msg = chat_message(Place(42), thread=thread)
        def quick_confirm(send, owner_id):
            async def confirm(summary):
                await send(f"Allow {summary}?", None)
                return False
            return confirm

        with mock.patch.object(hac, "make_confirm", quick_confirm):
            await self.cog(agent).on_message(msg)
        self.assertTrue(any("unlock the door" in str(m) for m in thread.sent), thread.sent)


def said(author_id, content, name="Thea", id=0):
    return types.SimpleNamespace(id=id, content=content,
                                 author=types.SimpleNamespace(id=author_id, display_name=name))


class HistoryAgent(FakeAgent):
    def __init__(self, known=()):
        super().__init__()
        self.known, self.remembered = set(known), []

    def has_history(self, cid):
        return cid in self.known

    def remember(self, cid, turns):
        self.remembered.append((cid, turns))


class FakeThread(Place):
    def __init__(self, messages, starter=None, error=None):
        super().__init__(50, parent_id=42)
        self.messages, self.error = messages, error
        parent = types.SimpleNamespace()

        async def fetch_message(mid):
            if starter is None:
                raise hac.discord.NotFound(types.SimpleNamespace(status=404, reason="nf"), "gone")
            return starter
        parent.fetch_message = fetch_message
        self.parent = parent

    def history(self, limit):
        if self.error:
            raise self.error
        newest_first = list(reversed(self.messages))[:limit]

        async def gen():
            for m in newest_first:
                yield m
        return gen()


class RestoreTests(unittest.IsolatedAsyncioTestCase):
    def test_thread_turns_keeps_the_conversation_only(self):
        answer = """It's 21 degrees.
-# ✅ sensor.read
-# 1.2 s"""
        msgs = [said(THEA, "<@999> how warm is it?"), said(BOT.id, answer),
                said(BOT.id, "Allow **unlock the door**?"), said(1, "random person", "Bob"),
                said(BOT.id, "||thinking||"), said(BOT.id, "Home Assistant isn't responding."),
                said(THEA, "thanks"), said(BOT.id, "part one"), said(BOT.id, "part two")]
        self.assertEqual(hac.thread_turns(msgs, BOT.id, frozenset({THEA})), [
            ("user", "Thea: how warm is it?"), ("assistant", "It's 21 degrees."),
            ("user", "Thea: thanks"), ("assistant", "part one" + chr(10) + "part two")])

    def test_timer_messages_are_not_agent_answers(self):
        msgs = [said(THEA, "set a timer for the pasta"), said(BOT.id, "Timer set for 10 minutes."),
                said(BOT.id, "⏰ Timer done: pasta (Thea). It ended at 1830 while I was offline.")]
        self.assertEqual(hac.thread_turns(msgs, BOT.id, frozenset({THEA})), [
            ("user", "Thea: set a timer for the pasta"),
            ("assistant", "Timer set for 10 minutes.")])

    def cog(self, agent):
        cog = hac.HomeAssistant(types.SimpleNamespace(user=BOT), settings(), agent=agent)
        cog.started = True
        return cog

    async def test_a_forgotten_thread_is_read_back_with_its_starter(self):
        agent = HistoryAgent()
        thread = FakeThread([said(BOT.id, "Fan off.", id=2), said(THEA, "and the lamp", id=1000)],
                            starter=said(THEA, "turn off the fan", id=50))
        await self.cog(agent).on_message(chat_message(thread, content="and the lamp"))
        self.assertEqual(agent.remembered, [("discord:50", [
            ("user", "Thea: turn off the fan"), ("assistant", "Fan off.")])])
        self.assertEqual(agent.calls[0][1], "and the lamp")

    async def test_a_remembered_thread_is_not_read_again(self):
        agent = HistoryAgent(known={"discord:50"})
        thread = FakeThread([], error=AssertionError("should not read"))
        await self.cog(agent).on_message(chat_message(thread, content="hi"))
        self.assertEqual(agent.remembered, [])

    async def test_an_unreadable_thread_still_gets_an_answer(self):
        agent = HistoryAgent()
        err = hac.discord.Forbidden(types.SimpleNamespace(status=403, reason="no"), "Missing Access")
        thread = FakeThread([], error=err)
        with self.assertLogs("homeassistant", "WARNING"):
            await self.cog(agent).on_message(chat_message(thread, content="hi"))
        self.assertEqual(agent.remembered, [])
        self.assertEqual(len(agent.calls), 1)

    async def test_a_new_thread_is_not_read_back(self):
        agent = HistoryAgent()
        await self.cog(agent).on_message(chat_message(Place(42), thread=Place(50, parent_id=42)))
        self.assertEqual(agent.remembered, [])

    async def test_slash_commands_outside_the_channel_point_to_it(self):
        cog = self.cog(FakeAgent())
        inter = interaction(THEA)
        inter.channel = types.SimpleNamespace(id=7, guild=object())
        await hac.HomeAssistant.think.callback(cog, inter, None)
        self.assertEqual(inter.response.send_message.call_args.args[0], "I only answer in <#42>.")


class ModeAgent(FakeAgent):
    def __init__(self):
        super().__init__()
        self.current = {}

    def mode(self, cid):
        return self.current.get(cid, "general")

    def set_mode(self, cid, name):
        name = name.strip().lower()
        if name not in ("general", "home", "code"):
            raise ValueError(f"unknown mode '{name}'")
        self.current[cid] = name
        return name


class ModeTests(unittest.IsolatedAsyncioTestCase):
    def test_footer_shows_the_mode_that_answered(self):
        r = reply(actions=[act("fan.turn_off", ["Fan"], {})])
        r.mode, r.routed_by, r.tool_calls = "home", "keywords", 2
        self.assertEqual(hac.footer_bits(r), ["\U0001f3e0 home (auto)", "\U0001f50e 1 tool", "2.3 s"])
        r.mode, r.routed_by, r.actions, r.tool_calls = "code", "", [], 0
        self.assertEqual(hac.footer_bits(r), ["\U0001f4bb code", "2.3 s"])
        r.mode, r.tool_calls = "general", 3
        self.assertEqual(hac.footer_bits(r)[:2], ["\U0001f4ac general", "\U0001f50e 3 tools"])

    def test_no_mode_on_older_agents(self):
        self.assertEqual(hac.footer_bits(reply()), ["\U0001f50e 1 tool", "2.3 s"])

    def test_split_never_breaks_a_code_block(self):
        nl = chr(10)
        code = nl.join(f"    line_{i} = {i}  # some code" for i in range(300))
        text = "Here you go:" + nl + "```python" + nl + code + nl + "```" + nl + "That's it."
        parts = hac.split_message(text)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), hac.DISCORD_LIMIT)
            fences = [l for l in part.split(nl) if l.lstrip().startswith("```")]
            self.assertEqual(len(fences) % 2, 0, part[-80:])
        self.assertTrue(parts[1].startswith("```python" + nl))
        body = [l for p in parts for l in p.split(nl) if not l.lstrip().startswith("```")]
        self.assertEqual(body, [l for l in text.split(nl) if not l.lstrip().startswith("```")])

    def test_a_long_unbroken_code_line_still_ends(self):
        nl = chr(10)
        parts = hac.split_message("```" + nl + "x" * 5000 + nl + "```")
        self.assertTrue(all(len(p) <= hac.DISCORD_LIMIT for p in parts))
        self.assertEqual(sum(p.count("x") for p in parts), 5000)

    def cog(self, agent):
        return hac.HomeAssistant(types.SimpleNamespace(user=BOT), settings(), agent=agent)

    def inter(self):
        inter = interaction(THEA)
        inter.channel = types.SimpleNamespace(id=42, guild=object())
        inter.response.defer = mock.AsyncMock()
        inter.followup = types.SimpleNamespace(send=mock.AsyncMock())
        return inter

    async def test_mode_shows_and_sets(self):
        agent = ModeAgent()
        cog = self.cog(agent)
        inter = self.inter()
        await hac.HomeAssistant.mode.callback(cog, inter, None)
        self.assertIn("General", inter.response.send_message.call_args.args[0])
        await hac.HomeAssistant.mode.callback(cog, inter, "code")
        self.assertIn("Code", inter.response.send_message.call_args.args[0])
        self.assertEqual(agent.current, {"discord:42": "code"})

    async def test_an_unknown_mode_lists_the_names(self):
        cog = self.cog(ModeAgent())
        inter = self.inter()
        await hac.HomeAssistant.mode.callback(cog, inter, "chef")
        self.assertIn(", ".join(hac.MODE_NAMES), inter.response.send_message.call_args.args[0])

    async def test_code_command_uses_code_mode_once(self):
        agent = ModeAgent()
        cog = self.cog(agent)
        cog.started = True
        await hac.HomeAssistant.code.callback(cog, self.inter(), "reverse a list")
        self.assertEqual(agent.modes_used, ["code"])
        self.assertEqual(agent.current, {})

    async def test_plain_answers_leave_the_mode_to_the_agent(self):
        agent = FakeAgent()
        cog = self.cog(agent)
        cog.started = True
        await cog.answer("c", "hi", message().author, no_confirm)
        self.assertEqual(agent.modes_used, [None])


class TimerTargetTests(unittest.TestCase):
    def test_ids_map_back_to_where_they_came_from(self):
        self.assertEqual(hac.timer_target("discord:123"), ("channel", 123))
        self.assertEqual(hac.timer_target("discord:dm:5"), ("dm", 5))

    def test_ids_this_cog_did_not_make_are_none(self):
        for cid in ("", "discord:", "discord:x", "discord:dm:", "terminal:1", "discord:a:1"):
            self.assertIsNone(hac.timer_target(cid), cid)

    def test_round_trip_with_conversation_id(self):
        user = types.SimpleNamespace(id=THEA)
        thread = types.SimpleNamespace(id=77, guild=object())
        dm = types.SimpleNamespace(id=1, guild=None)
        self.assertEqual(hac.timer_target(hac.conversation_id(thread, user)), ("channel", 77))
        self.assertEqual(hac.timer_target(hac.conversation_id(dm, user)), ("dm", THEA))


class TimerBot:
    """Just the bits of discord.py's Bot that timer delivery uses."""

    def __init__(self, channels=(), users=(), fetchable=()):
        self.channels = {c.id: c for c in channels}
        self.users = {u.id: u for u in users}
        self.fetchable = {c.id: c for c in fetchable}
        self.wait_until_ready = mock.AsyncMock()

    def get_channel(self, ident):
        return self.channels.get(ident)

    def get_user(self, ident):
        return self.users.get(ident)

    async def fetch_channel(self, ident):
        if ident in self.fetchable:
            return self.fetchable[ident]
        raise hac.discord.NotFound(mock.Mock(status=404), "Unknown Channel")

    async def fetch_user(self, ident):
        raise hac.discord.NotFound(mock.Mock(status=404), "Unknown User")


def sendable(ident):
    return types.SimpleNamespace(id=ident, send=mock.AsyncMock())


class TimerDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def cog(self, bot):
        return hac.HomeAssistant(bot=bot, settings=settings(), agent=FakeAgent())

    async def test_a_thread_timer_lands_in_the_thread_without_pings(self):
        thread = sendable(77)
        bot = TimerBot(channels=[thread])
        await self.cog(bot).deliver_timer("discord:77", "⏰ Timer done: pasta (Thea)")
        bot.wait_until_ready.assert_awaited()
        thread.send.assert_awaited_once_with("⏰ Timer done: pasta (Thea)",
                                             allowed_mentions=hac.NO_PINGS)

    async def test_an_archived_thread_is_fetched(self):
        thread = sendable(77)
        await self.cog(TimerBot(fetchable=[thread])).deliver_timer("discord:77", "done")
        thread.send.assert_awaited_once()

    async def test_a_dm_timer_goes_to_the_user(self):
        thea = sendable(THEA)
        await self.cog(TimerBot(users=[thea])).deliver_timer(f"discord:dm:{THEA}", "done")
        thea.send.assert_awaited_once()

    async def test_a_deleted_thread_falls_back_to_the_owners_dms(self):
        thea = sendable(THEA)
        with self.assertLogs("homeassistant", "WARNING"):
            await self.cog(TimerBot(users=[thea])).deliver_timer("discord:77", "done")
        thea.send.assert_awaited_once_with("done", allowed_mentions=hac.NO_PINGS)

    async def test_nowhere_at_all_is_logged_not_raised(self):
        with self.assertLogs("homeassistant", "WARNING"):
            await self.cog(TimerBot()).deliver_timer("discord:77", "done")

    async def test_an_unknown_id_is_logged_not_sent(self):
        with self.assertLogs("homeassistant", "WARNING"):
            await self.cog(TimerBot()).deliver_timer("terminal:1", "done")

    async def test_a_failed_send_never_raises(self):
        thread = sendable(77)
        thread.send.side_effect = RuntimeError("discord down")
        with self.assertLogs("homeassistant", "ERROR"):
            await self.cog(TimerBot(channels=[thread])).deliver_timer("discord:77", "done")

    def test_the_built_agent_reports_timers_to_the_cog(self):
        class Built:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
        with mock.patch.object(hac, "HAAgent", Built):
            cog = hac.HomeAssistant(bot=None, settings=settings(ha_token="t", llm_url="http://x/v1"))
            agent = cog._build_agent()
        self.assertEqual(agent.on_timer, cog.deliver_timer)
        self.assertEqual(agent.kwargs["llm_url"], "http://x/v1")

    def test_llm_url_left_out_when_blank(self):
        class Built:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
        with mock.patch.object(hac, "HAAgent", Built):
            agent = hac.HomeAssistant(bot=None, settings=settings(ha_token="t"))._build_agent()
        self.assertNotIn("llm_url", agent.kwargs)



class ReloadTests(unittest.IsolatedAsyncioTestCase):
    """/ha reload reloads the extension (and so ha_agent), not just the action list."""

    def cog(self, agent, **over):
        return hac.HomeAssistant(bot=None, settings=settings(**over), agent=agent)

    def started_cog(self, entries=3):
        agent = FakeAgent()
        agent.catalog = types.SimpleNamespace(entries=list(range(entries)))
        cog = self.cog(agent)
        cog.started = True
        return cog

    def test_report(self):
        self.assertIn("5 Home Assistant actions", hac.reload_report(self.started_cog(5)))
        off = self.cog(None)
        off.problem = "No Home Assistant token: set HA_TOKEN on Golurk."
        self.assertIn("HA_TOKEN", hac.reload_report(off))
        self.assertIn("isn't reachable", hac.reload_report(self.cog(FakeAgent())))
        self.assertIn("didn't come back", hac.reload_report(None))

    def inter(self):
        inter = interaction(THEA)
        inter.channel = types.SimpleNamespace(id=42, guild=object())
        inter.response.defer = mock.AsyncMock()
        inter.followup = types.SimpleNamespace(send=mock.AsyncMock())
        return inter

    async def test_reload_reloads_the_extension_and_reports_the_new_cog(self):
        new = self.started_cog(7)
        bot = types.SimpleNamespace(reload_extension=mock.AsyncMock(),
                                    get_cog=mock.Mock(return_value=new))
        old = hac.HomeAssistant(bot=bot, settings=settings(), agent=FakeAgent())
        inter = self.inter()
        await hac.HomeAssistant.reload.callback(old, inter)
        bot.reload_extension.assert_awaited_once_with("cogs.homeassistant")
        bot.get_cog.assert_called_once_with(old.qualified_name)
        self.assertIn("7 Home Assistant actions", inter.followup.send.call_args.args[0])

    async def test_reload_works_while_the_agent_is_off(self):
        bot = types.SimpleNamespace(reload_extension=mock.AsyncMock(),
                                    get_cog=mock.Mock(return_value=self.started_cog()))
        old = hac.HomeAssistant(bot=bot, settings=settings(), agent=None)
        await hac.HomeAssistant.reload.callback(old, self.inter())
        bot.reload_extension.assert_awaited_once()

    async def test_a_failed_reload_says_the_old_code_still_runs(self):
        bot = types.SimpleNamespace(reload_extension=mock.AsyncMock(side_effect=RuntimeError("x")),
                                    get_cog=mock.Mock())
        old = hac.HomeAssistant(bot=bot, settings=settings(), agent=FakeAgent())
        inter = self.inter()
        with self.assertLogs("homeassistant", "ERROR"):
            await hac.HomeAssistant.reload.callback(old, inter)
        self.assertIn("old code is still running", inter.followup.send.call_args.args[0])
        bot.get_cog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
