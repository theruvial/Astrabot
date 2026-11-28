"""Golurk Console remote feed, protocol v1: watch the console's panes from a phone.

Watch-only: the client never sends commands (Start/Stop/Restart from the phone is
not part of v1). The full contract is in docs/CONSOLE_REMOTE.md; in short:

* HTTP + WebSocket on ONE literal IP from console.json
  ``"remote": {"bind": "<Tailscale or LAN IP>", "port": 13386, "token": "..."}``.
  A blank bind means the feed is off; ``0.0.0.0`` / ``::`` are refused. The token
  may come from ``GOLURK_REMOTE_TOKEN`` instead (keeps it out of the Drive-synced
  file). No token, or one under 32 characters, and the server does not start.
* Every request needs ``Authorization: Bearer <token>`` (constant-time compare);
  anything else is a bare 401. Never a query-string token. No TLS: Tailscale is
  the expected path; on the plain LAN the token can be sniffed (known risk).
* ``GET /status`` returns a status message; ``GET /ws?boot=&since=`` streams
  hello, status, replay..., replay_done, then live lines and status heartbeats.

Threads: the console calls ``Hub.publish`` from whatever thread emitted the line
(through ``TapQueue``, which wraps the console's line queue, so direct
``lines.put`` calls are seen too). The hub appends to its ring buffers under a
lock and hands the line to the server's own asyncio loop with
``call_soon_threadsafe``. Nothing here touches Tk, so it imports and tests
without it.

Replay without gaps or repeats: a new client registers for live lines first,
then takes the buffer snapshot (which records the last seq); live lines with a
seq at or below that are dropped, since the replay already had them.

Screens (added 2026-11-25, still protocol 1): the Strata pane's monitor grid,
sent as ``screen`` frames. The console hands the hub the live
``TerminalSession`` with ``Hub.set_screen`` (on open and end) and
``Hub.screen_moved`` (pop out / reattach), from the Tk thread; the hub keeps
only that reference. Once a second, and only while a client is connected, the
server loop copies every row under ``session.lock`` (``sample_screen``; it
never reads or clears ``screen.dirty``, which is the Tk view's) and diffs
against the grid it last sent. Everything that compares and stores grids runs
on the server loop alone, so a new client's full frame (the last-sent grid)
and the next diff always line up. Not buffered, no seq, never replayed.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import os
import queue
import re
import secrets
import threading
import time
from collections import deque

from console_terminal import screen_runs

PROTOCOL = 1
DEFAULT_PORT = 13386
MIN_TOKEN_LENGTH = 32
TOKEN_ENV = "GOLURK_REMOTE_TOKEN"
REPLAY_LINES = 500          # per pane
HEARTBEAT_SECONDS = 10      # a status message at least this often
STATUS_CHECK_SECONDS = 2    # pane states are compared this often
MAX_BEHIND = 5000           # queued messages before a client is closed with 1013
WS_PING_SECONDS = 30
CLOSE_GOING_AWAY = 1001
CLOSE_TRY_AGAIN = 1013
SCREEN_SECONDS = 1          # screens are sampled at most this often
SCREEN = "__screen__"       # Client.items marker: the item is a whole screen frame

# Built-in panes in protocol order. Site, agent and portfolio are cogs inside
# the bot: they show the bot's state and clients do not alert on them.
BUILTIN_ORDER = ("bot", "site", "agent", "portfolio", "absLogin", "wiki", "classifier",
                 "librarian")
FOLLOWS = {"site": "bot", "agent": "bot", "portfolio": "bot"}
# The strata stream only carries header notes; "__" streams are console internals.
HIDDEN_STREAMS = frozenset({"strata"})

ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")


# -- pure helpers -------------------------------------------------------------------
def remote_settings(remote, env=None) -> tuple[dict | None, str]:
    """``(settings, "")`` when the feed should start, else ``(None, why)``.

    ``why`` is "" when the feed is simply off (blank bind), otherwise the line the
    console prints in the Bot pane. Never includes the token. Pure."""
    env = os.environ if env is None else env
    remote = remote if isinstance(remote, dict) else {}
    bind = str(remote.get("bind") or "").strip()
    if not bind:
        return None, ""
    try:
        address = ipaddress.ip_address(bind)
    except ValueError:
        return None, (f"Remote feed off: bind {bind!r} is not one IP address "
                      "(use the Tailscale or LAN address).")
    if address.is_unspecified:
        return None, ("Remote feed off: bind must be one interface address, "
                      "never 0.0.0.0 or ::.")
    try:
        port = int(remote.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = 0
    if not 0 < port < 65536:
        return None, f"Remote feed off: port {remote.get('port')!r} is not a valid port."
    token = str(remote.get("token") or env.get(TOKEN_ENV) or "").strip()
    if not token:
        return None, (f"Remote feed off: no token. Set {TOKEN_ENV} (or remote.token in "
                      "console.json).")
    if len(token) < MIN_TOKEN_LENGTH:
        return None, (f"Remote feed off: the token is shorter than {MIN_TOKEN_LENGTH} "
                      "characters.")
    return {"bind": str(address), "port": port, "token": token}, ""


def authorised(header: str | None, token: str) -> bool:
    """True for exactly ``Bearer <token>``, compared in constant time."""
    if not token or not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[len("Bearer "):].strip().encode(), token.encode())


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def visible(stream) -> bool:
    """Streams that go to clients: real panes, not internals or Strata notes."""
    return (isinstance(stream, str) and bool(stream) and not stream.startswith("__")
            and stream not in HIDDEN_STREAMS)


def pane_state(status_text: str) -> str:
    """``Supervisor.status()`` text to the protocol's state word."""
    text = str(status_text or "")
    if text.startswith("Running"):
        return "running"
    if text.startswith("Waiting"):
        return "waiting"
    if text.startswith("Stopped"):
        return "stopped"
    return "unknown"


