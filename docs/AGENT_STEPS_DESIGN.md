# ha_agent: report each step while answering

A design change for whoever works on the agent (`C:\codingShit\ha-agent`, package
`ha_agent`). Written 2026-11-26 by the Astrabot Discord cog session, against agent
`master` at `7670fa8` with LangChain 1.5.2. Follow that repo's `CONTRIBUTING.md`
(suggested branch: `feature/step-events`; ask Thea before any push, PR or merge).

## Goal

Thea wants to see the model's thought process and tool calls in the Discord channel she
types prompts into, **while the answer is being worked out**, and wants it to **work like
Justin's LangChat** (`egamI-rorriM/LangChat`, `discord_bot.py`, read at `b30c5f8`): one
message per step in the thread: `💭 **Thinking**` with the full reasoning,
`🔧 Calling: **names**`, a card per result (arguments and the first 500 characters, ❌ on
failure), and `⏳ Still working…` through quiet stretches.

For the agent this means one thing: report each model call and each tool result as it
happens. LangChat's own Discord bot also gets reasoning one whole block per model call
(its token stream feeds only its web UI), so no token streaming is needed. Don't port
LangChat's task queue or card text into the agent.

## Why the agent has to change

The cog only gets the finished `Reply`:

- `reply.thinking` is every model call's reasoning joined at the end, with no link to the
  calls it led to.
- `reply.actions` lists Home Assistant actions only. `status`, `find`, `web_search`,
  `open_url`, notes, documents, math and timers are only a number (`reply.tool_calls`).
- Nothing arrives before the whole run ends, and nothing at all if it times out or fails.

Nothing else needs to change. All formatting, Discord messages, rate limits and clipping
stay in the cog, and the agent learns nothing about Discord.

## The interface

One new optional argument:

```python
async def chat(self, conversation_id, text, confirm=_deny, speaker=None, think=None,
               mode=None, on_step: Callable[[dict], None] | None = None) -> Reply:
```

The cog looks for a parameter named **`on_step`** in `chat`'s signature and passes it only
when it's there, so either side can ship first. Keep that name, as a real parameter (not
hidden in `**kwargs`).

`on_step` gets plain dicts (no shared class: the cog must load without `ha_agent`, and
`/ha reload` re-imports the package). Every dict has `type`. The cog ignores types and
keys it doesn't know, so more can be added later.

| `type` | When | Keys |
|---|---|---|
| `model` | a model call returned, before its tools run | `thinking` (str, `""` if none), `calls` (list of `{"id", "name", "args"}`, may be empty) |
| `tool` | one tool call finished | `id`, `name`, `ok` (bool), `result` (str, what the model is told) |
| `note` | the agent did something on its own that explains what comes next | `text` (str, one plain sentence) |

There is deliberately no "tool started" event: `model.calls` already announces each call
before it runs, and the cog shows it as pending until the matching `tool` arrives.

### Rules

1. **`on_step` is a plain function, not a coroutine, and returns at once.** The agent calls
   it on the event loop and never waits for Discord: the cog buffers and posts on its own
   task, so a rate limit can't slow an answer or eat into the cog's 180 s timeout.
2. **It can't break an answer.** Wrap every call in `try/except Exception`, log it, carry on.
3. **`on_step=None` behaves exactly as today.** In both cases nothing sent to the model may
   change (the prompt cache depends on byte-identical prompts).
4. Events arrive in the order things happen, all before `chat()` returns or raises. After a
   model-server error or a cancel, the events already sent stand; send nothing more.
5. Send `result` and `args` whole, except for a safety cap of 4000 characters on `result`.
   The cog clips for display. Never put a token or a request header in any event.
6. The fast path and learned phrasings use no model: send nothing for them. The reply's
   action footer already shows what ran.

### Notes to send

One `note` each time `_chat`'s loop takes a branch other than `break`:

| Branch | Suggested `text` |
|---|---|
| handoff (`turn.switch_to`) | `Switched to code mode.` |
| only unparseable tool calls | `A tool call had unreadable arguments, so it was asked to fix it.` |
| tool call written as text | `A tool call was written as text, so nothing ran; asked again.` |
| timer claim with no timer tool | `It claimed a timer change without using a timer tool; sent back.` |
| device claim with no device tool | `It claimed a device change without using a device tool; sent back.` |

