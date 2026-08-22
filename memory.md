# Project Memory — Offline Claude-like Chat (samsu)

Durable context for this project: decisions, the reasoning behind them, and gotchas.
Read this first when resuming after a break.

---

## What this is

A fully offline web chat app, styled after claude.ai in a white/light theme, running a
local Qwen3-4B model. No cloud, no accounts. Runs entirely on localhost.

## Hardware constraint (drives every other decision)

Apple M3, **8 GB unified memory**, 8 cores, macOS 26.3.1, ~129 GB free disk.

8 GB is small for local LLM work. macOS plus a browser consume ~3.5–4 GB, leaving
roughly **4–4.5 GB** for inference. Exceeding that causes swapping, which slows
generation by an order of magnitude. Every sizing choice below exists to stay under it.

---

## Key decisions and why

### GGUF, not the repo that was originally linked
`model_link.txt` originally pointed at `Qwen/Qwen3-4B` — that repo is **BF16 safetensors,
~8 GB**, which cannot fit. The project uses the official quantized build instead:
`Qwen/Qwen3-4B-GGUF` → `Qwen3-4B-Q4_K_M.gguf`, **2.5 GB**. Same model, 4-bit quantized.

### llama.cpp (`llama-server`), not Ollama
Ollama copies models into its own blob store. The requirement was to keep the model file
on disk and use it in place, which `llama-server -m <path>` does directly. It also gives
an OpenAI-compatible streaming API and Metal GPU acceleration on the M3 for free.

### Context = 8192, with a q8_0 quantized KV cache
This is the non-obvious memory trap. Qwen3-4B has **36 layers and 8 KV heads**, so the
KV cache costs **~144 KB per token** at f16 — the cache can dwarf the weights.

| Context | KV @ f16 | KV @ q8_0 | Total w/ 2.5 GB weights (q8_0) |
|---------|----------|-----------|--------------------------------|
| 4096    | 0.58 GB  | 0.29 GB   | ~2.8 GB                        |
| **8192**| 1.13 GB  | **0.58 GB** | **~3.1 GB ← chosen default** |
| 16384   | 2.25 GB  | 1.13 GB   | ~3.7 GB                        |
| 32768   | 4.60 GB  | 2.30 GB   | ~4.8 GB — swaps, avoid         |

The model natively supports 32K context, but **we deliberately do not use it.** 8192 keeps
total footprint near 3.1 GB. Raise `n_ctx` in `config.json` only if you accept the cost.

### Quantized KV cache requires flash attention
`--cache-type-v q8_0` does not work without `-fa`. If the V cache is quantized and flash
attention is off, llama.cpp will error or silently fall back.

### Thinking blocks handled server-side, not by string parsing
Qwen3 is a **hybrid reasoning model** — it emits `<think>…</think>`. Rather than regexing
those tags, `llama-server` runs with `--jinja --reasoning-format deepseek`, which splits
the reasoning into a separate `reasoning_content` field in the OpenAI response. The UI
renders it as a collapsible "thinking" section, mirroring Claude's extended thinking.

### FastAPI + vanilla JS, no Node
No Node/npm was installed and a build toolchain is not worth it for a local app. The
frontend is plain HTML/CSS/JS with `marked` and `DOMPurify` vendored as local files so the
app works with networking fully off. **Never reference a CDN** — that silently breaks offline.

### Stop is implemented via upstream cancellation, not UI hiding
The streaming generator accumulates output and persists it in a `finally` block. When the
browser aborts, FastAPI closes the generator, the partial text is saved with `stopped=1`,
and dropping the httpx stream makes `llama-server` abandon generation instead of burning
GPU on tokens nobody reads. Do **not** implement Stop by POSTing received text back — that
loses data on a mid-flight abort.

### `seq` column makes edit and regenerate trivial
Both operations are the same primitive: delete all messages with `seq >= N`, then re-run.

### Auto-title uses the first user message, not a second LLM call
A title-generation call would force a full prompt reprocess. Too expensive on 8 GB.

