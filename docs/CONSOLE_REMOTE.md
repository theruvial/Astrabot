# Golurk Console remote feed: protocol v1

The contract between the Golurk Console (`astra_console.py`, the server, built in
`console_remote.py`) and the phone app (the client). It is **watch-only**: the client
never sends commands. Start/Stop/Restart from the phone is not part of v1 (ask Thea
first).

## Transport and auth

- Plain HTTP/WebSocket on one interface address and port from `console.json`:
  `"remote": {"bind": "<Tailscale or LAN IP>", "port": 13386, "token": ""}`.
  - `bind` must be one literal IP address, never `0.0.0.0`, `::` or blank. Blank = feed off.
  - The token should come from the environment variable `GOLURK_REMOTE_TOKEN` (keeps it
    out of the Drive-synced `console.json`); `remote.token` works too. No token, or a
    token shorter than 32 characters, means the server does not start; the console says
    why in the Bot pane. A bind address that is not on this PC is reported the same way.
- Every request needs `Authorization: Bearer <token>`, compared in constant time
  (`hmac.compare_digest`). Anything else gets `401` with no body. The token is never
  accepted in a query string.
- No TLS: the expected path is Tailscale (WireGuard-encrypted). On the plain LAN the
  token can be sniffed; treat that as a known risk.
- Windows Firewall: Thea adds the inbound rule herself (port 13386, Tailscale/LAN range).

## Panes

Pane keys are the console's stream names: `bot`, `site`, `portfolio`, `absLogin`, `wiki`,
`classifier`, `librarian`, and each custom service's `name`. Streams starting with `__`
and the `strata` stream (header notes only) are internal and never sent.

A pane status object:

```json
{"key": "site", "title": "Site (13380)", "state": "running",
 "status": "Running (PID 1234)", "follows": "bot"}
```

- `state`: `running` | `waiting` (crashed, waiting to restart) | `stopped` | `unknown`,
  from `Supervisor.status()`: `Running (PID n)`, `Waiting to restart`, `Stopped`.
- `status`: the console's own text, for display.
- `follows` (optional): the pane has no supervisor of its own and shows another pane's
  state (`site` and `portfolio` follow `bot`). Clients do not alert on these.
- Order: built-ins in the order above, then custom services in the console's order. The
  list is rebuilt for every message (custom services come and go).

## Lines

```json
{"seq": 4812, "ts": 1764079200.123, "text": "[14:00:00] Starting bot."}
```

- `seq`: one counter for all panes, starting at 1 for each console run (`boot`).
- `ts`: Unix seconds (float) when the console received the line.
- `text`: the line as the pane shows it, ANSI escapes stripped.
- The server keeps the last 500 lines per pane for replay.

## `GET /status`

Returns a `status` message (below) as JSON. Handy for checks with `curl`.

## `GET /ws?boot=<boot>&since=<seq>` (WebSocket)

Both query parameters are optional. If `boot` equals the server's current boot id,
replay only sends lines with `seq > since`; otherwise it sends the whole buffer.

Server to client, all JSON text frames with a `type`:

1. `hello`, once on connect:
   `{"type":"hello","protocol":1,"console":"Golurk Console","boot":"<random id>","server_time":1764079200.0,"heartbeat_seconds":10,"replay_lines":500}`
2. `status`, right after `hello`, then whenever any pane's state or status text changes
   (checked every 2 s), and at least every `heartbeat_seconds` even if nothing changed.
   This is the heartbeat the client's dead-man alert watches:
   `{"type":"status","boot":"...","time":1764079200.0,"panes":[<pane status>...]}`
3. `replay`, one per pane that has lines to send, after the first `status`:
   `{"type":"replay","pane":"bot","lines":[<line>...]}`
4. `replay_done`, once, after the last `replay` (sent even if there was nothing):
   `{"type":"replay_done","last_seq":4812}`
5. `line`, live, for every new line after that (never one already replayed):
   `{"type":"line","pane":"bot","seq":4813,"ts":...,"text":"..."}`

Unknown `type`s must be ignored by the client (forward compatibility).

Client to server: nothing in v1. Anything received is ignored.

The server sends WebSocket pings (aiohttp `heartbeat=30`); the client sends its own too
(OkHttp `pingInterval`).

Close codes: `1001` console closing or restarting; `1013` the client fell more than 5000
messages behind (reconnect with `boot`/`since`).

## Server side (how Astrabot does it)

- `console_remote.py` imports without Tk; `tests/test_console_remote.py` needs no Tk.
- The server runs on its own thread with its own asyncio loop and never touches Tk.
- The console's line queue is a `TapQueue`: every `(stream, line)` put on it, from
  whatever thread, goes to `Hub.publish` first, which appends under a lock and hands the
  line to the server loop with `loop.call_soon_threadsafe`. This also catches lines the
  console puts on the queue directly (pull failures, Watchdog messages), not only `emit`.
- A new client registers for live lines before the replay snapshot is taken; live lines
  with a `seq` at or below the snapshot's `last_seq` are skipped, so nothing is lost or
  sent twice.
- Status comes from `remote_status()` in `main()`: the same supervisors as the status
  bar, plus `custom.values()` at call time.
- Stopped in `on_close()` and in `restart_console()` before the relaunch.
