"""Tests for cogs/homeassistant.py: routing, replies, errors, confirm buttons.

No real Home Assistant, Ollama or Discord: the agent is a stand-in object.
"""
import asyncio
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
    def test_dm_from_an_allowed_user(self):
        self.assertTrue(hac.should_handle(message(guild=False), BOT, settings()))

    def test_strangers_and_bots_are_ignored_everywhere(self):
        for msg in (message(author_id=1, guild=False), message(bot=True, guild=False),
                    message(author_id=BOT.id, guild=False), message(author_id=1, channel_id=42)):
            self.assertFalse(hac.should_handle(msg, BOT, settings()))

    def test_configured_channel_or_a_mention(self):
        self.assertTrue(hac.should_handle(message(channel_id=42), BOT, settings()))
        self.assertTrue(hac.should_handle(message(mentions=[BOT]), BOT, settings()))
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
            RuntimeError("Ollama error 500: {}"): "The local model isn't running. Start Ollama on Golurk.",
            aiohttp.ClientConnectorError(mock.Mock(), OSError("refused")):
                "The local model isn't running. Start Ollama on Golurk.",
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
        self.closed = False

    async def start(self):
        self.starts += 1
        if self.starts <= self.start_errors:
            raise hac.HAError("could not reach Home Assistant")

    async def chat(self, cid, text, confirm, speaker):
        self.calls.append((cid, text, speaker))
        result = self.chat_impl(cid, text, confirm, speaker)
        return await result if asyncio.iscoroutine(result) else result

    def forget(self, cid):
        self.forgotten.append(cid)

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
        self.assertEqual(parts, ["The local model isn't running. Start Ollama on Golurk."])

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
        await self.cog(agent).cog_unload()
        self.assertTrue(agent.closed)


def interaction(user_id):
    resp = types.SimpleNamespace(send_message=mock.AsyncMock(), edit_message=mock.AsyncMock())
    return types.SimpleNamespace(user=types.SimpleNamespace(id=user_id), response=resp,
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


if __name__ == "__main__":
    unittest.main()
