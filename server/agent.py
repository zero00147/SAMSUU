"""The tool-calling agent loop, async and frontend-agnostic.

`server/cli.py` grew this loop first, synchronously, with `print()` calls interleaved
through it. The Telegram bot needs the same loop but must not block the event loop that
uvicorn is serving the web UI on, and it renders progress by editing a Telegram message
rather than writing to a terminal.

So the loop lives here and emits events instead of printing. Callers consume them:

    async for kind, payload in run_agent(...):
        ...

    kind        payload
    ----------- -----------------------------------------------------------
    'thinking'  reasoning text (only when thinking is enabled)
    'tool'      {'name', 'args'} — about to run, before any confirmation
    'result'    {'name', 'ok', 'summary'} — what the tool did
    'warn'      human-readable string; the loop is recovering, not failing
    'text'      the model's final prose answer
    'done'      {'rounds', 'timings'}

The confinement guarantees are unchanged: tool schemas are only sent when a Workspace
exists, and every path still goes through `Workspace.resolve`.
"""

import asyncio
import json
from typing import AsyncIterator, Callable, Optional

import httpx

from .config import CONFIG, LLAMA_URL
from .workspace import DESTRUCTIVE, TOOL_SCHEMAS, Workspace, WorkspaceError

REQUEST_TIMEOUT = 600.0

MAX_TOOL_ROUNDS = int(CONFIG.get("max_tool_rounds", 40))
AGENT_MAX_TOKENS = int(CONFIG.get("cli_max_tokens", CONFIG["max_tokens"]))

SYSTEM_TOOLS = """You are samsu, a coding assistant working inside a single directory.

Workspace root: {root}

Rules:
- All paths are RELATIVE to the workspace root. Never use absolute paths or '..'.
- Use the tools to inspect and change files. Do not describe what you would do — do it.
- Read a file before editing it.
- Keep every write_file call SHORT — at most about 80 lines. A long file must be built
  up across several calls: write a small first version, then extend it with edit_file.
  One oversized call gets truncated and fails.
- Work on ONE file at a time.
- After finishing, reply with a short plain-text summary of what you changed.
"""

TRUNCATION_HINT = (
    "Your previous tool call was cut off because it was too long, so nothing was "
    "written. Retry with a much smaller write_file call — at most 40 lines. "
    "Build the file up over several calls instead of one."
)

# Derived from the schemas so the two cannot drift apart.
SIGNATURES = {
    t["function"]["name"]: t["function"]["parameters"].get("required", [])
    for t in TOOL_SCHEMAS
}

# Serialises agent generations *within this process* — two Telegram users, or a bot turn
# and a CLI turn, take it in turns rather than interleaving tool rounds against a single
# llama-server slot. It deliberately does not cover the web UI's own streaming path in
# llm.py: making the browser block behind a multi-minute build from the phone would be
# worse than letting llama-server queue the two requests itself, which it already does.
GENERATION_LOCK = asyncio.Lock()


class Truncated(Exception):
    """Generation ran out of tokens mid tool-call, so the arguments are unparseable."""


async def count_tokens(client: httpx.AsyncClient, text: str) -> int:
    try:
        r = await client.post(f"{LLAMA_URL}/tokenize", json={"content": text}, timeout=15.0)
        return len(r.json().get("tokens", []))
    except Exception:
        return len(text) // 3 + 1


async def trim(client: httpx.AsyncClient, messages: list[dict]) -> list[dict]:
    """Drop oldest turns to fit the window, never orphaning a tool result.

    A `tool` message is only valid immediately after the `assistant` message whose
    tool_calls it answers, so cuts are only made at user-message boundaries.
    """
    budget = max(512, CONFIG["n_ctx"] - AGENT_MAX_TOKENS - 512)
    system, rest = messages[0], messages[1:]
    total = await count_tokens(client, json.dumps(messages))
    if total <= budget:
        return messages

    while rest and total > budget:
        cut = 1
        while cut < len(rest) and rest[cut].get("role") != "user":
            cut += 1
        dropped, rest = rest[:cut], rest[cut:]
        total -= await count_tokens(client, json.dumps(dropped))

    return [system] + rest


