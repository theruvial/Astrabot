# Discord cog for Golurk-Agent: what it needs to do

This is a brief for whoever builds the Discord cog. The agent itself lives in
`C:\codingShit\ha-agent` (package `ha_agent`) and is already working. The cog's only job
is to carry messages between Discord and the agent.

## How the pieces connect

```
Discord user ──message──▶ bot (discord.py) ──▶ HAAgent cog ──await agent.chat()──▶ ha_agent
                                                                                    ├─▶ Ollama  http://localhost:11434  (Qwen3.5-4B)
                                                                                    └─▶ Home Assistant REST  http://192.168.4.229
Discord user ◀──reply + buttons── cog ◀──────────── Reply(text, actions) ◀──────────┘
```

- The cog **imports the agent as a library**. There's no server in between. The agent is fully
  `async` (aiohttp) and never blocks discord.py's event loop.
- **The bot must run on Golurk, the PC running Ollama.** Ollama only accepts connections from
  that machine (127.0.0.1), on purpose. If the bot ever moves to another machine, stop and ask:
  that change needs an HTTP wrapper around the agent and a decision about network exposure.

## Setup the cog relies on

1. Install the agent into the bot's Python environment (editable, so agent fixes apply straight away):
   `pip install -e C:\codingShit\ha-agent`. Dependencies: aiohttp (discord.py already has it),
   `langchain` + `langchain-ollama` (the agent loop, since 2026-11-24),
   `ddgs` (DuckDuckGo search, no API key) and `truststore` (uses Windows' certificate store for HTTPS).
2. Environment variables, read by `HAAgent()` when created:

   | Variable | Required | Default |
   |---|---|---|
   | `HA_TOKEN` | yes | an HA long-lived access token, created by Thea. Never commit it or log it. |
   | `HA_URL` | no | `http://192.168.4.229` (this HA serves on port 80, not 8123) |
   | `HA_AGENT_LLM_URL` | no | `http://127.0.0.1:8080/v1` (Strata). A URL ending in `/v1` is spoken to as OpenAI-compatible; anything else (e.g. `http://localhost:11434`) as Ollama. `OLLAMA_URL` is read too, as the old name. |
   | `HA_AGENT_MODEL` | no | `qwen3.8-flash-next-coder-iq1_m` (Strata serves whatever it loaded; the name only matters for Ollama) |
   | `HA_AGENT_LLM_KEY` | no | API key for the model server; Strata on `--access local` needs none |
   | `HA_AGENT_EMBED_URL` | no | `http://127.0.0.1:11434`: Ollama, which serves `nomic-embed-text` (on the CPU) for notes and document search |
   | `HA_AGENT_EMBED_MODEL` | no | `nomic-embed-text` |
   | `HA_AGENT_DATA_DIR` | no | `~/.golurk-agent`: where `notes.json`, `docs_index.json`, `timers.json` and `learned.json` live |
   | `HA_AGENT_FAST_PATH` | no | `1`. `0` sends every message to the model (no rule-based answers) |
   | `HA_AGENT_LEARN` | no | `1`. `0` stops learning phrasings from the model's choices |
   | `HA_AGENT_DOCS_DIR` | no | A folder of your files (txt, md, html, code; PDF if `pypdf` is installed) for `search_docs`. Unset = no document search. |

   You can also pass them as constructor arguments: `HAAgent(ha_url=..., ha_token=..., llm_url=..., model=...)`
   (`ollama_url=` still works as the old name). A down or failing model server raises `ha_agent.LLMError`
   (a `RuntimeError` whose message starts with "Model server" or "Ollama").
3. The model server, Strata, must be running before the agent gets a message. The cog doesn't start
   it; it runs as a background service on the host. As it sits on 2026-11-25 (profile
   `launch-iq1_m-32k`, Strata 0.1.48):

   ```powershell
   cd C:\Users\theruvial\AppData\Local\Strata\terminal\current
   .\python\python.exe strata.py runtime start --profile launch-iq1_m-32k
   ```

   The profile's launch file is `C:\Users\theruvial\AppData\Local\Strata\launch\iq1_m-32k.json`.
   It serves `qwen3.8-flash-next-coder-iq1_m` on `http://127.0.0.1:8080` (CUDA 12, GPU 0),
   and runs the engine from `C:\Users\theruvial\AppData\Local\Strata\terminal\0.1.48.1` with these arguments:

   ```
   --pack C:\Users\theruvial\Documents\Strata\packs\coder-iq1_m
   --native C:\Strata\models\coder-IQ1_M\Qwen3.8-Flash-Next-GSQ-RCO-IQ1_M-00001-of-00002.gguf
   --ple-gguf C:\Strata\models\coder-IQ1_M\Qwen3.8-Flash-Next-GSQ-RCO-IQ1_M-00002-of-00002.gguf
   --expert-profile C:\Users\theruvial\AppData\Local\Strata\terminal\0.1.48.1\data\expert-profile-coder.bin
   --expert-cache auto --prefill auto --spec 4 --max-context 32768 --kv k8v4
   --resident-experts --vram-reserve-mib 1472
   ```

   - `--vram-reserve-mib 1472` keeps that much VRAM free for Windows and other apps. Strata sizes its
     expert cache from free VRAM when it starts, so restart it after freeing VRAM (e.g. moving
     another GPU program to the CPU) for it to take the space.
   - To restart, run `runtime restart --profile launch-iq1_m-32k --plan --json` first. Then rerun
     without `--plan`, passing `--if-owner-id` and `--if-instance-id` from the plan (both are
     required) and `--startup-wait 600`. After an edit to the launch file, also pass
     `--allow-source-migration` and `--if-source-sha256 <candidate_source_sha256>`.
   - `runtime status --profile launch-iq1_m-32k` shows whether it's up. If it's down, `chat()`
     raises `LLMError` (see §7).
4. The bot needs the **Message Content** privileged intent if it reads normal messages (not only slash commands).

## The agent's interface (all the cog touches)

```python
from ha_agent import HAAgent, HAError, Reply, ActionRecord

agent = HAAgent(on_timer=send_timer)   # reads env vars; on_timer: see "Timers" below
                                       # fast_path=/learn_phrases= (bool) override HA_AGENT_FAST_PATH/_LEARN
await agent.start()                    # loads HA's action list + devices; raises HAError if HA is unreachable
reply: Reply = await agent.chat(
    conversation_id: str,              # memory key; same id = it remembers the last 6 exchanges
    text: str,                         # the user's message, mention stripped
    confirm=async_fn,                  # async (summary: str) -> bool, awaited before risky actions
    speaker="Thea",                    # optional display name, shown to the model
    think=None,                        # True/False = force thinking for this message; None = use the toggle
    mode=None,                         # "general"/"code" for this message only ("home" = general); None = conversation's mode
    on_step=None,                      # plain fn(dict) called per step while it works; see "Live steps" below
)
agent.mode(conversation_id)            # current mode, default "general"
agent.set_mode(conversation_id, "code")  # raises ValueError for unknown names
# MODES (from ha_agent import MODES): dict name -> Mode with .title, e.g. MODES["code"].title == "Code"
#   Old name "home" still works: MODES["home"] is general, set_mode(cid, "home") returns "general"
agent.loaded_groups(conversation_id)   # tool groups loaded for it, e.g. {"home", "timers"}
agent.forget(conversation_id)          # wipe that conversation's memory
agent.has_history(conversation_id)     # True if the agent remembers anything for it (False after a restart)
agent.remember(conversation_id, [      # add earlier turns, oldest first, e.g. rebuilt from a Discord thread
    ("user", "Thea: turn off the bed fan"),   # role "user"/"assistant"; ValueError for anything else
    ("assistant", "The bed fan is off."),
])
agent.set_thinking(conversation_id, on=None)  # toggle thinking (on=True/False to set); returns new value
agent.thinking(conversation_id)        # current toggle value (default False = fast)
await agent.reload_catalog()           # re-read HA's actions (after adding an integration); returns count
agent.learned()                        # learned phrasings: [{pattern, action, count, promoted, seen}], promoted first
agent.unlearn("kill {device}")         # forget one; True if it existed
await agent.close()                    # close HTTP sessions
```

`Reply` fields:
- `text: str`: what to send back.
- `actions: list[ActionRecord]`: everything it ran or tried. Each has `.action` (e.g. `"fan.set_percentage"`), `.targets` (friendly names), `.options` (dict), `.ok` (bool) and `.result` (`"ok"`, `"declined"` or the error).
- `tool_calls: int` and `seconds: float`, for a footer or logs.
- `mode: str`: which mode actually answered. `routed_by: str`: how it was answered:
  `"fast"` (a rule answered without the model), `"learned"` (a phrasing learned from the model answered
  without it), `"keywords"` or `"model"` when general handed it to code, or `""` if the model in general
  answered or the mode was set explicitly. (`"audit"` and `"home"` no longer occur.) Fast and learned
  replies have `mode == "general"`, `tool_calls == 1`, no thinking, and take well under a second.
- `thought: bool`: whether thinking was on. `thinking: str`: the model's reasoning, if any (never part of `text`).

`chat()` already serializes messages within one conversation, so the cog doesn't need its own lock.
Different conversations can run at once; the GPU just queues them.

`confirm` must return exactly `True` to allow. Anything else (`False`, `None`, a timeout) or an
exception inside it counts as **declined**.

### Live steps (`on_step`)
Pass `on_step=fn` to see the model work while it answers. `fn` gets one plain `dict` per step, in
order, all before `chat()` returns or raises:

| `type` | When | Keys |
|---|---|---|
| `model` | a model call returned, before its tools run | `thinking` (str, `""` if none), `calls` (list of `{"id", "name", "args"}`, may be empty) |
| `tool` | one tool call finished | `id` (matches a call in the `model` event), `name`, `ok` (bool), `result` (str, what the model was told, capped at 4000 chars) |
| `note` | the agent sent the model back or switched mode | `text` (one plain sentence) |

- `fn` is called, not awaited, on the event loop: buffer and return at once; post to Discord from your own task.
- If `fn` raises, the agent logs it and carries on. With or without `on_step`, the model gets the same requests.
- A call in `model.calls` with no `tool` event never ran (the tool-call limit refused it).
- `ok` is False for bad arguments, an unknown tool, a declined confirmation and a tool's own `error: ...` result.
- Fast-path and learned answers use no model and send nothing. Ignore types and keys you don't know;
  more may be added. Check `"on_step" in inspect.signature(agent.chat).parameters` to support older agents.

### Memory, and restoring it after a restart
- The agent keeps the last `history_turns` exchanges (default 6) per conversation id, in memory
  only. A bot restart forgets them all. So do mode and the thinking toggle.
- `remember(cid, turns)` adds earlier turns. It works the same way `chat()` stores turns: blank
  texts are skipped, assistant text over 800 characters is clipped, and only the last
  `history_turns` exchanges are kept, so passing a whole long thread is fine. It appends, so call
  it once before the first `chat()` for that id, guarded by `has_history`:
  ```python
  if not agent.has_history(cid):
      turns = []
      async for m in thread.history(limit=20, oldest_first=False):   # newest first...
          ...                                                          # build (role, text) pairs
      agent.remember(cid, list(reversed(turns)))                       # ...so reverse to oldest first
  reply = await agent.chat(cid, text, speaker=name, ...)
  ```
  Leave out the message you're about to pass to `chat()`, or it appears twice.
- Write user turns the way `chat()` stores them: `"Name: message"` when you pass `speaker=`
  (the cog does), with the bot mention stripped. Bot replies are `"assistant"`. Leave out footers,
  confirmation prompts and error messages; only the conversation itself helps the model.
- Only the most recently used `max_conversations` ids (constructor argument, default 500) are
  kept; older ones are dropped and `has_history` turns False, so the same rebuild covers them.

## What the cog must do

### 1. Lifecycle
- `cog_load`: create the agent and `await agent.start()`. If that raises `HAError`, log it and keep
  the cog loaded but answer messages with "Home Assistant isn't reachable", then try `start()` again
  on the next message. Don't crash the bot.
- `cog_unload`: `await agent.close()`.
- Timers: pass `on_timer`, an `async (conversation_id, text)` function, to `HAAgent(...)`. Each timer
  calls it when it ends, with the id of the conversation that set it (`discord:<channel id>` or
  `discord:dm:<user id>`) and a ready message like `⏰ Timer done: pasta (Thea)`. Map the id back to
  the channel or DM and send the text. Without `on_timer`, `set_timer` refuses and the model says
  timers aren't available. Timers are saved in `HA_AGENT_DATA_DIR` and re-armed by `start()`, so a
  restart keeps them; one that ended while the bot was down fires on start, saying it's late.

### 2. Decide which messages go to the agent
- **Only allowlisted Discord user IDs** may use it. Whoever can talk to this cog controls the
  house (lights, locks if added, restarts). Use an ID allowlist in the bot's config, not role names
  (anyone with Manage Roles can grant a role). Ignore everyone else silently or with a short "not allowed".
- Trigger on: DMs from allowed users, messages in one configured channel, or @mentions of the bot.
  Pick whichever the bot already uses; make the channel ID configurable.
- Ignore messages from bots (including itself).
- Strip the bot mention from the text before passing it on.

### 3. Pick the conversation id
- Use `f"discord:{channel.id}"` for a channel and `f"discord:dm:{user.id}"` for DMs.
  Same id = shared short-term memory ("turn it back on" works after "turn off the fan").
- For one thread per request, use the thread's id (`f"discord:{thread.id}"`): each thread is its
  own conversation. After a restart, rebuild it from the thread's messages with `remember()`
  (see "Memory, and restoring it after a restart" above).

### 4. Call the agent
- Wrap the call in `async with channel.typing():`. A normal reply takes 2–6 s; the first message
  after Ollama has unloaded the model takes about 10–15 s longer.
- Pass `speaker=message.author.display_name`.
- Add a timeout as a safety net (`asyncio.wait_for(..., 180)`).

### 5. Confirmation buttons (required)
The agent asks before risky actions (restarting HA, unlocking, opening covers, add-on/backup/update
actions). It does that by awaiting the `confirm` function the cog passes in. The cog must:
- Build a `confirm(summary)` closure for each message that posts the summary (e.g.
  "Allow **homeassistant.restart on Home Assistant**?") with **Allow** / **Deny** buttons
  (`discord.ui.View`).
- Only accept clicks from **the user who sent the original message**; reply ephemerally to anyone else.
- Return `True` on Allow, `False` on Deny or after a **60 s timeout**. Disable the buttons afterwards
  and edit the message to show the outcome.
- Return `False` (never raise) on any error inside confirm.
- Never auto-approve.

### 6. Send the reply
- Reply to the original message with `reply.text`. Split anything over 2000 characters.
- Optional small footer from `reply.actions`, e.g. `✅ fan.set_percentage → Air Purifier {percentage: 40}`
  or `❌ lock.unlock → Front Door (declined)`, plus `reply.seconds`.
- Disable mention pings in replies (`allowed_mentions=discord.AllowedMentions.none()`).

### 7. Handle errors
| Exception | Meaning | Say |
|---|---|---|
| `HAError` | HA unreachable or the token was rejected | "Home Assistant isn't responding" (log the message) |
| `aiohttp.ClientConnectorError` / `RuntimeError` starting with "Ollama" (the agent turns LangChain/Ollama connection and server errors into this) | Ollama isn't running | "The local model isn't running. Start Ollama on Golurk." |
| `asyncio.TimeoutError` | stuck | "That took too long, try again" |
Log the full traceback; never send tracebacks or the token to Discord.

### 8. Thinking toggle (required)
Thinking makes Qwen reason before answering: smarter on hard requests, but about 3x slower
(measured: 3.7 s off vs 13.0 s on for the same request). It's off by default.
- `/think` with no text **toggles** thinking for the conversation: `agent.set_thinking(cid)`.
  Reply with the new state, e.g. "Thinking is now ON (smarter, slower)".
  Also accept `/think on` and `/think off` (`agent.set_thinking(cid, True/False)`).
- `/think <message>` thinks for **that one message only**: `agent.chat(cid, message, think=True, ...)`.
  It doesn't change the toggle.
- As a slash command: `/think` with an optional `message` argument covers both cases.
- When `reply.thought` is true, mark the reply (e.g. a 🧠 in the footer). Optionally show
  `reply.thinking` in a collapsed `||spoiler||` or a "Show reasoning" button. Truncate it, since it
  can run past 2000 characters.
- The toggle lives in the agent's memory, so it resets to off when the bot restarts.

### 9. Modes (required)
One model, two system prompts. Switching never reloads anything.

| Mode | Does | Tools |
|---|---|---|
| `general` (**default**) | The smart home, everyday questions, web lookups, math, dates, time zones, units, long-term notes, your documents, timers. Hands coding to `code` automatically, per message | Always sent: `status`, `find`, `use`, `switch_mode`, `load_tools`. Loaded per conversation (see below): `web_search`, `open_url` (paged, with `find`), `calc`, `days_between`, `date_add`, `time_in`, `convert`, `save_note`, `search_notes`, `delete_note`, `search_docs` (only with `HA_AGENT_DOCS_DIR`), `set_timer`, `list_timers`, `cancel_timer` (need `on_timer`) |
| `code` | Writes and explains code (answers only; it can't touch files) | none |

`home` used to be a mode of its own; it is now part of general and the name is an alias, so a saved
or typed `home` keeps working. Drop it from your `/mode` choices (build them from `MODES`, which no
longer lists it).

Tool loading: to keep each request short (and the model server's cached prompt reusable), general
sends only the smart-home tools, `switch_mode` and `load_tools`. The other groups (`web`, `math`,
`memory`, `timers`) are one line each in the system prompt; the model calls `load_tools` to get one,
or calls a listed tool directly, which runs it and loads its group. A loaded group stays for the
conversation until `forget()`. The cog doesn't need to do anything for this.

- `/mode` with no argument: show the current mode (`MODES[agent.mode(cid)].title`).
- `/mode <name>`: `agent.set_mode(cid, name)`. Catch `ValueError` and list the valid names.
  As a slash command, use a choice parameter built from `MODES`.
- Per-message override (optional): e.g. `/code <message>` → `agent.chat(..., mode="code")`.
- Show which mode answered, e.g. footer `💻 code (auto)`, `💻 code`, `💬 general`, using
  `reply.mode` and `reply.routed_by`. That makes wrong guesses easy to notice. For `routed_by`
  `"fast"` or `"learned"`, a `⚡` (e.g. `⚡ fast`, `⚡ learned`) shows the model was skipped.
- Code replies can be long and contain fenced blocks. When splitting at 2000 characters, don't cut
  inside a code block: close it with ``` at the split and reopen it in the next message.
- Mode and the thinking toggle both reset to defaults when the bot restarts.
- Web answers take longer: about 3–10 s for a search, 15–25 s when it also reads a page. Keep the
  typing indicator running for the whole call (`async with channel.typing()` already does).
- Web tool calls don't show up in `reply.actions` (that list is only Home Assistant actions).
  `reply.tool_calls` still counts them, so a footer like `🔎 3 tools` is possible.
- `open_url` refuses localhost and LAN addresses on purpose, so a Discord message or a web page
  can't make the agent reach Home Assistant, Ollama or the router. The cog doesn't need to filter links.

### 10. Slash commands (nice to have)
- `/ha reset`: `agent.forget(conversation_id)`.
- `/ha reload`: `await agent.reload_catalog()`, then reply with the count.
- `/ha learned`: list `agent.learned()` (pattern → action, ✅ when promoted).
- `/ha unlearn <pattern>`: `agent.unlearn(pattern)`; say whether it existed. Autocomplete from
  `agent.learned()`.
- `/ha ask <text>`: same as a normal message, for servers where message content is off.
All of these are restricted to the same allowlist.

## Testing without touching the real house
`tests/fake_ha.py` in the agent repo swaps in a fake Home Assistant (`agent.ha = FakeHA()`
and `agent.home.ha = agent.ha`) and records every action instead of running it. The cog can
use the same trick in a dev mode so the Discord flow (typing, buttons, splitting, errors) can be
tested while the real devices are left alone. Restarting HA through the fake is a good way to test
the confirm buttons.

## Out of scope for the cog
- Choosing tools, prompts or how devices are matched: that's the agent's job. If replies are wrong,
  fix it in `ha_agent`, not in the cog.
- Voice. That comes later through Home Assistant Assist, not Discord.