## How to build it

A prototype of the middleware below ran against `tests/helpers.FakeOllama`, and end to end
with the cog against Strata (`qwen3.8-flash-next-coder-iq1_m` on the laptop, thinking on,
fake Home Assistant): three model calls, all with `reasoning_content`, with `load_tools`,
`status` and `calc` reported in order. It is a few dozen lines. (The prototype carried the
callback in a global; build it as in step 1.)

1. `Turn` gains `on_step: Callable[[dict], None] | None = None`; `chat` → `_chat` fills it.
   Agents are compiled once per `(mode, think)` and cached, so **the callback must travel in
   the per-message context**, never be bound into the middleware instance.
2. One helper that does rules 2 and 5:

   ```python
   @staticmethod
   def _emit(turn: Turn, kind: str, **data) -> None:
       if turn.on_step is None:
           return
       try:
           turn.on_step({"type": kind, **data})
       except Exception:
           log.exception("on_step failed")
   ```

3. A `_Steps(AgentMiddleware)` reading `request.runtime.context`:
   - `awrap_model_call`: `await handler(request)`, then for the `AIMessage` in
     `response.result` send `model`: thinking from
     `additional_kwargs.get("reasoning_content")`, calls from `msg.tool_calls`. Return the
     response untouched.
   - `awrap_tool_call`: `await handler(request)`, send `tool` with
     `request.tool_call["id"]` and `["name"]`, return the result untouched. If the handler
     raises, let it raise.
4. Put it **first** in `_agent`'s middleware list (outermost). Add it for every mode,
   including code mode (which has no `_ToolLoading`).

## What the prototype showed (and the traps)

| Case | What the middleware saw |
|---|---|
| normal call | `model` (with reasoning and the call), tool start, `ToolMessage` status `success`, next `model` |
| arguments fail validation | the tool wrapper runs; status `error`, content says which field |
| tool name that doesn't exist | the tool wrapper runs; status `error` |
| tool returns `"error: ..."` | status **`success`**: the text is the only sign |
| `switch_mode` | seen as an ordinary tool call; the rerun's events follow |
| call refused by `ToolCallLimitMiddleware` | **the tool wrapper never runs**: the call is in `model.calls` and no `tool` event follows |

So `ok = status != "error" and not content.startswith(("error", "The user declined"))`.
Don't invent a `tool` event for refused calls; the cog marks a call with no result as
"not run". `awrap_tool_call` can also get a `Command` instead of a `ToolMessage`; treat
that as `ok` with an empty result.

When testing, mind the router: a message with a coding word in it ("loop") goes to code
mode, which has no tools.

## Tests to add

With `tests/helpers` (`FakeOllama` replies already take a `"thinking"` key):

- thinking + one tool: `model` (thinking, one call), `tool` (same `id`, `ok`), `model`;
- `ok` is False for a validation error, an unknown tool and an `error:` result;
- over the tool limit: a call in `model.calls` with no `tool` event;
- handoff and one claim check each send their `note`, in order;
- an `on_step` that raises changes nothing about the reply;
- the requests recorded by `FakeOllama` are identical with and without `on_step`;
- a fast-path answer sends nothing.

Please also add a short `on_step` section to `COG_GUIDE.md`.

## Not in this change

Token-by-token streaming. LangChat's Discord bot doesn't do it either: reasoning arrives
when each model call finishes, so with thinking on there is still a wait per call.

## The cog side

Built on Astrabot branch `feature/ha-cog-steps` (`cogs/homeassistant.py`, `StepFeed`):
LangChat's message per step, formatted from these events. The cog keeps each call's
`args` from `model.calls` to head its result card, marks a call with no `tool` event
"not run", and times "Still working" itself (nothing needed from the agent). Until
`chat` has `on_step`, only "Still working" is new. After the agent change is on the
agent's checked-out `master`, `/ha reload` picks it up.