def pane_entries(builtins: dict, titles: dict, customs) -> list[dict]:
    """The pane status list: built-ins in protocol order, then custom services.

    ``builtins`` maps a supervised pane key to its supervisor; ``customs`` are the
    custom supervisors (``name``, ``title``, ``status()``) in console order."""
    panes = []
    for key in BUILTIN_ORDER:
        follows = FOLLOWS.get(key)
        source = builtins.get(follows or key)
        status = source.status() if source is not None else "Unknown"
        entry = {"key": key, "title": titles.get(key, key), "state": pane_state(status),
                 "status": status}
        if follows:
            entry["follows"] = follows
        panes.append(entry)
    for svc in customs:
        status = svc.status()
        panes.append({"key": svc.name, "title": svc.title, "state": pane_state(status),
                      "status": status})
    return panes


def parse_since(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


# -- the hub ------------------------------------------------------------------------
class Client:
    """One connected WebSocket's live-line queue (lives on the server loop)."""

    def __init__(self):
        self.items: deque = deque()
        self.event = asyncio.Event()
        self.overflow = False
        self.screens = False   # has had its full screen frames; diffs only after

    def push(self, pane, line):
        if self.overflow:
            return
        self.items.append((pane, line))
        if len(self.items) > MAX_BEHIND:
            self.overflow = True
            self.items.clear()
        self.event.set()


def sample_screen(session) -> dict | None:
    """``{"rows", "cols", "cursor", "grid"}`` copied from a TerminalSession, or
    None when it isn't running. Takes ``session.lock`` (the reader thread feeds
    the screen under it) and never touches ``screen.dirty``. ``grid`` is one
    list of ``[text, fg, bg, bold]`` runs per row, ``screen_runs`` output with
    the cursor cell drawn reversed, as the console shows it."""
    if session is None or not getattr(session, "alive", False):
        return None
    with session.lock:
        screen = session.screen
        rows, cols = screen.lines, screen.columns
        cursor = screen.cursor
        cursor_at = None if cursor.hidden else [cursor.y, cursor.x]
        grid = [[list(run) for run in screen_runs(
                    screen.buffer[r], cols,
                    cursor.x if cursor_at is not None and r == cursor.y else None)]
                for r in range(rows)]
    return {"rows": rows, "cols": cols, "cursor": cursor_at, "grid": grid}


def screen_message(key, title, sample, full=True, changed=None, note=None) -> dict:
    """One ``screen`` frame. ``sample`` None = not running (with ``note``).
    ``changed``: the row numbers for a partial frame. Pure."""
    if sample is None:
        frame = {"type": "screen", "key": key, "title": title, "running": False}
        if note:
            frame["note"] = note
        return frame
    rows = range(sample["rows"]) if full else changed
    return {"type": "screen", "key": key, "title": title, "running": True,
            "rows": sample["rows"], "cols": sample["cols"], "full": full,
            "cursor": sample["cursor"],
            "lines": {str(r): sample["grid"][r] for r in rows}}


class Hub:
    """Ring buffers per pane, one seq counter per console run, live fan-out."""

    def __init__(self, replay_lines: int = REPLAY_LINES, clock=time.time):
        self.boot = secrets.token_hex(8)
        self.replay_lines = replay_lines
        self.clock = clock
        self.seq = 0
        self.buffers: dict[str, deque] = {}
        self.lock = threading.Lock()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set[Client] = set()
        # key -> {"session", "title", "note", "gen"}; written from the Tk
        # thread under self.lock. gen goes up whenever a full frame is due.
        self.screens: dict[str, dict] = {}
        # key -> what was last sent ({"gen", "sample"}); server loop only.
        self.screen_sent: dict[str, dict] = {}

    # -- screens: the console's side (any thread) --
    def set_screen(self, key, session, title, note=None) -> None:
        """The pane's terminal session now (None: no monitor open, ``note``
        says why). A new session, or none, always means a full frame."""
        with self.lock:
            old = self.screens.get(key, {})
            self.screens[key] = {"session": session, "title": title, "note": note,
                                 "gen": old.get("gen", 0) + 1}

    def screen_moved(self, key) -> None:
        """Same session, new view (pop out / reattach): send a full frame."""
        with self.lock:
            if key in self.screens:
                self.screens[key]["gen"] += 1

    def _screen_sources(self):
        with self.lock:
            return [(key, dict(src)) for key, src in self.screens.items()]

    # -- screens: the server loop's side --
    def screen_initial(self) -> list[dict]:
        """Full frames for a client that just finished its replay: the grid
        last sent to the others (so the next diff applies), or a fresh sample
        when nobody had one."""
        frames = []
        for key, src in self._screen_sources():
            sent = self.screen_sent.get(key)
            if sent is None or sent["gen"] != src["gen"]:
                sent = self.screen_sent[key] = {"gen": src["gen"],
                                                "sample": sample_screen(src["session"])}
            frames.append(screen_message(key, src["title"], sent["sample"], note=src["note"]))
        return frames

    def screen_frames(self) -> list[dict]:
        """What changed since the last call, as frames for every ready client:
        a full frame for a new or moved session or a new size, changed rows
        otherwise, ``running: false`` once when the monitor stops, nothing
        while the screen is idle."""
        frames = []
        for key, src in self._screen_sources():
            sample = sample_screen(src["session"])
            sent = self.screen_sent.get(key)
            self.screen_sent[key] = {"gen": src["gen"], "sample": sample}
            last = sent["sample"] if sent else None
            if sample is None:
                if sent is None or last is not None or sent["gen"] != src["gen"]:
                    frames.append(screen_message(key, src["title"], None, note=src["note"]))
                continue
            if (sent is None or last is None or sent["gen"] != src["gen"]
                    or (last["rows"], last["cols"]) != (sample["rows"], sample["cols"])):
                frames.append(screen_message(key, src["title"], sample))
                continue
            changed = [r for r in range(sample["rows"]) if sample["grid"][r] != last["grid"][r]]
            if changed or sample["cursor"] != last["cursor"]:
                frames.append(screen_message(key, src["title"], sample, full=False,
                                             changed=changed))
        return frames

    def push_screens(self) -> None:
        """One sampling tick (server loop): diffs to every ready client. With
        nobody connected nothing is sampled and the last-sent grids are
        dropped, so the next client starts from a fresh sample."""
        if not self.clients:
            self.screen_sent.clear()
            return
        for frame in self.screen_frames():
            for client in list(self.clients):
                if client.screens:
                    client.push(SCREEN, frame)

    def publish(self, stream, text) -> None:
        """Called from any thread. Never raises into the console."""
        if not visible(stream) or not isinstance(text, str):
            return
        with self.lock:
            self.seq += 1
            line = {"seq": self.seq, "ts": self.clock(), "text": strip_ansi(text)}
            buffer = self.buffers.get(stream)
            if buffer is None:
                buffer = self.buffers[stream] = deque(maxlen=self.replay_lines)
            buffer.append(line)
            loop = self.loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self._fan_out, stream, line)
            except RuntimeError:  # the loop is closed: the feed is stopping
                pass

    def publish_item(self, item) -> None:
        """``TapQueue`` hook: the console's ``(stream, line)`` tuples."""
        try:
            stream, text = item
        except (TypeError, ValueError):
            return
        self.publish(stream, text)

    def _fan_out(self, stream, line):
        for client in list(self.clients):
            client.push(stream, line)

    def replay(self, boot=None, since=0, order=()) -> tuple[list, int]:
        """``([(pane, [lines])...], last_seq)``. Same boot: only lines after
        ``since``; another boot (or none): the whole buffer. Panes in ``order``
        come first, the rest after in name order."""
        with self.lock:
            after = since if boot == self.boot else 0
            last = self.seq
            keys = [k for k in order if k in self.buffers]
            keys += sorted(k for k in self.buffers if k not in keys)
            out = []
            for key in keys:
                lines = [dict(line) for line in self.buffers[key] if line["seq"] > after]
                if lines:
                    out.append((key, lines))
        return out, last


