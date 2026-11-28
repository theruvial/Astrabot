"""Tests for cogs/golurk_agent.py: routing, replies, errors, confirm buttons.

No real Home Assistant, Ollama or Discord: the agent is a stand-in object.
"""
import asyncio
import sys
import types
import unittest
from unittest import mock

import aiohttp

from cogs import golurk_agent as hac

class GolurkAgentConfigTests(unittest.TestCase):
    def test_legacy_config_is_used_when_the_new_section_is_absent(self):
        legacy = {"allowed_user_ids": [123], "dm_user_ids": [], "channel_id": 42}
        self.assertEqual(hac.load_settings({"homeassistant": legacy}, env={}),
                         hac.load_settings({"golurk_agent": legacy}, env={}))

    def test_new_section_takes_precedence_without_inheriting_legacy_access(self):
        cfg = {"homeassistant": {"allowed_user_ids": [123], "channel_id": 42},
               "golurk_agent": {"allowed_user_ids": []}}
        settings = hac.load_settings(cfg, env={})
        self.assertEqual(settings["allowed"], frozenset())
        self.assertEqual(settings["channel_id"], 0)


THEA = 151408413744693248
BOT = types.SimpleNamespace(id=999, bot=True)


def settings(**over):
    s = hac.load_settings({"golurk_agent": {"allowed_user_ids": [THEA], "channel_id": 42}}, env={})
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
        s = hac.load_settings({"golurk_agent": {"ha_token": "cfg", "allowed_user_ids": ["5", "x"],
                                                 "channel_id": "nope"}}, env={"HA_TOKEN": "env"})
        self.assertEqual(s["ha_token"], "cfg")
        self.assertEqual(s["allowed"], frozenset({5}))
        self.assertEqual(s["channel_id"], 0)

    def test_an_empty_allowlist_means_nobody(self):
        self.assertEqual(hac.load_settings({"golurk_agent": {"allowed_user_ids": []}}, env={})["allowed"],
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
        self.assertEqual(hac.load_settings({"golurk_agent": {"dm_user_ids": ["3", "x"]}},
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
        return hac.GolurkAgent(bot=None, settings=settings(**over), agent=agent)

    async def test_a_failed_start_keeps_the_cog_and_retries_next_message(self):
        agent = FakeAgent(start_errors=1)
        cog = self.cog(agent)
        with self.assertLogs("golurk_agent", "WARNING"):
            await cog.cog_load()
        self.assertFalse(cog.started)
        parts = (await cog.answer("discord:1", "hi", message().author, no_confirm)).parts
        self.assertEqual(parts[0].split("\n")[0], "Done it.")
        self.assertEqual(agent.starts, 2)
        self.assertEqual(agent.calls, [("discord:1", "hi", "Thea")])

    async def test_ha_still_down_says_so(self):
        cog = self.cog(FakeAgent(start_errors=5))
        with self.assertLogs("golurk_agent", "WARNING"):
            parts = (await cog.answer("c", "hi", message().author, no_confirm)).parts
        self.assertIn("isn't reachable", parts[0])

    async def test_errors_become_short_messages(self):
        def boom(*_):
            raise RuntimeError("Ollama error 404: model not found")
        cog = self.cog(FakeAgent(chat=boom))
        with self.assertLogs("golurk_agent", "ERROR"):
            parts = (await cog.answer("c", "hi", message().author, no_confirm)).parts
        self.assertEqual(parts, ["The local model isn't running. Start Strata on Golurk."])

    async def test_the_timeout_is_a_safety_net(self):
        async def slow(*_):
            await asyncio.sleep(5)
        cog = self.cog(FakeAgent(chat=lambda *a: slow()), timeout=0.05)
        with self.assertLogs("golurk_agent", "ERROR"):
            parts = (await cog.answer("c", "hi", message().author, no_confirm)).parts
        self.assertEqual(parts, ["That took too long, try again."])

    async def test_no_token_turns_it_off_with_a_reason(self):
        cog = self.cog(None, ha_token=None)
        with self.assertLogs("golurk_agent", "WARNING"):
            await cog.cog_load()
        self.assertIsNone(cog.agent)
        self.assertIn("HA_TOKEN", (await cog.answer("c", "hi", message().author, no_confirm)).parts[0])

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
            with self.assertLogs("golurk_agent", "ERROR"):
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
        cog = hac.GolurkAgent(bot=None, settings=settings(), agent=agent)
        await cog.answer("c", "hi", message().author, no_confirm, think=True)
        await cog.answer("c", "hi", message().author, no_confirm)
        self.assertEqual(agent.thinks, [True, None])

    async def think_command(self, cog, user_id, message=None):
        inter = interaction(user_id)
        inter.channel = types.SimpleNamespace(id=42, guild=object())
        await hac.GolurkAgent.think.callback(cog, inter, message)
        return inter

    async def test_think_toggles_and_sets(self):
        agent = FakeAgent()
        cog = hac.GolurkAgent(bot=None, settings=settings(), agent=agent)
        for arg, want in ((None, "ON"), (None, "OFF"), ("on", "ON"), ("off", "OFF")):
            inter = await self.think_command(cog, THEA, arg)
            self.assertIn(f"now {want}", inter.response.send_message.call_args.args[0])
        self.assertEqual(agent.toggles, {"discord:42": False})

    async def test_think_with_a_message_thinks_once_without_the_toggle(self):
        agent = FakeAgent()
        cog = hac.GolurkAgent(bot=None, settings=settings(), agent=agent)
        inter = interaction(THEA)
        inter.channel = types.SimpleNamespace(id=42, guild=object())
        inter.response.defer = mock.AsyncMock()
        inter.followup = types.SimpleNamespace(send=mock.AsyncMock())
        await hac.GolurkAgent.think.callback(cog, inter, "will it rain?")
        self.assertEqual(agent.calls, [("discord:42", "will it rain?", "Thea")])
        self.assertEqual(agent.thinks, [True])
        self.assertEqual(agent.toggles, {})

    async def test_strangers_cannot_think(self):
        agent = FakeAgent()
        cog = hac.GolurkAgent(bot=None, settings=settings(), agent=agent)
        inter = await self.think_command(cog, 1)
        self.assertEqual(inter.response.send_message.call_args.args[0], "Not allowed.")
        self.assertEqual(agent.toggles, {})


def interaction(user_id):
    resp = types.SimpleNamespace(send_message=mock.AsyncMock(), defer=mock.AsyncMock())
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

    async def press(self, button, edit=None, timeout=5):
        sent = {}

        async def send(content, view):
            sent["view"] = view
            sent["content"] = content
            sent["prompt"] = types.SimpleNamespace(edit=edit if edit is not None else mock.AsyncMock())
            return sent["prompt"]

        confirm = hac.make_confirm(send, THEA, timeout=timeout)
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
        click.response.defer.assert_awaited_once_with()
        self.assertIn("Allowed", sent["prompt"].edit.call_args.kwargs["content"])

    async def test_deny_returns_false(self):
        result, sent, _ = await self.press("deny")
        self.assertFalse(result)
        self.assertIn("Denied", sent["prompt"].edit.call_args.kwargs["content"])

    async def test_allow_retries_a_transient_edit_without_approving_early(self):
        edit_started = asyncio.Event()
        finish_edit = asyncio.Event()

        async def edit_prompt(**kwargs):
            if edit.await_count == 1:
                raise OSError("temporary connection failure")
            edit_started.set()
            await finish_edit.wait()

        edit = mock.AsyncMock(side_effect=edit_prompt)
        with mock.patch.object(hac, "CONFIRM_EDIT_RETRY_SECONDS", 0):
            pending = asyncio.create_task(self.press("allow", edit))
            try:
                await asyncio.wait_for(edit_started.wait(), 1)
                self.assertFalse(pending.done())
            finally:
                finish_edit.set()
                result, _, click = await pending
        self.assertTrue(result)
        self.assertEqual(edit.await_count, 2)
        click.response.defer.assert_awaited_once_with()

    async def test_allow_returns_false_when_edit_keeps_failing_until_deadline(self):
        async def edit_prompt(content, **kwargs):
            if "Allowed" in content:
                raise OSError("connection down")

        edit = mock.AsyncMock(side_effect=edit_prompt)
        with mock.patch.object(hac, "CONFIRM_EDIT_RETRY_SECONDS", 0.005):
            with self.assertLogs("golurk_agent", "WARNING"):
                result, _, _ = await self.press("allow", edit, timeout=0.05)
        self.assertFalse(result)
        self.assertGreaterEqual(edit.await_count, 2)
        self.assertIn("so no", edit.await_args.kwargs["content"])

    async def test_allow_returns_false_on_permanent_edit_error(self):
        edit = mock.AsyncMock(side_effect=hac.discord.Forbidden(
            types.SimpleNamespace(status=403, reason="Forbidden"), "missing access"))
        with self.assertLogs("golurk_agent", "ERROR"):
            result, _, _ = await self.press("allow", edit)
        self.assertFalse(result)
        edit.assert_awaited_once()

    async def test_timeout_returns_false_and_says_so(self):
        prompt = mock.AsyncMock()

        async def send(content, view):
            return prompt

        self.assertFalse(await hac.make_confirm(send, THEA, timeout=0.05)("x"))
        self.assertIn("No answer", prompt.edit.call_args.kwargs["content"])

    async def test_an_error_inside_confirm_is_false_not_raised(self):
        async def send(content, view):
            raise RuntimeError("discord down")
        with self.assertLogs("golurk_agent", "ERROR"):
            self.assertFalse(await hac.make_confirm(send, THEA)("x"))


def folder_request(cid="discord:50", user_id=THEA, approver_ids=(str(THEA),)):
    return types.SimpleNamespace(path=r"C:\Manuals", access="read", user_id=str(user_id),
                                 conversation_id=cid, approver_ids=approver_ids)


def folder_decision(action, approver_id=None, text=""):
    return types.SimpleNamespace(action=action, approver_id=approver_id, text=text)


class FolderApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def proposal(self, timeout=2):
        shown = asyncio.get_running_loop().create_future()
        prompt = types.SimpleNamespace(edit=mock.AsyncMock())

        async def send(content, view, *, embed):
            shown.set_result((view, embed))
            return prompt

        approve = hac.make_folder_approval(send, "discord:50", str(THEA), folder_decision, timeout)
        pending = asyncio.create_task(approve(folder_request()))
        view, embed = await asyncio.wait_for(shown, 1)
        return pending, view, embed, prompt

    def click(self, user=THEA):
        inter = interaction(user)
        inter.response.send_modal = mock.AsyncMock()
        return inter

    async def test_embed_shows_exact_proposal_and_yes_resolves_after_edit(self):
        pending, view, embed, prompt = await self.proposal()
        self.assertEqual([(field.name, field.value) for field in embed.fields],
                         [("Path", hac.discord.utils.escape_markdown(r"C:\Manuals")),
                          ("Requested by", f"<@{THEA}>"), ("Access", "Read only"),
                          ("Scope", "This conversation")])
        user = self.click()
        self.assertTrue(await view.interaction_check(user))
        await view.yes.callback(user)
        self.assertEqual(await pending, folder_decision("yes", str(THEA)))
        user.response.defer.assert_awaited_once_with()
        self.assertIn("Approved", prompt.edit.await_args.kwargs["content"])
        self.assertTrue(all(button.disabled for button in view.children))

    async def test_ask_opens_modal_and_waits_for_an_authorized_approver_to_submit(self):
        pending, view, _, prompt = await self.proposal()
        click = self.click()
        await view.ask.callback(click)
        modal = click.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, hac.FolderApprovalModal)
        self.assertFalse(pending.done())
        other = self.click(THEA + 1)
        self.assertFalse(await view.interaction_check(other))
        await modal.on_submit(other)
        self.assertFalse(pending.done())
        modal.comment._value = "Can we use read-only instead?"
        submit = self.click()
        await modal.on_submit(submit)
        self.assertEqual(await pending, folder_decision("ask", str(THEA), "Can we use read-only instead?"))
        submit.response.defer.assert_awaited_once_with()
        self.assertNotIn("read-only instead", prompt.edit.await_args.kwargs["content"])

    async def test_no_and_expired_modal_do_not_authorize(self):
        pending, view, _, _ = await self.proposal()
        await view.no.callback(self.click())
        self.assertEqual(await pending, folder_decision("no", str(THEA)))

        pending, view, _, prompt = await self.proposal(timeout=0.03)
        click = self.click()
        await view.ask.callback(click)
        modal = click.response.send_modal.await_args.args[0]
        self.assertEqual(await pending, folder_decision("no"))
        late = self.click()
        modal.comment._value = "Please approve"
        await modal.on_submit(late)
        late.response.send_message.assert_awaited_once_with(
            "This folder request is closed.", ephemeral=True)
        self.assertIn("Expired", prompt.edit.await_args.kwargs["content"])

    async def test_failed_edit_rejects_yes(self):
        pending, view, _, prompt = await self.proposal()
        prompt.edit.side_effect = RuntimeError("Discord edit failed")
        await view.yes.callback(self.click())
        with self.assertLogs("golurk_agent", "ERROR"):
            self.assertEqual(await pending, folder_decision("no"))

    async def test_callback_rejects_wrong_requester_or_conversation(self):
        send = mock.AsyncMock()
        approve = hac.make_folder_approval(send, "discord:50", str(THEA), folder_decision)
        self.assertEqual(await approve(folder_request(cid="discord:51")), folder_decision("no"))
        self.assertEqual(await approve(folder_request(user_id=THEA + 1)), folder_decision("no"))
        self.assertEqual(await approve(folder_request(approver_ids=())), folder_decision("no"))
        send.assert_not_awaited()

    async def test_agent_waits_for_ask_in_the_originating_thread(self):
        class RequestAgent(FakeAgent):
            async def chat(self, cid, text, confirm, speaker, think=None, *, approve_folder=None):
                self.decision = await approve_folder(folder_request(cid=cid, approver_ids=(str(THEA + 1),)))
                return reply("I received your question.")

        shown = asyncio.get_running_loop().create_future()
        prompt = types.SimpleNamespace(edit=mock.AsyncMock())
        thread = Place(50, parent_id=42)

        async def send(content, *, view=None, embed=None, allowed_mentions=None):
            if embed is not None:
                shown.set_result(view)
                return prompt
            return await Place.send(thread, content, view=view, allowed_mentions=allowed_mentions)

        thread.send = send
        msg = chat_message(Place(42), thread=thread)
        agent = RequestAgent()
        cog = hac.GolurkAgent(types.SimpleNamespace(user=BOT), settings(show_steps=False), agent=agent)
        with mock.patch.object(hac, "ha_agent", types.SimpleNamespace(FolderGrantDecision=folder_decision)):
            pending = asyncio.create_task(cog.on_message(msg))
            try:
                view = await asyncio.wait_for(shown, 1)
                self.assertFalse(await view.interaction_check(self.click()))
                ask = self.click(THEA + 1)
                self.assertTrue(await view.interaction_check(ask))
                await view.ask.callback(ask)
                self.assertFalse(pending.done())
                modal = ask.response.send_modal.await_args.args[0]
                modal.comment._value = "What files would that include?"
                await modal.on_submit(self.click(THEA + 1))
                await asyncio.wait_for(pending, 1)
            finally:
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
        self.assertEqual(agent.decision, folder_decision("ask", str(THEA + 1), "What files would that include?"))
        self.assertTrue(thread.sent[0].startswith("I received your question."))
        self.assertEqual(msg.replies, [])

    def test_live_step_does_not_echo_the_modal_answer(self):
        event = {"type": "tool", "name": "request_folder_access", "result": "private revision"}
        self.assertEqual(hac.step_messages(event), [])


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
        cog = hac.GolurkAgent(types.SimpleNamespace(user=BOT), settings(), agent=agent)
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
        self.assertEqual(hac.load_settings({"golurk_agent": {"history_turns": "20"}}, env={})["history_turns"], 20)
        self.assertEqual(hac.load_settings({"golurk_agent": {"history_turns": "x"}}, env={})["history_turns"], 6)

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
        with self.assertLogs("golurk_agent", "WARNING"):
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
        cog = hac.GolurkAgent(types.SimpleNamespace(user=BOT), settings(), agent=agent)
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
        with self.assertLogs("golurk_agent", "WARNING"):
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
        await hac.GolurkAgent.think.callback(cog, inter, None)
        self.assertEqual(inter.response.send_message.call_args.args[0], "I only answer in <#42>.")


class UpgradeAgent(HistoryAgent):
    def __init__(self, response=None):
        super().__init__()
        self.response = response or reply()
        self.metadata = []
        self.conversations = types.SimpleNamespace(seen=mock.Mock(return_value=False))

    async def chat(self, cid, text, confirm, speaker, think=None, on_step=None, *,
                   user_id=None, message_id=None, is_dm=False, attachments=()):
        self.metadata.append((user_id, message_id, is_dm, attachments))
        self.calls.append((cid, text, speaker))
        return self.response


class UpgradeTests(unittest.IsolatedAsyncioTestCase):
    def cog(self, agent, **bot_options):
        cog = hac.GolurkAgent(types.SimpleNamespace(user=BOT, **bot_options),
                              settings(show_steps=False), agent=agent)
        cog.started = True
        return cog

    async def test_attachment_only_is_routed_with_trusted_dm_metadata(self):
        agent = UpgradeAgent()
        dm = Place(42)
        dm.guild = None
        msg = chat_message(dm, content="<@999>")
        msg.attachments = [object()]
        accepted = [object()]
        reader = mock.AsyncMock(return_value=accepted)
        with mock.patch.object(hac, "ha_agent", types.SimpleNamespace(read_discord_attachments=reader)):
            await self.cog(agent).on_message(msg)
        reader.assert_awaited_once_with(msg.attachments)
        self.assertEqual(agent.calls, [(f"discord:dm:{THEA}", "", "Thea")])
        self.assertEqual(agent.metadata, [(str(THEA), str(msg.id), True, accepted)])
        self.assertEqual(len(msg.replies), 1)

    async def test_unauthorized_upload_is_never_downloaded(self):
        agent = UpgradeAgent()
        msg = chat_message(Place(7))
        msg.attachments = [object()]
        reader = mock.AsyncMock()
        with mock.patch.object(hac, "ha_agent", types.SimpleNamespace(read_discord_attachments=reader)):
            await self.cog(agent).on_message(msg)
        reader.assert_not_awaited()
        self.assertEqual(agent.calls, [])
        self.assertEqual(msg.replies, [])

    async def test_file_replies_and_notices_stay_in_the_thread(self):
        result = reply()
        result.attachment_errors = ["bad.png: media unsupported"]
        result.files = [types.SimpleNamespace(filename="failed.txt", data=b"no"),
                        types.SimpleNamespace(filename="updated.txt", data=b"edited")]
        agent = UpgradeAgent(result)
        thread = Place(50, parent_id=42)
        uploads = []

        async def send(content=None, *, allowed_mentions, file=None):
            self.assertIs(allowed_mentions, hac.NO_PINGS)
            if file is not None:
                if file.filename == "failed.txt":
                    raise OSError("upload unavailable")
                uploads.append((file.filename, file.fp.read()))
            else:
                thread.sent.append(content)

        thread.send = send
        msg = chat_message(Place(42), thread=thread)
        with self.assertLogs("golurk_agent", "WARNING"):
            await self.cog(agent).on_message(msg)
        self.assertEqual(agent.metadata, [(str(THEA), str(msg.id), False, ())])
        self.assertTrue(thread.sent[0].startswith("Done it."))
        self.assertIn("bad.png: media unsupported", thread.sent[1])
        self.assertIn("Could not upload failed.txt", thread.sent[2])
        self.assertEqual(uploads, [("updated.txt", b"edited")])
        self.assertEqual(msg.replies, [])

    async def test_replayed_starter_recovers_existing_thread_and_suppresses_reply(self):
        result = reply("")
        result.duplicate = True
        agent = UpgradeAgent(result)
        channel = Place(42)
        already_exists = hac.discord.HTTPException(
            types.SimpleNamespace(status=400, reason="Bad Request"), "Thread already exists")
        msg = chat_message(channel, thread_error=already_exists)
        thread = mock.Mock(spec=hac.discord.Thread, id=msg.id, parent_id=channel.id)
        thread.send = mock.AsyncMock()
        thread.typing = Place(msg.id).typing
        fetch = mock.AsyncMock(return_value=thread)
        await self.cog(agent, fetch_channel=fetch).on_message(msg)
        fetch.assert_awaited_once_with(msg.id)
        self.assertEqual(agent.calls[0][0], f"discord:{msg.id}")
        thread.send.assert_not_awaited()
        self.assertEqual(msg.replies, [])

    async def test_durable_ids_prevent_reseeding_discarded_history(self):
        agent = UpgradeAgent()
        agent.conversations.seen.side_effect = lambda cid, ident: cid == "discord:50" and ident == "50"
        thread = FakeThread([said(BOT.id, "Old private file excerpt.", id=2)],
                            starter=said(THEA, "Old prompt", id=50))
        await self.cog(agent).on_message(chat_message(thread, content="new prompt"))
        self.assertEqual(agent.remembered, [])
        self.assertEqual(agent.calls[0][:2], ("discord:50", "new prompt"))

    async def test_legacy_agent_answers_text_with_an_upload_upgrade_notice(self):
        agent = FakeAgent()
        dm = Place(7)
        dm.guild = None
        msg = chat_message(dm, content="hello")
        msg.attachments = [object()]
        await self.cog(agent).on_message(msg)
        self.assertEqual(agent.calls, [(f"discord:dm:{THEA}", "hello", "Thea")])
        self.assertTrue(msg.replies[0].startswith("Done it."))
        self.assertIn("Uploads need the updated Golurk-Agent package", dm.sent[0])

    async def test_slash_chat_supplies_discord_metadata(self):
        agent = UpgradeAgent()
        inter = interaction(THEA)
        inter.id = 543
        inter.channel = Place(42)
        inter.followup = types.SimpleNamespace(send=mock.AsyncMock())
        await hac.GolurkAgent.ask.callback(self.cog(agent), inter, "hello")
        self.assertEqual(agent.metadata, [(str(THEA), "543", False, ())])
        inter.followup.send.assert_awaited_once()

    def test_legacy_history_seed_excludes_upload_exchange_and_notices(self):
        upload = said(THEA, "Edit this", id=1)
        upload.attachments = [object()]
        messages = [upload, said(BOT.id, "private excerpt", id=2),
                    said(THEA, "hello", id=3),
                    said(BOT.id, "Attachment notice: failed upload", id=4),
                    said(BOT.id, "hi", id=5)]
        self.assertEqual(hac.thread_turns(messages, BOT.id, {THEA}),
                         [("user", "Thea: hello"), ("assistant", "hi")])


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

    def test_footer_shows_routes_that_skip_the_model(self):
        for routed_by in ("fast", "learned"):
            with self.subTest(routed_by=routed_by):
                r = reply(actions=[act("fan.turn_off", ["Fan"], {})])
                r.mode, r.routed_by = "general", routed_by
                self.assertEqual(hac.action_footer(r).splitlines()[-1],
                                 f"-# 💬 general · ⚡ {routed_by} · 2.3 s")

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
        return hac.GolurkAgent(types.SimpleNamespace(user=BOT), settings(), agent=agent)

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
        await hac.GolurkAgent.mode.callback(cog, inter, None)
        self.assertIn("General", inter.response.send_message.call_args.args[0])
        await hac.GolurkAgent.mode.callback(cog, inter, "code")
        self.assertIn("Code", inter.response.send_message.call_args.args[0])
        self.assertEqual(agent.current, {"discord:42": "code"})

    async def test_an_unknown_mode_lists_the_names(self):
        cog = self.cog(ModeAgent())
        inter = self.inter()
        await hac.GolurkAgent.mode.callback(cog, inter, "chef")
        self.assertIn(", ".join(hac.MODE_NAMES), inter.response.send_message.call_args.args[0])

    async def test_code_command_uses_code_mode_once(self):
        agent = ModeAgent()
        cog = self.cog(agent)
        cog.started = True
        await hac.GolurkAgent.code.callback(cog, self.inter(), "reverse a list")
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
        return hac.GolurkAgent(bot=bot, settings=settings(), agent=FakeAgent())

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
        with self.assertLogs("golurk_agent", "WARNING"):
            await self.cog(TimerBot(users=[thea])).deliver_timer("discord:77", "done")
        thea.send.assert_awaited_once_with("done", allowed_mentions=hac.NO_PINGS)

    async def test_nowhere_at_all_is_logged_not_raised(self):
        with self.assertLogs("golurk_agent", "WARNING"):
            await self.cog(TimerBot()).deliver_timer("discord:77", "done")

    async def test_an_unknown_id_is_logged_not_sent(self):
        with self.assertLogs("golurk_agent", "WARNING"):
            await self.cog(TimerBot()).deliver_timer("terminal:1", "done")

    async def test_a_failed_send_never_raises(self):
        thread = sendable(77)
        thread.send.side_effect = RuntimeError("discord down")
        with self.assertLogs("golurk_agent", "ERROR"):
            await self.cog(TimerBot(channels=[thread])).deliver_timer("discord:77", "done")

    def test_the_built_agent_reports_timers_to_the_cog(self):
        class Built:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
        with mock.patch.object(hac, "HAAgent", Built):
            cog = hac.GolurkAgent(bot=None, settings=settings(ha_token="t", llm_url="http://x/v1"))
            agent = cog._build_agent()
        self.assertEqual(agent.on_timer, cog.deliver_timer)
        self.assertEqual(agent.kwargs["llm_url"], "http://x/v1")

    def test_llm_url_left_out_when_blank(self):
        class Built:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
        with mock.patch.object(hac, "HAAgent", Built):
            agent = hac.GolurkAgent(bot=None, settings=settings(ha_token="t"))._build_agent()
        self.assertNotIn("llm_url", agent.kwargs)



class ReloadTests(unittest.IsolatedAsyncioTestCase):
    """/ha reload reloads the extension (and so ha_agent), not just the action list."""

    def cog(self, agent, **over):
        return hac.GolurkAgent(bot=None, settings=settings(**over), agent=agent)

    def started_cog(self, entries=3):
        agent = FakeAgent()
        agent.catalog = types.SimpleNamespace(entries=list(range(entries)))
        cog = self.cog(agent)
        cog.started = True
        return cog

    def test_report(self):
        self.assertIn("5 Home Assistant actions", hac.reload_report(self.started_cog(5)))
        self.assertIn("the agent's default", hac.reload_report(self.started_cog()))
        named = self.started_cog()
        named.settings = settings(llm_url="http://192.0.2.9:8080/v1")
        self.assertIn("Model server: http://192.0.2.9:8080/v1", hac.reload_report(named))
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

    def test_read_config_reads_the_file_now_not_the_cached_copy(self):
        import json, os, tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"golurk_agent": {"llm_url": "http://old/v1"}}, f)
            self.assertEqual(hac.read_config(path)["golurk_agent"]["llm_url"], "http://old/v1")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"golurk_agent": {"llm_url": "http://new/v1"}}, f)
            self.assertEqual(hac.read_config(path)["golurk_agent"]["llm_url"], "http://new/v1")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{ half written")
            with self.assertRaises(ValueError):
                hac.read_config(path)

    def test_a_new_cog_takes_its_settings_from_read_config(self):
        fresh = {"golurk_agent": {"llm_url": "http://new/v1"}}
        with mock.patch.object(hac, "read_config", return_value=fresh) as read:
            cog = hac.GolurkAgent(bot=None, agent=FakeAgent())
        read.assert_called_once_with()
        self.assertEqual(cog.settings["llm_url"], "http://new/v1")

    async def test_reload_reloads_the_extension_and_reports_the_new_cog(self):
        new = self.started_cog(7)
        bot = types.SimpleNamespace(reload_extension=mock.AsyncMock(),
                                    get_cog=mock.Mock(return_value=new))
        old = hac.GolurkAgent(bot=bot, settings=settings(), agent=FakeAgent())
        inter = self.inter()
        await hac.GolurkAgent.reload.callback(old, inter)
        bot.reload_extension.assert_awaited_once_with("cogs.golurk_agent")
        bot.get_cog.assert_called_once_with(old.qualified_name)
        self.assertIn("7 Home Assistant actions", inter.followup.send.call_args.args[0])

    async def test_reload_works_while_the_agent_is_off(self):
        bot = types.SimpleNamespace(reload_extension=mock.AsyncMock(),
                                    get_cog=mock.Mock(return_value=self.started_cog()))
        old = hac.GolurkAgent(bot=bot, settings=settings(), agent=None)
        await hac.GolurkAgent.reload.callback(old, self.inter())
        bot.reload_extension.assert_awaited_once()

    async def test_a_failed_reload_says_the_old_code_still_runs(self):
        bot = types.SimpleNamespace(reload_extension=mock.AsyncMock(side_effect=RuntimeError("x")),
                                    get_cog=mock.Mock())
        old = hac.GolurkAgent(bot=bot, settings=settings(), agent=FakeAgent())
        inter = self.inter()
        with self.assertLogs("golurk_agent", "ERROR"):
            await hac.GolurkAgent.reload.callback(old, inter)
        self.assertIn("old code is still running", inter.followup.send.call_args.args[0])
        bot.get_cog.assert_not_called()


# ------------------------------------------------------------------- steps --

def thought_call(thinking="I should check the fan.", calls=None):
    return {"type": "model", "thinking": thinking, "seconds": 4.21, "input_tokens": 2314,
            "output_tokens": 156,
            "calls": [{"id": "c1", "name": "status", "args": {"target": "Bed Fan"}}]
            if calls is None else calls}


def result(call_id="c1", name="status", ok=True, text="Bed Fan: on 40%"):
    return {"type": "tool", "id": call_id, "name": name, "ok": ok, "result": text,
            "seconds": 0.12}


class Outbox:
    def __init__(self, failures=0):
        self.messages, self.failures = [], failures

    async def __call__(self, content):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("Discord said no")
        self.messages.append(content)


class StepMessageTests(unittest.TestCase):
    def test_reasoning_then_the_calls_as_in_langchat(self):
        self.assertEqual(hac.step_messages(thought_call("First.\n\nSecond.")), [
            "\U0001f4ad **Thinking**\nFirst.\n\nSecond.",
            "\U0001f527 Calling: **status**"])
        two = thought_call("", calls=[{"id": "a", "name": "calc", "args": {}},
                                      {"id": "b", "name": "web_search", "args": {}}])
        self.assertEqual(hac.step_messages(two),
                         ["\U0001f527 Calling: **calc, web\\_search**"])

    def test_result_cards(self):
        self.assertEqual(hac.step_messages(result(), {"target": "Bed Fan"}),
                         ['\U0001f527 **`status`** — `target="Bed Fan"`\n'
                          '```\nBed Fan: on 40%\n```'])
        failed = hac.step_messages(result(ok=False, text="error: no such device"))[0]
        self.assertEqual(failed, "❌ **`status`** failed\n```\nerror: no such device\n```")
        self.assertIn("(no output)", hac.step_messages(result(text=""))[0])

    def test_arguments_are_one_clipped_line(self):
        summary = hac.args_summary({"text": "a`b\nc", "n": 3, "skip": "", "opts": {"p": 5}})
        self.assertEqual(summary, 'text="a\'b ⏎ c", n=3, opts={"p": 5}')
        self.assertLessEqual(len(hac.args_summary({f"k{i}": "x" * 200 for i in range(9)})),
                             hac.STEP_ARGS_LIMIT + 1)

    def test_a_result_is_cut_at_500_and_cannot_close_its_block(self):
        card = hac.step_messages(result(text="```\n" + "y" * 5000))[0]
        self.assertEqual(card.count("```"), 2)
        self.assertTrue(card.endswith("...\n```"))
        self.assertLess(len(card), hac.STEP_SNIPPET + 80)

    def test_long_reasoning_is_split_with_a_header_on_every_part(self):
        text = "```python\n" + "print('a line of code here')\n" * 150 + "```\n" + "word " * 500
        parts = hac.step_messages(thought_call(text, calls=[]))
        self.assertGreater(len(parts), 2)
        for part in parts:
            self.assertTrue(part.startswith(hac.THINKING_HEADER), part[:40])
            self.assertLessEqual(len(part), hac.STEP_PAGE_LIMIT)
            self.assertEqual(part.count("```") % 2, 0)
        huge = "".join(hac.thinking_messages("z" * 100_000))
        self.assertLess(len(huge), hac.STEP_THINKING_LIMIT + 600)

    def test_notes_and_events_with_nothing_to_show(self):
        self.assertEqual(hac.step_messages({"type": "note", "text": "Switched to code mode."}),
                         ["\U0001f501 Switched to code mode."])
        self.assertEqual(hac.step_messages(thought_call("", calls=[])), [])
        self.assertEqual(hac.step_messages({"type": "something new"}), [])

    def test_only_a_real_on_step_parameter_counts(self):
        class New:
            async def chat(self, cid, text, on_step=None):
                pass

        self.assertIn("on_step", hac.chat_parameters(New()))
        self.assertNotIn("on_step", hac.chat_parameters(FakeAgent()))   # **kw is not enough
        self.assertEqual(hac.chat_parameters(None), {})

    def test_show_steps_setting(self):
        self.assertTrue(hac.load_settings({}, env={})["show_steps"])
        off = {"golurk_agent": {"show_steps": False}}
        self.assertFalse(hac.load_settings(off, env={})["show_steps"])

    def test_no_step_message_is_read_back_as_an_answer(self):
        events = [thought_call("thought " * 600), result(), result(ok=False),
                  {"type": "note", "text": "Switched to code mode."}]
        posted = [m for e in events for m in hac.step_messages(e, {"a": 1})]
        posted += ["⏳ Still working… (20s)",
                   hac._card_head("❌", "use", " not run", None)]
        msgs = ([said(THEA, "how is the fan?")] + [said(BOT.id, m) for m in posted]
                + [said(BOT.id, "It's on.")])
        self.assertEqual(hac.thread_turns(msgs, BOT.id, frozenset({THEA})),
                         [("user", "Thea: how is the fan?"), ("assistant", "It's on.")])


class StepFeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_step_is_its_own_message_in_order(self):
        box = Outbox()
        feed = hac.StepFeed(box)
        feed.add(thought_call())
        feed.add(result())
        await feed.close()
        self.assertEqual([m[:2] for m in box.messages],
                         ["\U0001f4ad ", "\U0001f527 ", "\U0001f527 "])
        self.assertIn('target="Bed Fan"', box.messages[2])  # args come from the call
        self.assertTrue(feed.showed_thinking())

    async def test_adding_never_waits_for_discord(self):
        release, sent = asyncio.Event(), []

        async def slow(content):
            await release.wait()
            sent.append(content)

        feed = hac.StepFeed(slow)
        for _ in range(5):
            feed.add(result())          # returns at once although nothing can be sent
        self.assertEqual(sent, [])
        release.set()
        await feed.close()
        self.assertEqual(len(sent), 5)

    async def test_a_call_with_no_result_is_marked_not_run(self):
        box = Outbox()
        feed = hac.StepFeed(box)
        feed.add(thought_call("", calls=[{"id": "a", "name": "calc", "args": {}},
                                         {"id": "b", "name": "use", "args": {"action": "x"}}]))
        feed.add(result("a", "calc"))
        await feed.close()
        self.assertEqual(box.messages[-1], '❌ **`use`** not run — `action="x"`')

    async def test_a_quiet_stretch_says_still_working_then_waits_for_the_gap(self):
        box = Outbox()
        feed = hac.StepFeed(box, heartbeat=0.02, gap=60)
        feed.start()
        await asyncio.sleep(0.15)
        await feed.close()
        self.assertEqual(len(box.messages), 1, box.messages)
        self.assertTrue(box.messages[0].startswith("⏳ Still working"))

    async def test_the_first_still_working_comes_after_one_quiet_interval(self):
        box = Outbox()
        feed = hac.StepFeed(box, heartbeat=0.2, gap=60)
        feed.start()
        await asyncio.sleep(0.3)             # one interval has passed, not two
        await feed.close()
        self.assertEqual(box.messages, ["\u23f3 Still working\u2026 (0.2s)"])

    async def test_no_still_working_while_busy_paused_or_closed(self):
        box = Outbox()
        feed = hac.StepFeed(box, heartbeat=0.05, gap=0)
        feed.start()
        for _ in range(6):
            await asyncio.sleep(0.015)
            feed.add({"type": "something new"})   # activity, nothing to post
        await feed.pause()                        # waiting for Allow / Deny
        await asyncio.sleep(0.15)
        await feed.close()
        await asyncio.sleep(0.1)
        self.assertEqual(box.messages, [])

    async def test_a_failed_post_is_dropped_and_keeps_the_spoiler(self):
        box = Outbox(failures=1)
        feed = hac.StepFeed(box)
        with self.assertLogs("golurk_agent", "WARNING"):
            feed.add(thought_call())
            await feed.close()
        self.assertEqual(box.messages, ["\U0001f527 Calling: **status**",
                                        "❌ **`status`** not run — `target=\"Bed Fan\"`"])
        self.assertFalse(feed.showed_thinking())

    async def test_a_bad_event_is_logged_not_raised(self):
        feed = hac.StepFeed(Outbox())
        with self.assertLogs("golurk_agent", "ERROR"):
            feed.add(None)
        await feed.close()


class StepAgent(FakeAgent):
    """An agent that reports steps, as AGENT_STEPS_DESIGN.md describes."""

    def __init__(self, events=(), error=None, ask=False):
        super().__init__()
        self.events, self.error, self.ask = list(events), error, ask
        self.on_step_seen = []

    async def chat(self, cid, text, confirm, speaker, think=None, on_step=None, **kw):
        self.on_step_seen.append(on_step)
        for event in self.events:
            if on_step:
                on_step(event)
        if self.ask:
            await confirm("unlock the door")
            if on_step:
                on_step({"type": "note", "text": "after the prompt"})
        if self.error:
            raise self.error
        out = reply()
        out.thought, out.thinking = True, "I should check the fan."
        return out


class StepPlace(Place):
    """A channel or thread that also records the send flags."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.flags = []

    async def send(self, content, view=None, allowed_mentions=None, **flags):
        self.flags.append(flags)
        self.sent.append(content)
        return content


class StepAnswerTests(unittest.IsolatedAsyncioTestCase):
    def cog(self, agent, **over):
        cog = hac.GolurkAgent(types.SimpleNamespace(user=BOT), settings(**over), agent=agent)
        cog.started = True
        return cog

    async def test_steps_come_before_the_answer_in_the_thread_without_pinging(self):
        agent = StepAgent([thought_call(), result()])
        thread = StepPlace(50, parent_id=42)
        await self.cog(agent).on_message(chat_message(Place(42), thread=thread))
        self.assertEqual(len(thread.sent), 4, thread.sent)
        self.assertTrue(thread.sent[0].startswith(hac.THINKING_HEADER))
        self.assertTrue(thread.sent[3].startswith("Done it."))
        self.assertEqual(thread.flags[:3], [{"silent": True, "suppress_embeds": True}] * 3)
        self.assertNotIn("Reasoning (tap to show)", "".join(thread.sent))

    async def test_turned_off_keeps_the_spoiler_and_asks_for_no_steps(self):
        agent = StepAgent([thought_call()])
        cog = self.cog(agent, show_steps=False)
        self.assertIsNone(cog._step_feed(Outbox()))
        parts = (await cog.answer("c", "hi", message().author, no_confirm)).parts
        self.assertIn("Reasoning (tap to show)", parts[-1])
        self.assertEqual(agent.on_step_seen, [None])

    async def test_an_older_agent_gets_no_on_step_and_keeps_the_spoiler(self):
        def old(*_):
            out = reply()
            out.thought, out.thinking = True, "hmm"
            return out

        agent, box = FakeAgent(chat=old), Outbox()
        cog = self.cog(agent)
        parts = (await cog.answer("c", "hi", message().author, no_confirm,
                                 steps=cog._step_feed(box))).parts
        self.assertIn("Reasoning (tap to show)", parts[-1])
        self.assertEqual(box.messages, [])

    async def test_steps_are_posted_even_when_the_answer_fails(self):
        box = Outbox()
        cog = self.cog(StepAgent([thought_call()], error=asyncio.TimeoutError()))
        with self.assertLogs("golurk_agent", "ERROR"):
            parts = (await cog.answer("c", "hi", message().author, no_confirm,
                                     steps=cog._step_feed(box))).parts
        self.assertEqual(parts, ["That took too long, try again."])
        self.assertTrue(box.messages[-1].startswith("❌ **`status`** not run"))

    async def test_a_confirm_prompt_sits_between_the_steps(self):
        agent = StepAgent([thought_call(), result()], ask=True)
        thread = StepPlace(50, parent_id=42)

        def quick_confirm(send, owner_id):
            async def confirm(summary):
                await send(f"Allow {summary}?", None)
                return False
            return confirm

        with mock.patch.object(hac, "make_confirm", quick_confirm):
            await self.cog(agent).on_message(chat_message(Place(42), thread=thread))
        self.assertEqual(len(thread.sent), 6, thread.sent)
        self.assertTrue(thread.sent[3].startswith("Allow "))
        self.assertIn("after the prompt", thread.sent[4])
        self.assertTrue(thread.sent[5].startswith("Done it."))


if __name__ == "__main__":
    unittest.main()
