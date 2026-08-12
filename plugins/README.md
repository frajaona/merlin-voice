# Merlin tool plugins

Every `.py` file in this directory (not starting with `_`) is auto-discovered at
startup and becomes a tool Merlin can call. **This is the only place new
capabilities should be added** — never wire tools into `bot.py` directly.

## Contract

Each plugin module MUST export:

- `SCHEMA`: a `pipecat.adapters.schemas.function_schema.FunctionSchema`.
  The `name` must be unique across plugins and match the filename stem.
  Descriptions are written in French — the model converses in French.
- `handler`: an `async def handler(params: FunctionCallParams)` coroutine.

## Handler rules

1. Call `await params.result_callback(<JSON-serializable dict>)` **exactly once**,
   including on failure (`{"error": "..."}`) — never raise out of the handler.
2. Never block the event loop: wrap synchronous I/O or CPU work in
   `await asyncio.to_thread(...)`. A blocked loop degrades live audio.
3. If the tool takes more than ~1 second, push a short spoken filler first:
   `await params.llm.push_frame(TTSSpeakFrame("Je regarde ça."))`.
4. Results are read aloud by an LLM: return compact, useful fields, not dumps.
5. Log with `from loguru import logger` — one INFO line per invocation.

## Testing

`tools/probe_tool_call.py` runs a full voice round-trip against a running bot.
Adapt the spoken question to exercise a new tool before shipping it.
