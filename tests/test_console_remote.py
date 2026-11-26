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


def fake_session(cols=20, rows=5, hide_cursor=True):
    """A TerminalSession stand-in: a real pyte screen, a lock, alive. No pty."""
    import pyte
    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    session = types.SimpleNamespace(screen=screen, stream=stream, lock=threading.Lock(),
                                    alive=True)
    if hide_cursor:
        stream.feed("\x1b[?25l")
    return session


def draw(session, text):
    with session.lock:
        session.stream.feed(text)


class ScreenTests(unittest.TestCase):
    """Screen frames: sampling, diffs, and when a full frame is due. No Tk, no pty."""

    def test_sample_copies_every_row_as_runs_and_leaves_dirty_alone(self):
        import console_terminal as term
        session = fake_session()
        draw(session, "\x1b[31mred\x1b[0m ok")
        session.screen.dirty.update({0, 3})
        dirty = set(session.screen.dirty)
        sample = cr.sample_screen(session)
        self.assertEqual(session.screen.dirty, dirty)  # the Tk view's, untouched
        self.assertEqual((sample["rows"], sample["cols"], sample["cursor"]), (5, 20, None))
        self.assertEqual(len(sample["grid"]), 5)
        self.assertEqual(sample["grid"][0][0], ["red", term.NAMED["red"], term.DEFAULT_BG, False])
        for row in sample["grid"]:
            self.assertEqual(sum(len(run[0]) for run in row), 20)
        self.assertIsNone(cr.sample_screen(None))
        session.alive = False
        self.assertIsNone(cr.sample_screen(session))

    def test_a_visible_cursor_is_reported_and_drawn_reversed(self):
        import console_terminal as term
        session = fake_session(hide_cursor=False)
        draw(session, "ab")
        sample = cr.sample_screen(session)
        self.assertEqual(sample["cursor"], [0, 2])
        self.assertIn([" ", term.DEFAULT_BG, term.DEFAULT_FG, False], sample["grid"][0])

    def test_message_shapes(self):
        self.assertEqual(cr.screen_message("strata", "Strata (laptop)", None, note="closed"),
                         {"type": "screen", "key": "strata", "title": "Strata (laptop)",
                          "running": False, "note": "closed"})
        sample = cr.sample_screen(fake_session(rows=3))
        full = cr.screen_message("strata", "S", sample)
        self.assertTrue(full["full"])
        self.assertEqual(sorted(full["lines"]), ["0", "1", "2"])
        part = cr.screen_message("strata", "S", sample, full=False, changed=[1])
        self.assertEqual((part["full"], list(part["lines"])), (False, ["1"]))

    def test_frames_full_then_changed_rows_then_nothing_while_idle(self):
        hub, session = cr.Hub(), fake_session()
        hub.set_screen("strata", session, "Strata (laptop)")
        first = hub.screen_frames()
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0]["running"] and first[0]["full"])
        self.assertEqual(hub.screen_frames(), [])          # idle: nothing
        draw(session, "\x1b[3;1Hhello")
        part = hub.screen_frames()
        self.assertEqual(len(part), 1)
        self.assertFalse(part[0]["full"])
        self.assertEqual(list(part[0]["lines"]), ["2"])
        self.assertEqual(part[0]["lines"]["2"][0][0].rstrip(), "hello")
        self.assertEqual(hub.screen_frames(), [])

    def test_a_new_size_a_move_or_a_new_session_means_a_full_frame(self):
        hub, session = cr.Hub(), fake_session()
        hub.set_screen("strata", session, "S")
        hub.screen_frames()
        with session.lock:
            session.screen.resize(8, 30)
        frame = hub.screen_frames()[0]
        self.assertTrue(frame["full"])
        self.assertEqual((frame["rows"], frame["cols"], len(frame["lines"])), (8, 30, 8))
        hub.screen_moved("strata")                       # pop out / reattach
        self.assertTrue(hub.screen_frames()[0]["full"])
        hub.set_screen("strata", fake_session(), "S")    # monitor restarted
        self.assertTrue(hub.screen_frames()[0]["full"])

    def test_running_false_once_when_the_monitor_stops(self):
        hub, session = cr.Hub(), fake_session()
        hub.set_screen("strata", session, "S")
        hub.screen_frames()
        session.alive = False                            # ended, console not told yet
        stopped = hub.screen_frames()
        self.assertEqual([f["running"] for f in stopped], [False])
        self.assertEqual(hub.screen_frames(), [])
        hub.set_screen("strata", None, "S", "The monitor closed.")
        again = hub.screen_frames()                      # the console's note follows
        self.assertEqual(again[0].get("note"), "The monitor closed.")
        self.assertEqual(hub.screen_frames(), [])

    def test_a_new_client_gets_the_last_sent_grid_so_the_next_diff_applies(self):
        hub, session = cr.Hub(), fake_session()
        hub.set_screen("strata", session, "S")
        hub.screen_frames()                              # others were sent the blank grid
        draw(session, "\x1b[2;1Hnew")                    # changes after that sample
        initial = hub.screen_initial()[0]
        self.assertTrue(initial["full"])
        self.assertEqual(initial["lines"]["1"][0][0].strip(), "")   # still the sent grid
        diff = hub.screen_frames()[0]
        self.assertEqual(list(diff["lines"]), ["1"])
        self.assertEqual(diff["lines"]["1"][0][0].rstrip(), "new")

    def test_push_only_to_ready_clients_and_only_while_someone_is_connected(self):
        hub, session = cr.Hub(), fake_session()
        hub.set_screen("strata", session, "S")
        hub.push_screens()
        self.assertEqual(hub.screen_sent, {})            # nobody connected: no sampling

        async def run():
            ready, waiting = cr.Client(), cr.Client()
            ready.screens = True
            hub.clients.update({ready, waiting})
            hub.push_screens()
            return ready, waiting

        ready, waiting = asyncio.run(run())
        self.assertEqual([pane for pane, _ in ready.items], [cr.SCREEN])
        self.assertEqual(list(waiting.items), [])


class ScreenProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = cr.Hub()
        self.session = fake_session()
        draw(self.session, "Strata monitor")
        self.hub.set_screen("strata", self.session, "Strata (laptop)")
        app = cr.build_app(self.hub, TOKEN, lambda: statuses(), heartbeat=5, check=0.05,
                           screen_every=0.05)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def until(self, ws, kind, timeout=3):
        while True:
            msg = await ws.receive(timeout=timeout)
            self.assertEqual(msg.type, WSMsgType.TEXT, msg)
            message = msg.json()
            if message["type"] == kind:
                return message

    async def test_full_frame_after_replay_done_then_live_diffs(self):
        ws = await self.client.ws_connect("/ws", headers=AUTH)
        await self.until(ws, "replay_done")
        full = await self.until(ws, "screen")
        self.assertEqual((full["key"], full["title"], full["running"], full["full"]),
                         ("strata", "Strata (laptop)", True, True))
        self.assertEqual(len(full["lines"]), 5)
        self.assertEqual(full["lines"]["0"][0][0].rstrip(), "Strata monitor")
        # Drawn from another thread, as the pty reader does.
        threading.Thread(target=draw, args=(self.session, "\x1b[4;1Hlive")).start()
        diff = await self.until(ws, "screen")
        self.assertFalse(diff["full"])
        self.assertEqual(list(diff["lines"]), ["3"])
        self.assertEqual(diff["lines"]["3"][0][0].rstrip(), "live")
        await ws.close()

    async def test_a_console_without_a_monitor_says_so(self):
        self.hub.set_screen("strata", None, "Strata (laptop)", "The monitor hasn't opened yet.")
        ws = await self.client.ws_connect("/ws", headers=AUTH)
        await self.until(ws, "replay_done")
        frame = await self.until(ws, "screen")
        self.assertEqual(frame, {"type": "screen", "key": "strata", "title": "Strata (laptop)",
                                 "running": False, "note": "The monitor hasn't opened yet."})
        await ws.close()


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