class TapQueue(queue.Queue):
    """The console's line queue, with every put also shown to ``tap`` first."""

    def __init__(self, tap, maxsize=0):
        super().__init__(maxsize)
        self.tap = tap

    def put(self, item, block=True, timeout=None):
        try:
            self.tap(item)
        except Exception:  # noqa: BLE001 -- the feed must never break the console
            pass
        super().put(item, block, timeout)


# -- the server ---------------------------------------------------------------------
def build_app(hub: Hub, token: str, status_fn, console_name: str = "Golurk Console",
              heartbeat: float = HEARTBEAT_SECONDS, check: float = STATUS_CHECK_SECONDS,
              screen_every: float = SCREEN_SECONDS):
    """The aiohttp application. ``status_fn()`` returns the pane list (see
    ``pane_entries``); it runs on the server loop and must not touch Tk."""
    from aiohttp import WSMsgType, web

    sockets: set = set()
    last_panes = {"value": []}

    def panes_now():
        try:
            last_panes["value"] = status_fn()
        except Exception:  # noqa: BLE001 -- e.g. a service list changing mid-read
            pass
        return last_panes["value"]

    def status_message(panes):
        return {"type": "status", "boot": hub.boot, "time": time.time(), "panes": panes}

    @web.middleware
    async def auth(request, handler):
        if not authorised(request.headers.get("Authorization"), token):
            return web.Response(status=401)
        return await handler(request)

    async def get_status(_request):
        return web.json_response(status_message(panes_now()))

    async def get_ws(request):
        ws = web.WebSocketResponse(heartbeat=WS_PING_SECONDS)
        await ws.prepare(request)
        client = Client()
        hub.clients.add(client)   # before the snapshot: see the module docstring
        sockets.add(ws)

        async def read_and_ignore():
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
            client.event.set()

        reader = asyncio.create_task(read_and_ignore())
        try:
            await ws.send_json({"type": "hello", "protocol": PROTOCOL, "console": console_name,
                                "boot": hub.boot, "server_time": time.time(),
                                "heartbeat_seconds": heartbeat, "replay_lines": hub.replay_lines})
            panes = panes_now()
            await ws.send_json(status_message(panes))
            sent_at = time.monotonic()
            replay, last_seq = hub.replay(request.query.get("boot"),
                                          parse_since(request.query.get("since")),
                                          [p["key"] for p in panes])
            for pane, lines in replay:
                await ws.send_json({"type": "replay", "pane": pane, "lines": lines})
            await ws.send_json({"type": "replay_done", "last_seq": last_seq})
            # Queued, not sent here, and marked ready in the same step (no await
            # between), so no screen diff can slip in before the full frame.
            for frame in hub.screen_initial():
                client.push(SCREEN, frame)
            client.screens = True
            while not ws.closed and not reader.done():
                try:
                    await asyncio.wait_for(client.event.wait(), check)
                except asyncio.TimeoutError:
                    pass
                client.event.clear()
                if client.overflow:
                    await ws.close(code=CLOSE_TRY_AGAIN, message=b"fell behind")
                    break
                while client.items and not ws.closed:
                    pane, line = client.items.popleft()
                    if pane == SCREEN:
                        await ws.send_json(line)
                        continue
                    if line["seq"] <= last_seq:
                        continue  # already in the replay
                    await ws.send_json({"type": "line", "pane": pane, **line})
                now_panes = panes_now()
                if now_panes != panes or time.monotonic() - sent_at >= heartbeat:
                    if ws.closed:
                        break
                    panes = now_panes
                    await ws.send_json(status_message(panes))
                    sent_at = time.monotonic()
        except (ConnectionResetError, RuntimeError):
            pass  # the client went away mid-send
        finally:
            hub.clients.discard(client)
            sockets.discard(ws)
            reader.cancel()
        return ws

    async def sample_screens():
        while True:
            await asyncio.sleep(screen_every)
            try:
                hub.push_screens()
            except Exception:  # noqa: BLE001 -- a bad sample must not end the loop
                pass

    async def attach_loop(app_):
        hub.loop = asyncio.get_running_loop()
        app_["screens"] = asyncio.create_task(sample_screens())

    async def close_sockets(app_):
        hub.loop = None
        task = app_.get("screens")
        if task is not None:
            task.cancel()
        for ws in list(sockets):
            await ws.close(code=CLOSE_GOING_AWAY, message=b"console closing")

    app = web.Application(middlewares=[auth])
    app.router.add_get("/status", get_status)
    app.router.add_get("/ws", get_ws)
    app.on_startup.append(attach_loop)
    app.on_shutdown.append(close_sockets)
    return app