### Thinking is OFF by default — this was measured, not assumed
Qwen3's thinking mode is on by default in its chat template, and it is *verbose*. Measured
on this machine:

| Prompt | Thinking | Result |
|--------|----------|--------|
| "What is 17 × 23?" | on | burned all 400 max_tokens reasoning, returned **empty content** |
| same | off | `17 * 23 = 391` in **0.9 s** |

At ~16 tok/s that is the difference between a usable and an unusable app. It is switched
via `chat_template_kwargs: {"enable_thinking": bool}` on the request — `config.json` sets
the default and the composer's **Think** button overrides it per message (persisted in
`localStorage`). Keep it off for chat; turn it on for math and multi-step reasoning.

### LaTeX is stripped client-side, not rendered
Qwen3 reaches for LaTeX on anything numeric (`$60 \text{ mph} \times 1$`), which renders as
raw noise without a math engine. KaTeX would mean megabytes of vendored fonts, against the
lean/offline goal. Two-layer fix instead:
1. The system prompt explicitly forbids LaTeX and asks for Unicode math.
2. `stripLatex()` in `web/js/render.js` normalises whatever slips through
   (`\times`→×, `\frac{a}{b}`→(a)/(b), `\text{x}`→x, `\sqrt{}`→√()).

**Two traps that fix guards against, both verified by test:** `$5 and $10` must not be
eaten as a math span (a digit alone is not enough — a LaTeX control char or a pure-math
body is required), and code spans/fences must be excluded (`outsideCode()`), or shell
snippets like `echo $PATH` get mangled. If real math rendering is ever needed, KaTeX is the
upgrade path.

---

## Architecture

```
Browser (localhost:8000)
  │  fetch + SSE, AbortController for Stop
  ▼
FastAPI :8000    — serves frontend, owns SQLite, proxies streaming
  │  httpx stream → /v1/chat/completions
  ▼
llama-server :8080 — OpenAI-compatible, Metal, reads models/*.gguf
```

Both bind to 127.0.0.1 only. Nothing is exposed to the network.

---

## Gotchas hit

- System Python is **3.9.6** (deprecated Apple build). Use the Homebrew `python@3.13`
  virtualenv at `.venv/` instead.
- The `hf` CLI does **not** exist in `huggingface_hub` 0.27 (it arrives in 0.34). The
  command here is `./.venv/bin/huggingface-cli`.
- **Do not run two `brew install`s concurrently.** They deadlock on a shared dependency
  (`Error: … has already locked /opt/homebrew/Cellar/ca-certificates`) and, because the
  command was piped to `tail`, the failure still exited 0 and looked like success.
- `ps -o rss` and `ps aux` report **0 RSS for every process** on this macOS build. Use
  `top -l 1 -stats mem,command -o mem` to read real memory usage.

## Measured baseline (2026-08-23)

- Generation **~16–17 tok/s**, prompt processing **~22 tok/s**, Q4_K_M at 8192 ctx.
- `llama-server` resident ~786 MB (the rest of the model is mmap'd / on the Metal heap).
- Measured while the machine was already heavily oversubscribed — a **14 GB
  qemu-system-aarch64 VM** plus a 3.8 GB `studio` process were running, with 10.8 GB of
  swap in use and 11% memory free. Numbers on an otherwise-idle machine should be better.
  **If generation feels slow, check for a running VM before blaming the model.**

---

## Open questions / deferred

- Settings panel (temperature, system prompt) deferred out of v1 — edit `config.json` and
  restart instead. `/api/health` already reports `n_ctx` and `enable_thinking`, so a panel
  has something to bind to.
- Syntax highlighting in code blocks deferred; basic markdown + copy button only.
- Math rendering (KaTeX) deferred — see the LaTeX note above.
- No attachments/file upload, no search across chats, no export.
- If generation feels slow: close VMs first, then turn Think off, then drop `n_ctx` to 4096.
