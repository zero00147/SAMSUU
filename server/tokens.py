"""Token counting via llama-server's /tokenize endpoint.

Counting is exact rather than estimated because the whole point is to stay under a
hard limit — a heuristic that is 15% optimistic still bricks the chat. Results are
cached by content hash so repeated turns don't re-tokenise the same history.
"""

import hashlib
from typing import Iterable

import httpx

from .config import CONFIG, LLAMA_URL

# content hash -> token count. Bounded so a long session can't grow it without limit.
_CACHE: dict[str, int] = {}
_CACHE_MAX = 4096

# Rough fallback if llama-server is unreachable. Deliberately pessimistic (assumes
# short tokens) so the estimate over-counts and we trim more rather than less.
_FALLBACK_CHARS_PER_TOKEN = 3.0


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


async def count(text: str, client: httpx.AsyncClient | None = None) -> int:
    if not text:
        return 0
    k = _key(text)
    if k in _CACHE:
        return _CACHE[k]

    try:
        owns = client is None
        c = client or httpx.AsyncClient(timeout=10.0)
        try:
            resp = await c.post(f"{LLAMA_URL}/tokenize", json={"content": text})
            resp.raise_for_status()
            n = len(resp.json().get("tokens", []))
        finally:
            if owns:
                await c.aclose()
    except Exception:
        n = int(len(text) / _FALLBACK_CHARS_PER_TOKEN) + 1

    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[k] = n
    return n


async def count_many(texts: Iterable[str]) -> list[int]:
    """Count several strings over one connection."""
    texts = list(texts)
    async with httpx.AsyncClient(timeout=15.0) as client:
        return [await count(t, client) for t in texts]


def prompt_budget() -> int:
    """Tokens available for the prompt, after reserving room for the reply.

    `max_tokens` is what the model may generate, and it shares the same window as the
    prompt. The extra margin covers chat-template scaffolding (role markers, BOS/EOS)
    that /tokenize on raw content does not account for.
    """
    return max(512, CONFIG["n_ctx"] - CONFIG["max_tokens"] - 256)
