"""The Golurk Console remote feed (console_remote.py): settings, auth, the hub,
and the HTTP/WebSocket protocol through aiohttp's test server. No Tk."""

import asyncio
import queue
import socket
import sys
import threading
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

import console_remote as cr
from console_services import BUILTIN_TITLES, merge_config

TOKEN = "t" * 40   # a made-up test token
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Sup:
    def __init__(self, status, name="", title=""):
        self.text, self.name, self.title = status, name, title

    def status(self):
        return self.text


def statuses(bot="Running (PID 1)"):
    builtins = {"bot": Sup(bot), "absLogin": Sup("Stopped"), "wiki": Sup("Waiting to restart"),
                "classifier": Sup("Running (PID 4)"), "librarian": Sup("odd")}
    return cr.pane_entries(builtins, BUILTIN_TITLES, [Sup("Stopped", "dragonwilds", "Dragonwilds")])


class SettingsTests(unittest.TestCase):
    def test_blank_bind_is_off_and_silent(self):
        self.assertEqual(cr.remote_settings({"bind": ""}, env={}), (None, ""))
        self.assertEqual(cr.remote_settings(None, env={}), (None, ""))

    def test_bind_must_be_one_real_address(self):
        for bad in ("0.0.0.0", "::", "golurk", "100.64.0.1, 10.0.0.2"):
            settings, why = cr.remote_settings({"bind": bad, "token": TOKEN}, env={})
            self.assertIsNone(settings, bad)
            self.assertIn("Remote feed off", why)

    def test_token_from_env_and_never_in_the_message(self):
        settings, _ = cr.remote_settings({"bind": "100.64.0.1"}, env={cr.TOKEN_ENV: TOKEN})
        self.assertEqual(settings, {"bind": "100.64.0.1", "port": 13386, "token": TOKEN})
        short = "s" * 31
        settings, why = cr.remote_settings({"bind": "100.64.0.1", "token": short}, env={})
        self.assertIsNone(settings)
        self.assertIn("shorter than 32", why)
        self.assertNotIn(short, why)
        self.assertIn(cr.TOKEN_ENV, cr.remote_settings({"bind": "100.64.0.1"}, env={})[1])

    def test_bad_port(self):
        self.assertIn("port", cr.remote_settings({"bind": "10.0.0.2", "port": 70000,
                                                  "token": TOKEN}, env={})[1])

    def test_console_json_block_is_cleaned(self):
        cfg = merge_config({"remote": {"bind": " 100.64.0.1 ", "port": "x", "extra": 1}})
        self.assertEqual(cfg["remote"], {"bind": "100.64.0.1", "port": 13386, "token": ""})
        self.assertEqual(merge_config({})["remote"]["bind"], "")


class PureTests(unittest.TestCase):
    def test_bearer_only_exact(self):
        self.assertTrue(cr.authorised(f"Bearer {TOKEN}", TOKEN))
        for bad in (None, "", TOKEN, f"Basic {TOKEN}", f"Bearer {TOKEN}x", "Bearer "):
            self.assertFalse(cr.authorised(bad, TOKEN), bad)
        self.assertFalse(cr.authorised("Bearer ", ""))

    def test_states_and_follows(self):
        panes = statuses()
        self.assertEqual([p["key"] for p in panes],
                         ["bot", "site", "portfolio", "absLogin", "wiki", "classifier",
                          "librarian", "dragonwilds"])
        by = {p["key"]: p for p in panes}
        self.assertEqual(by["site"], {"key": "site", "title": "Site (13380)", "state": "running",
                                      "status": "Running (PID 1)", "follows": "bot"})
        self.assertEqual(by["wiki"]["state"], "waiting")
        self.assertEqual(by["absLogin"]["state"], "stopped")
        self.assertEqual(by["librarian"]["state"], "unknown")
        self.assertNotIn("follows", by["bot"])
        self.assertEqual(by["dragonwilds"]["title"], "Dragonwilds")

    def test_visible_streams(self):
        self.assertTrue(cr.visible("bot"))
        for hidden in ("strata", "__decisions__", "", None):
            self.assertFalse(cr.visible(hidden))

    def test_ansi_is_stripped(self):
        self.assertEqual(cr.strip_ansi("\x1b[1;32mok\x1b[0m \x1b]0;title\x07done"), "ok done")


