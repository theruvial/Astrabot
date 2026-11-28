# Discord cog for Golurk-Agent: what it needs to do

This is a brief for whoever builds the Discord cog. The agent itself lives in
`C:\codingShit\ha-agent` (package `ha_agent`) and is already working. The cog's only job
is to carry messages between Discord and the agent.

The [cog upgrade](#cog-upgrade) covers attachment input, file replies, folder grants,
and memory. Use it when upgrading to the agent version that exposes the new chat arguments.

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
    conversation_id,                   # the same id resumes the same conversation
    text,                              # the user's message, mention stripped
    confirm=async_fn,                  # async (summary: str) -> bool, awaited before risky actions
    speaker="Thea",                    # optional display name, shown to the model
    think=None,                        # True/False = force thinking for this message; None = use the toggle
    mode=None,                         # "general"/"code" for this message only ("home" = general); None = conversation's mode
    on_step=None,                      # plain fn(dict) called per step; see "Live steps" below
    user_id=user_id,                   # verified Discord author ID as a string
    message_id=message_id,             # Discord message ID as a string, when available
    is_dm=is_dm,
    attachments=attachments,           # see "Cog upgrade" below
)
agent.mode(conversation_id)            # current mode, default "general"
agent.set_mode(conversation_id, "code")  # raises ValueError for unknown names
# MODES (from ha_agent import MODES): dict name -> Mode with .title, e.g. MODES["code"].title == "Code"
#   Old name "home" still works: MODES["home"] is general, set_mode(cid, "home") returns "general"
agent.forget(conversation_id)          # clear recent context and automatic recall for this conversation
agent.has_history(conversation_id)     # True if recent context is available, including after restart
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
- A call in `model.calls` with no `tool` event never ran. The agent has no default tool-call
  cap. Hosts can opt in with `HAAgent(max_tool_calls=...)`.
- `ok` is False for bad arguments, an unknown tool, a declined confirmation and a tool's own `error: ...` result.
- Fast-path and learned answers use no model and send nothing. Ignore types and keys you don't know;
  more may be added. Check `"on_step" in inspect.signature(agent.chat).parameters` to support older agents.

### Memory, and restoring it after a restart

- Use the same conversation ID after a restart. The agent restores the last six eligible
  exchanges by default and can recall older exchanges from that same conversation. Mode and
  the thinking toggle reset when the process restarts.
- `remember(cid, turns)` can seed recent context from messages sent before the upgrade. Pass
  `(role, text)` pairs oldest first. User text should use `"Name: message"` when the cog
  passes `speaker`. Skip the current message, upload text, bot footers, and confirmations.
  Only do this once during migration. Checking `has_history()` on every turn and re-seeding
  from Discord would undo a user's reset.
- `forget(cid)` clears recent context and durable exchange recall for the conversation.
  Deliberately saved memories are managed through chat. Message IDs retained for duplicate
  detection do not retain the deleted conversation text.
- The agent keeps up to 500 exchanges and message markers per conversation and indexes
  older exchanges as needed. If the embedder is unavailable, recent context still works.

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
  own conversation and the agent restores its history after a restart.

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
  can run past 2000 characters. Astrabot sends both streamed progress and fallback reasoning
  to the configured Discord `channel_id` for requests from DMs and server conversations.
- The toggle lives in the agent's memory, so it resets to off when the bot restarts.

### 9. Modes (required)
One model, two system prompts. Switching never reloads anything.

| Mode | Does | Tools |
|---|---|---|
| `general` (**default**) | Smart home, everyday help, files, and automatic routing to code | `search_home`, `search_file_tools`, `search_assistant` |
| `code` | Writes and explains code and handles programming files | `search_file_tools`, `search_assistant` |