async def complete(
    client: httpx.AsyncClient, messages: list[dict], tools, thinking: bool
) -> tuple[dict, dict]:
    payload = {
        "messages": messages,
        "stream": False,
        "temperature": CONFIG["temperature"],
        "top_p": CONFIG["top_p"],
        "max_tokens": AGENT_MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": thinking},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    async with GENERATION_LOCK:
        resp = await client.post(
            f"{LLAMA_URL}/v1/chat/completions", json=payload, timeout=REQUEST_TIMEOUT
        )

    if resp.status_code == 500 and "tool call arguments" in resp.text:
        raise Truncated()
    resp.raise_for_status()

    data = resp.json()
    choice = data["choices"][0]
    # Cut off mid tool-call, but llama-server happened to parse it anyway — the
    # arguments are still incomplete, so treat it the same way.
    if choice.get("finish_reason") == "length" and choice["message"].get("tool_calls"):
        raise Truncated()
    return choice["message"], data.get("timings", {})


async def _execute(
    ws: Workspace,
    call: dict,
    confirm: Optional[Callable],
) -> tuple[dict, dict]:
    """Run one tool call. Returns (tool_message, result_event)."""
    fn = call.get("function", {})
    name = fn.get("name", "")
    raw = fn.get("arguments") or "{}"

    try:
        args = json.loads(raw)
        bad_args = not isinstance(args, dict)
    except json.JSONDecodeError:
        args, bad_args = {}, True

    def reply(content, ok, summary):
        return (
            {"role": "tool", "tool_call_id": call.get("id", ""), "name": name, "content": content},
            {"name": name, "ok": ok, "summary": summary},
        )

    if bad_args:
        # Almost always a truncated argument string. Say so, so the model retries
        # smaller instead of seeing a confusing Python TypeError.
        return reply(f"Error: {TRUNCATION_HINT}", False, "arguments were cut off")

    if name in DESTRUCTIVE and confirm is not None:
        allowed = await confirm(name, args, ws.describe(name, args))
        if not allowed:
            return reply("User declined this operation.", False, "declined")

    try:
        # Filesystem calls are blocking; keep them off the event loop so the web UI
        # stays responsive while the bot is mid-build.
        result = await asyncio.to_thread(ws.run, name, args)
        first = result.splitlines()[0] if result else ""
        return reply(result, True, first[:120])
    except WorkspaceError as e:
        return reply(f"Error: {e}", False, str(e)[:120])
    except TypeError:
        expected = ", ".join(SIGNATURES.get(name, []))
        msg = (
            f"Error: {name} needs these arguments: {expected}. "
            f"You supplied: {sorted(args) or 'none'}. Call it again with all of them."
        )
        return reply(msg, False, f"missing arguments (needs {expected})")


async def run_agent(
    client: httpx.AsyncClient,
    history: list[dict],
    workspace: Optional[Workspace],
    *,
    thinking: bool = False,
    confirm: Optional[Callable] = None,
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> AsyncIterator[tuple[str, object]]:
    """Drive one turn to completion, appending to `history` in place.

    `history` is mutated so the caller keeps the full conversation, including tool
    messages, for the next turn. With `workspace` None no tool schemas are sent at all,
    which is the same no-filesystem-surface guarantee the CLI gives in chat-only mode.
    """
    system = (
        SYSTEM_TOOLS.format(root=workspace.root) if workspace else CONFIG["system_prompt"]
    )
    tools = TOOL_SCHEMAS if workspace else None

    truncations = 0
    rounds = 0
    timings: dict = {}

    while rounds < max_rounds:
        rounds += 1
        messages = await trim(client, [{"role": "system", "content": system}] + history)

        try:
            msg, timings = await complete(client, messages, tools, thinking)
        except Truncated:
            truncations += 1
            if truncations > 3:
                yield "warn", (
                    "Gave up: the model kept trying to write more than it can emit in "
                    "one call. Ask for one small file at a time."
                )
                break
            yield "warn", f"output truncated mid-write — retrying smaller ({truncations}/3)"
            history.append({"role": "user", "content": TRUNCATION_HINT})
            continue

        calls = msg.get("tool_calls") or []
        reasoning = msg.get("reasoning_content")
        if reasoning and thinking:
            yield "thinking", reasoning

        history.append({
            "role": "assistant",
            "content": msg.get("content") or "",
            **({"tool_calls": calls} if calls else {}),
        })

        if not calls:
            yield "text", (msg.get("content") or "").strip()
            break

        for call in calls:
            args_preview = call.get("function", {}).get("arguments", "")
            yield "tool", {"name": call.get("function", {}).get("name", "?"), "args": args_preview}

            tool_msg, event = await _execute(workspace, call, confirm)
            history.append(tool_msg)
            yield "result", event
    else:
        yield "warn", f"Stopped after {max_rounds} tool rounds."

    yield "done", {"rounds": rounds, "timings": timings}
