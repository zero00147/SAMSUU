"""Streaming client for llama-server's OpenAI-compatible endpoint.

llama-server runs with `--jinja --reasoning-format deepseek`, so Qwen3's <think> blocks
arrive as a separate `reasoning_content` delta field rather than inline tags. That is why
nothing here parses <think> by hand.
"""

import json
from typing import AsyncIterator

import httpx

from .config import CONFIG, LLAMA_URL


def build_payload(messages: list[dict], thinking: bool) -> dict:
    """Turn stored rows into an OpenAI chat request, prepending the system prompt.

    `enable_thinking` is a Qwen3 chat-template switch. Left on, the model spends
    hundreds of tokens reasoning before every answer — at ~16 tok/s on an M3 that
    turns a one-second reply into half a minute. Off by default; the UI toggles it
    per request for questions that actually warrant it.
    """
    wire = [{"role": "system", "content": CONFIG["system_prompt"]}]
    for m in messages:
        wire.append({"role": m["role"], "content": m["content"]})
    return {
        "messages": wire,
        "stream": True,
        "temperature": CONFIG["temperature"],
        "top_p": CONFIG["top_p"],
        "top_k": CONFIG["top_k"],
        "max_tokens": CONFIG["max_tokens"],
        "chat_template_kwargs": {"enable_thinking": thinking},
    }


async def stream_completion(
    messages: list[dict], thinking: bool = False
) -> AsyncIterator[tuple[str, str]]:
    """Yield (kind, delta) pairs where kind is 'thinking' or 'content'.

    Closing this generator (which happens when the browser aborts) drops the upstream
    HTTP connection, and llama-server abandons the generation instead of continuing to
    burn GPU on tokens nobody will read.
    """
    payload = build_payload(messages, thinking)
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