class RemoteFeed:
    """Runs ``build_app`` on its own thread and asyncio loop.

    ``say(text)`` reports to the Bot pane (it must be thread-safe: the console's
    ``emit``). ``stop()`` closes every client with 1001 and frees the port."""

    def __init__(self, hub: Hub, settings: dict, status_fn, say,
                 console_name: str = "Golurk Console"):
        self.hub, self.settings, self.status_fn, self.say = hub, settings, status_fn, say
        self.console_name = console_name
        self.loop: asyncio.AbstractEventLoop | None = None
        self.runner = None
        self.thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.running = False

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="golurk-remote", daemon=True)
        self.thread.start()

    def _run(self):
        loop = asyncio.new_event_loop()
        self.loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except Exception as exc:  # noqa: BLE001 -- e.g. the address is not on this PC
            self.say(f"Remote feed could not start on {self.settings['bind']}:"
                     f"{self.settings['port']}: {exc}")
            self.ready.set()
            loop.close()
            return
        self.running = True
        self.ready.set()
        self.say(f"Remote feed listening on http://{self.settings['bind']}:"
                 f"{self.settings['port']} (protocol {PROTOCOL}, token required).")
        try:
            loop.run_forever()
        finally:
            loop.close()

    async def _serve(self):
        from aiohttp import web
        app = build_app(self.hub, self.settings["token"], self.status_fn, self.console_name)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.settings["bind"], self.settings["port"])
        try:
            await site.start()
        except BaseException:
            await self.runner.cleanup()
            raise

    async def _shutdown(self):
        if self.runner is not None:
            await self.runner.cleanup()

    def stop(self, timeout: float = 5) -> None:
        """Close clients (1001), free the port, end the thread. Safe to call twice."""
        loop = self.loop
        if loop is None or not self.running:
            return
        self.running = False
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(), loop).result(timeout)
        except Exception:  # noqa: BLE001 -- closing anyway
            pass
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass
        if self.thread is not None:
            self.thread.join(timeout)
