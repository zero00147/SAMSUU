"""Streaming client for llama-server's OpenAI-compatible endpoint.

llama-server runs with `--jinja --reasoning-format deepseek`, so Qwen3's <think> blocks
arrive as a separate `reasoning_content` delta field rather than inline tags. That is why
nothing here parses <think> by hand.
"""

import json
from typing import AsyncIterator

import httpx

from . import tokens
from .config import CONFIG, LLAMA_URL


def expand(msg: dict) -> str:
    """Message text as the model should see it, with any attached document
    sections prepended. Attachments live in a separate column so the chat bubble
    stays readable, but the model needs them inline."""
    body = msg["content"]
    raw = msg.get("attachments")
    if not raw:
        return body
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return body
    if not items:
        return body
    blocks = [
        f"[Reference: {a.get('label', 'document')}]\n{a.get('content', '')}"
        for a in items
    ]
    return "\n\n".join(blocks) + "\n\n---\n\n" + body


async def build_payload(messages: list[dict], thinking: bool) -> dict:
    """Turn stored rows into an OpenAI chat request, prepending the system prompt.

    History is trimmed to fit the context window. Without this, a conversation that
    grows past `n_ctx` fails permanently — llama-server rejects the request outright
    ("exceeds the available context size") and every later turn in that chat fails
    too. Oldest turns are dropped first; the newest turn is always kept.

    `enable_thinking` is a Qwen3 chat-template switch. Left on, the model spends
    hundreds of tokens reasoning before every answer — at ~16 tok/s on an M3 that
    turns a one-second reply into half a minute. Off by default; the UI toggles it
    per request for questions that actually warrant it.
    """
    system = CONFIG["system_prompt"]
    budget = tokens.prompt_budget()

    async with httpx.AsyncClient(timeout=15.0) as client:
        used = await tokens.count(system, client)
        kept: list[dict] = []

        # Walk newest-first so the most recent context survives.
        for m in reversed(messages):
            text = expand(m)
            n = await tokens.count(text, client)
            if kept and used + n > budget:
                break
            if not kept and n > budget:
                # The newest turn alone overflows — keep its tail rather than fail,
                # so the user gets a degraded answer instead of a dead chat.
                keep_chars = int(budget * 3.0)
                text = "…(truncated)…\n" + text[-keep_chars:]
                n = await tokens.count(text, client)
            used += n
            kept.append({"role": m["role"], "content": text})

    kept.reverse()
    return {
        "messages": [{"role": "system", "content": system}] + kept,
        "stream": True,
        "temperature": CONFIG["temperature"],
        "top_p": CONFIG["top_p"],
        "top_k": CONFIG["top_k"],
        "max_tokens": CONFIG["max_tokens"],
        "chat_template_kwargs": {"enable_thinking": thinking},
    }


async def stream_completion(
    messages: list[dict], thinking: bool = False
) -> AsyncIterator[tuple[str, object]]:
    """Yield (kind, payload) pairs.

    kind is 'thinking' or 'content' (payload = text delta), or 'timings' (payload =
    llama.cpp's stats dict from the final chunk: predicted_n, predicted_per_second, …).

    Closing this generator (which happens when the browser aborts) drops the upstream
    HTTP connection, and llama-server abandons the generation instead of continuing to
    burn GPU on tokens nobody will read.
    """
    payload = await build_payload(messages, thinking)
    timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=None)

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST", f"{LLAMA_URL}/v1/chat/completions", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                # The final chunk carries timings alongside an empty delta, so this
                # must be checked before the empty-choices guard below.
                if "timings" in chunk:
                    yield ("timings", chunk["timings"])

                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}

                reasoning = delta.get("reasoning_content")
                if reasoning:
                    yield ("thinking", reasoning)

                content = delta.get("content")
                if content:
                    yield ("content", content)


async def health() -> dict:
    """Report whether llama-server is up, and which model it loaded."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{LLAMA_URL}/v1/models")
            resp.raise_for_status()
            data = resp.json()
            model = (data.get("data") or [{}])[0].get("id", "unknown")
            return {"ok": True, "model": model}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