class HubTests(unittest.TestCase):
    def test_seq_is_shared_and_buffers_are_rings(self):
        hub = cr.Hub(replay_lines=3, clock=lambda: 5.0)
        for i in range(5):
            hub.publish("bot", f"b{i}")
        hub.publish("wiki", "w")
        hub.publish("strata", "note")          # never sent
        hub.publish("__decisions__", ["x"])    # never sent
        replay, last = hub.replay()
        self.assertEqual(last, 6)
        self.assertEqual(replay[0], ("bot", [{"seq": 3, "ts": 5.0, "text": "b2"},
                                             {"seq": 4, "ts": 5.0, "text": "b3"},
                                             {"seq": 5, "ts": 5.0, "text": "b4"}]))
        self.assertEqual(replay[1][0], "wiki")

    def test_same_boot_replays_after_since_other_boot_everything(self):
        hub = cr.Hub()
        for i in range(4):
            hub.publish("bot", str(i))
        self.assertEqual([l["seq"] for l in hub.replay(hub.boot, 2)[0][0][1]], [3, 4])
        self.assertEqual(len(hub.replay("old-boot", 2)[0][0][1]), 4)
        self.assertEqual(hub.replay(hub.boot, 4)[0], [])

    def test_pane_order_first_then_others(self):
        hub = cr.Hub()
        for key in ("zeta", "wiki", "bot"):
            hub.publish(key, "x")
        self.assertEqual([k for k, _ in hub.replay(order=["bot", "wiki"])[0]],
                         ["bot", "wiki", "zeta"])

    def test_tap_queue_sees_every_put_and_survives_a_broken_tap(self):
        seen = []
        q = cr.TapQueue(seen.append)
        q.put(("bot", "hi"))
        self.assertEqual(seen, [("bot", "hi")])
        self.assertEqual(q.get_nowait(), ("bot", "hi"))

        def broken(_item):
            raise RuntimeError("boom")

        q = cr.TapQueue(broken)
        q.put(("bot", "still queued"))
        self.assertEqual(q.get_nowait(), ("bot", "still queued"))
        self.assertIsInstance(q, queue.Queue)

    def test_publish_from_other_threads(self):
        hub = cr.Hub()
        threads = [threading.Thread(target=lambda: [hub.publish("bot", "x") for _ in range(200)])
                   for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(hub.seq, 800)


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = cr.Hub()
        self.panes = statuses()
        app = cr.build_app(self.hub, TOKEN, lambda: self.panes, heartbeat=0.6, check=0.1)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def receive(self, ws, timeout=3):
        msg = await ws.receive(timeout=timeout)
        self.assertEqual(msg.type, WSMsgType.TEXT, msg)
        return msg.json()

    async def until(self, ws, kind, timeout=3):
        while True:
            message = await self.receive(ws, timeout)
            if message["type"] == kind:
                return message

    async def test_no_token_or_a_query_token_is_a_bare_401(self):
        for path, headers in (("/status", {}), ("/ws", {}),
                              (f"/status?token={TOKEN}", {}),
                              ("/status", {"Authorization": "Bearer wrong"})):
            resp = await self.client.get(path, headers=headers)
            self.assertEqual(resp.status, 401, path)
            self.assertEqual(await resp.text(), "")

    async def test_status_endpoint(self):
        resp = await self.client.get("/status", headers=AUTH)
        body = await resp.json()
        self.assertEqual(body["type"], "status")
        self.assertEqual(body["boot"], self.hub.boot)
        self.assertEqual(body["panes"], self.panes)

    async def test_connect_order_replay_then_live_without_repeats(self):
        self.hub.publish("bot", "one")
        self.hub.publish("wiki", "two")
        ws = await self.client.ws_connect("/ws", headers=AUTH)
        hello = await self.receive(ws)
        self.assertEqual((hello["type"], hello["protocol"], hello["boot"]),
                         ("hello", 1, self.hub.boot))
        self.assertEqual(hello["replay_lines"], 500)
        self.assertEqual((await self.receive(ws))["type"], "status")
        bot = await self.receive(ws)
        self.assertEqual((bot["type"], bot["pane"], bot["lines"][0]["text"]),
                         ("replay", "bot", "one"))
        self.assertEqual((await self.receive(ws))["pane"], "wiki")
        self.assertEqual(await self.receive(ws), {"type": "replay_done", "last_seq": 2})
        self.hub.publish("classifier", "three")
        line = await self.until(ws, "line")
        self.assertEqual((line["pane"], line["seq"], line["text"]), ("classifier", 3, "three"))
        await ws.close()

    async def test_reconnect_with_boot_and_since_gets_only_newer_lines(self):
        for i in range(3):
            self.hub.publish("bot", str(i))
        ws = await self.client.ws_connect(f"/ws?boot={self.hub.boot}&since=2", headers=AUTH)
        replay = await self.until(ws, "replay")
        self.assertEqual([l["seq"] for l in replay["lines"]], [3])
        await ws.close()
        ws = await self.client.ws_connect("/ws?boot=other&since=2", headers=AUTH)
        replay = await self.until(ws, "replay")
        self.assertEqual(len(replay["lines"]), 3)
        await ws.close()

    async def test_empty_replay_still_sends_replay_done(self):
        ws = await self.client.ws_connect("/ws", headers=AUTH)
        kinds = [(await self.receive(ws))["type"] for _ in range(3)]
        self.assertEqual(kinds, ["hello", "status", "replay_done"])
        await ws.close()

    async def test_status_on_change_and_as_heartbeat(self):
        ws = await self.client.ws_connect("/ws", headers=AUTH)
        await self.until(ws, "replay_done")
        self.panes = statuses(bot="Waiting to restart")
        changed = await self.until(ws, "status", timeout=1)
        self.assertEqual(changed["panes"][0]["state"], "waiting")
        beat = await self.until(ws, "status", timeout=2)   # nothing changed: heartbeat
        self.assertEqual(beat["panes"], changed["panes"])
        await ws.close()

    async def test_lines_from_another_thread_arrive(self):
        ws = await self.client.ws_connect("/ws", headers=AUTH)
        await self.until(ws, "replay_done")
        t = threading.Thread(target=self.hub.publish, args=("bot", "from a thread"))
        t.start()
        t.join()
        self.assertEqual((await self.until(ws, "line"))["text"], "from a thread")
        await ws.close()

    async def test_a_client_too_far_behind_is_closed_with_1013(self):
        ws = await self.client.ws_connect("/ws", headers=AUTH)
        await self.until(ws, "replay_done")
        client = next(iter(self.hub.clients))
        for i in range(cr.MAX_BEHIND + 1):
            client.push("bot", {"seq": 10_000 + i, "ts": 0, "text": "x"})
        while True:
            msg = await ws.receive(timeout=3)
            if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING):
                break
        self.assertEqual(ws.close_code, 1013)

    async def test_client_messages_are_ignored(self):
        ws = await self.client.ws_connect("/ws", headers=AUTH)
        await self.until(ws, "replay_done")
        await ws.send_str('{"type": "restart", "pane": "bot"}')
        self.hub.publish("bot", "still fine")
        self.assertEqual((await self.until(ws, "line"))["text"], "still fine")
        await ws.close()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FeedThreadTests(unittest.TestCase):
    def test_runs_on_its_own_thread_and_stop_frees_the_port(self):
        said = []
        port = free_port()
        feed = cr.RemoteFeed(cr.Hub(), {"bind": "127.0.0.1", "port": port, "token": TOKEN},
                             statuses, said.append)
        feed.start()
        self.assertTrue(feed.ready.wait(5))
        self.assertTrue(feed.running)
        self.assertIn("listening", said[-1])
        self.assertNotIn(TOKEN, " ".join(said))
        feed.stop()
        self.assertFalse(feed.thread.is_alive())
        with socket.socket() as s:   # the port is free again
            s.bind(("127.0.0.1", port))
        feed.stop()   # twice is fine

    def test_an_address_not_on_this_pc_is_reported_not_raised(self):
        said = []
        feed = cr.RemoteFeed(cr.Hub(), {"bind": "203.0.113.9", "port": free_port(),
                                        "token": TOKEN}, statuses, said.append)
        feed.start()
        self.assertTrue(feed.ready.wait(5))
        self.assertFalse(feed.running)
        self.assertIn("could not start", said[-1])


if __name__ == "__main__":
    unittest.main()