Keep building mode choices from `MODES`. The old `home` name still resolves to `general`.
Search matches are available for the current request. Remove any calls to the old
`loaded_groups()` API from the cog. Use `on_step` if the UI shows what the model discovers.

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
- `/stop` cancels your active Golurk-Agent requests. See [Stopping an active turn](#stopping-an-active-turn).
- `/ha reset`: `agent.forget(conversation_id)`.
- `/ha reload`: `await agent.reload_catalog()`, then reply with the count.
- `/ha learned`: list `agent.learned()` (pattern → action, ✅ when promoted).
- `/ha unlearn <pattern>`: `agent.unlearn(pattern)`; say whether it existed. Autocomplete from
  `agent.learned()`.
- `/ha ask <text>`: same as a normal message, for servers where message content is off.
All of these are restricted to the same allowlist.

### Stopping an active turn

Handle `/stop` directly in the cog so the command can run while the agent is busy.
For each of the requester's active turns, use the conversation ID from `chat()` and
pass the verified Discord author ID from the slash interaction.

```python
stopped = await agent.stop(conversation_id, user_id=str(interaction.user.id))
```

- A `True` result means the active turn was cancelled and its lock was released.
  A `False` result means this requester has no active turn to stop in this conversation.
- When the original `chat()` call returns with `reply.stopped`, close its progress
  UI, dismiss any pending confirmation view, and skip the usual reply and file sends.
  Use the slash interaction to acknowledge the stop.
- Cancellation applies to the current turn. Requests queued behind it can still run.
  Actions already sent to Home Assistant and file operations already started can
  complete. The agent does not archive a stopped turn as a completed exchange.

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

## Cog upgrade

Upgrade the agent package and use the same message handling path for DMs, channel messages,
and thread replies. Keep the existing allowlist, typing indicator, timeouts, and confirmation UI.

### Pass Discord messages and attachments to the agent

- Apply the existing user, channel, and thread rules before downloading any upload. Within those
  rules, accept a message with attachments even when its text is empty after stripping the mention.
- Keep the existing conversation IDs. Pass the Discord author ID separately from the display
  name and identify DMs explicitly. The agent uses the stable ID for personal memories and the
  conversation ID for thread history and files. Include the original Discord message ID so
  retries and replayed events can be recognized.
- Pass the accepted message's uploads to the agent package's
  `read_discord_attachments()` helper. Pass its result and the message text separately to
  `chat()`. The helper handles up to four UTF-8 uploads of up to 256 KiB each, with the agent
  adding up to 3,000 characters per file to the prompt. This covers plain text, Markdown, CSV,
  and source files. Unsupported, oversized, and failed uploads should produce a readable
  notice while the other uploads and the message continue through the normal flow.
- Keep the existing confirmation and live-step callbacks. Tool searches and selection happen
  inside the agent through `search_home`, `search_file_tools`, and `search_assistant`.
  The agent runs its fast path before starting tool discovery.

For a normal message or thread reply, pass the Discord message metadata as follows.

```python
from ha_agent import read_discord_attachments

attachments = await read_discord_attachments(message.attachments)
reply = await agent.chat(
    conversation_id,
    text,
    speaker=message.author.display_name,
    user_id=str(message.author.id),
    message_id=str(message.id),
    is_dm=message.guild is None,
    attachments=attachments,
    confirm=confirm,
    approve_folder=approve_folder,
    ask_user=ask_user,
    on_step=on_step,
)
if reply.duplicate:
    return
```

### Send generated or edited files back

- An edit to an upload produces a copy that the user can download in Discord. The agent creates
  that copy from the complete accepted upload, even when the prompt only includes a preview.
  It writes the copy to the conversation's workspace and returns its filename and bytes in
  `Reply.files`. The original Discord upload is preserved.
- Build Discord uploads from returned bytes with `discord.File(io.BytesIO(file.data),
  filename=file.filename)`. Send them with the reply in the conversation that requested them.
  Keep the current text chunking and mention settings. If an upload fails, tell the user which
  file failed and preserve the text reply.
- Include any `reply.attachment_errors` as upload notices alongside the text reply. An
  empty `reply.files` list means no files need to be uploaded.

### Authorize folders on the agent host

The agent reads a local list of folder grants. Set
`HA_AGENT_FOLDER_GRANTS` to the path of a JSON file on the machine running the agent and
restart the process after changing the file. Each grant maps a short alias to a local
folder and gives specific Discord user IDs access. Access defaults to `read_write`.
For example, an owner can make a manuals folder available for searching and reading.

```json
{
  "manuals": {
    "path": "C:\\Users\\you\\Documents\\Manuals",
    "access": "read",
    "discord_user_ids": ["123456789012345678"]
  }
}
```

- Grants default to DMs. An optional `conversation_ids` list can grant access in selected
  shared conversations for the specified users. Only enable a shared conversation if
  everyone who can read it may also see files or excerpts posted there.
- Users refer to folders by alias, for example `manuals:appliances/washer.md`. The agent
  checks each file operation against the caller's ID, the conversation, and the grant.
  It rejects paths that leave the authorized folder, blocks linked paths, and keeps its
  private state and grant configuration out of the file tools.
- The cog uses the same verified user and conversation metadata it passes for memory.
- To expose the existing Nomic document index to Discord, point `HA_AGENT_DOCS_DIR` at a
  folder with a matching grant. Searching other granted folders by filename or text does
  not require an index.

### Ask for folder approval in Discord

Set `folder_approver_ids` in Astrabot's agent settings to the Discord IDs of the
people allowed to approve access to local folders. An empty or absent list uses the
agent host's comma-separated `HA_AGENT_FOLDER_APPROVER_IDS` environment variable.
Astrabot passes the optional
`approve_folder` callback to `chat()` separately from the existing Home Assistant
`confirm` callback. The agent discovers `request_folder_access` through
`search_file_tools` and calls the callback when the user mentions an absolute local
folder path. Quote paths with spaces.

```python
from ha_agent import FolderGrantDecision, FolderGrantRequest

async def approve_folder(request: FolderGrantRequest) -> FolderGrantDecision:
    # Display request.path, request.access, and request.user_id in an embed.
    # Send it in request.conversation_id. Check request.approver_ids on every click.
    # On Ask, open a Discord modal and wait for an authorized approver's submission.
    ...
    return FolderGrantDecision(action="ask", approver_id=str(approver.id), text=modal_text)
```

- **Yes** returns `FolderGrantDecision("yes", str(approver.id))`. The agent records the grant
  for the requester and returns the alias to use in file tools. A grant requested in a
  shared thread works only in that thread. A DM grant works in that requester's DMs.
- **No** returns `FolderGrantDecision("no", str(approver.id))`. A timeout or cancelled
  prompt can return `FolderGrantDecision("no")`.
- In Astrabot, **Ask** opens a Discord modal for the approver's question or revision.
  The submission goes to the original `chat()` call through `text`. Ask leaves the grant
  pending. The model can answer in the conversation or propose a revision for fresh approval.
- The agent stores Discord-approved grants in `HA_AGENT_DATA_DIR/folder_access.json`.
  To revoke one, remove its entry on the host and restart the agent.

### Ask a yes-or-no clarification

The model can discover `ask_user` through `search_assistant`. The optional
`chat(ask_user=callback)` callback receives `AskUserRequest(question, user_id,
conversation_id)`, exported by `ha_agent`. Show the question in an embed with **Yes**
and **No** buttons. Accept clicks from `request.user_id` and return `True` for Yes,
`False` for No, or `None` for dismissal or timeout. Open-ended questions arrive as
ordinary chat replies. Use the folder-approval callback for all folder permission decisions.

### Scope memory by user and conversation

- Explicit requests such as “remember this,” “what do you remember about me?” and “forget that”
  go through the usual chat handler. The agent stores personal memories against the author ID
  for recall across private chats. In shared channels, only memories explicitly saved for that
  channel are available. Saved notes created before the upgrade have no verified Discord
  owner. They stay available in local chat and must be re-saved in the intended Discord
  scope to make them available there.
- Automatic recall stays within the current conversation, including after a bot restart. The
  agent persists and retrieves relevant exchanges using its configured Nomic embedder. Use
  `remember()` only to seed recent context from pre-upgrade messages. The agent handles
  new messages and deduplication from their Discord message IDs.
- Attachment contents are excluded from automatic memory. If the user explicitly asks to
  remember something from an attachment, the agent can save the requested information under
  the same memory scope rules.

### Verify in a test server

- Send an attachment with and without accompanying text. Try one unsupported or failed
  upload alongside a readable one and confirm the readable file still reaches the agent.
- Ask for an edited copy and download the result in the originating thread.
- Save a personal memory in a DM, restart the bot, and retrieve it in the DM. Check that a shared
  channel and a second user cannot retrieve it. In the agent integration tests, use two private
  conversation IDs for the same user to verify that personal memory follows the user ID.
- Continue a conversation after enough exchanges for an earlier decision to fall out of
  recent history. Verify it can still be recalled in its original thread after a restart.
- Authorize a read-only folder for one Discord user. Verify reading works in their DM,
  writes fail, and the alias is unavailable to a second user and in a shared thread.
- With fake Home Assistant, verify a fast-path request and a request that needs discovery.
  Confirm that risky actions still prompt the original requester for approval.
