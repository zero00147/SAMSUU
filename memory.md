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

## FIXED 2026-08-27: context overflow

**Fixed in `server/llm.py:build_payload`.** History is now trimmed to fit before every
request: newest turns are kept, oldest dropped, using exact token counts from
llama-server's `/tokenize` (see `server/tokens.py`). Verified with a 24,191-token history
against an 8,192 window — trimmed to 5,602 tokens and completed, where it previously
failed permanently.

Budget is `n_ctx − max_tokens − 256`, i.e. **5,888 tokens** at current settings. The margin
covers chat-template scaffolding that `/tokenize` on raw content doesn't count.

Edge case handled: if the newest turn *alone* exceeds the budget, its tail is kept with a
`…(truncated)…` marker rather than failing — a degraded answer beats a dead chat.

### Original diagnosis (kept for context)

`llama-server.log.crash` contains three real failures:

```
error: request (12670 tokens) exceeds the available context size (8192 tokens)
error: request (12676 tokens) exceeds the available context size (8192 tokens)
error: request ( 9376 tokens) exceeds the available context size (8192 tokens)
```

**Cause:** `build_payload` sent *every* message in the chat with no trimming, so once a
conversation passed 8192 tokens every further turn failed permanently.

Note the separate `GGML_ASSERT` crash in that same log was **not** caused by overflow — it
was a SIGINT shutdown artefact (see below).

## Documents: why sections, not RAG (2026-08-27)

The driving use case is feeding a 12,518-token PRD to a model with 5,888 tokens of usable
prompt space. **The document is 2× the window — it cannot be attached whole at any
context size this machine can afford** (32K ctx would need ~4.8 GB, and there were 69 MB
of free RAM at the time of measurement).

Chosen approach: split on headings server-side, user ticks the sections they need.
Rejected embedding-based RAG because it needs an embedding model resident in RAM that
isn't available here, and because on a numbered spec heading-selection is both
deterministic and more accurate than semantic similarity. `§7.1 Proxy Bidding` is
370 tokens — you attach that, not the PRD.

The splitter (`server/documents.py`) recognises markdown `#` headings and numbered
headings (`3.1 Item Listing Engine`). On PRD.md it recovered all 32 sections including
subsections. The numbered-heading regex requires a capitalised title after the number so
list items like `1. Media Assets: …` don't produce false sections.

**Attachments are stored in `messages.attachments` (JSON), not inlined into
`messages.content`** — so the chat bubble stays readable, edit/regenerate keeps the
reference, and `llm.expand()` re-inlines it only when sending to the model.

## Gotcha: killing uvicorn also kills llama-server

`start.sh` traps `EXIT INT TERM` and runs `kill 0`, which signals the whole process group.
So `pkill -f uvicorn` makes `start.sh` exit, which takes `llama-server` down with it. That
is correct for Ctrl-C but surprising during development. llama.cpp's Metal backend also
throws a noisy `GGML_ASSERT([rsets->data count] == 0)` backtrace on interrupt — that is a
**shutdown artefact, not a crash or OOM**. To restart just the web layer, start uvicorn
separately instead of through `start.sh`.

## CLI with file tools (2026-08-27)

`./samsu` launches a terminal REPL. It asks for a working directory; supply one and the
model gets file tools, decline and **no tools are sent to the model at all** — tool
schemas are only included in the request when a workspace exists, so chat-only mode has
no filesystem surface whatsoever.

**Qwen3-4B does native OpenAI tool calling correctly** (verified: `finish_reason:
tool_calls`, well-formed arguments). This needs `--jinja` on llama-server. No text-parsing
fallback was necessary.

### Confinement (server/workspace.py)

`Workspace.resolve()` is the single choke point. Verified blocked: `../../../etc/passwd`,
absolute `/etc/passwd`, `sub/../../../../etc/hosts`, a **symlink to /etc/passwd**, `..`,
and deleting the root. Two details that matter:

- **Absolute paths must be rejected explicitly.** `Path('/root') / '/etc/passwd'` yields
  `/etc/passwd` — pathlib lets an absolute right-hand operand replace the base entirely.
- `Path.resolve()` follows symlinks, which is what catches the symlink escape.

### Policy — automatic since 2026-08-27

`config.json: auto_approve = true` (requested explicitly). The CLI creates, edits,
overwrites and deletes inside the workspace **with no confirmation**. `--ask` / `/ask` /
`auto_approve: false` restores prompting.

**Confinement is unchanged and still enforced in automatic mode** — verified after the
change: `../../../../etc/passwd` is still rejected. Auto-approve removes the prompt, not
the sandbox.

Tool rounds capped by `config.json: max_tool_rounds` (default 40, raised from 12 because
autonomous builds need many calls).

No undo exists. `git init` in the workspace is the practical safety net.

### Gotcha: launcher argument parsing

`./samsu` dispatches on `$1`, so a bare flag like `./samsu --dir X` first looked like an
unknown subcommand. Leading `-` now routes to CLI mode. `-h/--help` is still a subcommand.

### FIXED 2026-08-27: 500 crash when writing large files

**Symptom:** `model server error: Server error '500 Internal Server Error'`, often preceded
by `Workspace.edit_file() missing 3 required positional arguments`.

**Cause:** a `write_file` call must emit the entire file inside a JSON string. With
`max_tokens: 2048` the model ran out mid-string, producing unterminated JSON:

```
Failed to parse tool call arguments as JSON: parse error at column 7311:
invalid string: missing closing quote
```

llama-server rejects the whole request with 500. The `missing arguments` error was the
same truncation reaching the CLI, where `json.loads` failed and fell back to `{}`.

**Fix, three layers:**
1. `cli_max_tokens: 3072` (config) — separate from the web UI's `max_tokens`, since only
   the CLI has to fit whole files into one response. Costs prompt budget: 4,608 at n_ctx 8192.
2. `Truncated` exception — raised on a 500 mentioning "tool call arguments", or on
   `finish_reason == "length"` with tool_calls present. The turn injects `TRUNCATION_HINT`
   and retries up to 3× instead of aborting.
3. System prompt caps `write_file` at ~80 lines and tells it to extend with `edit_file`.

**Verified:** the identical request that previously crashed now produces 6 small files
across 3 directories, zero 500s.

### Measured behaviour

Handled well: list files → read → identify a real bug (`add` doing subtraction) → apply
the edit → write a test file **that passed when executed**. Weak at multi-file work and
long tool chains.

## Phase 2 — Telegram bot (2026-08-29)

### Why the bot is the security boundary

Everything else in samsu binds `127.0.0.1` and nothing leaves the machine. The bot breaks
that by design: Telegram delivers messages from **anyone** who finds it, and the bot can
drive file tools. So authorisation is load-bearing here in a way it never was for the CLI.

**Pairing code, not a password.** `server/auth.py` mints a single-use code printed to the
terminal running samsu. Being able to read that terminal *is* the proof of authorisation —
nothing secret in `config.json`, nothing to leak in a chat log. Codes expire (60 min for
the bootstrap owner code, 15 for `/invite`) and are compared with `secrets.compare_digest`
against every unused row, with no early `break`, so neither timing nor a prefix leaks.

**Two independent gates on file tools.** `may_use_tools()` requires role `owner` **and** a
bound workspace. Verified: an owner with no workspace gets no tools; a `user` who somehow
has a workspace row still gets none. Tool schemas are only put in the request when both
hold — the same "no filesystem surface at all" property the CLI has in chat-only mode.

Verified 2026-08-29: unpaired stranger gated, owner commands refused to non-owners, codes
single-use, expired codes rejected, blocked ids get silence (no reply at all, so the id
cannot probe for existence). Sandbox re-tested through the *new* agent path: 7/7 escapes
blocked, including `sub/../../../../etc/hosts` and absolute paths.

### server/agent.py — the loop now lives in one place

`cli.py` grew the tool loop first, synchronously, with `print()` interleaved. The bot needs
the same loop but must not block uvicorn's event loop. Rather than copy it, `agent.py` holds
an async version that **emits events** (`tool`, `result`, `warn`, `text`, `done`) instead of
printing; the terminal renders them as lines, the bot renders them by editing one Telegram
message. Blocking filesystem calls go through `asyncio.to_thread`.

**`cli.py` has NOT yet been migrated onto it** — it still has its own sync copy. That is the
one piece of real debt from this phase. `agent.py` is the version to keep.

### Concurrency — what actually serialises

`agent.GENERATION_LOCK` serialises agent generations *inside this process* (two bot users,
or bot + CLI). It deliberately does **not** cover the web UI's streaming path in `llm.py`:
making the browser block behind a multi-minute build from the phone would be worse than
letting llama-server queue the two itself, which it already does.

**Trap that cost a rewrite:** the first version awaited `_handle(update)` inline in the poll
loop. A single long build then froze polling for everyone — and the per-user `busy` guard
could never fire, because no new messages were being read. Updates are now dispatched as
tasks, with strong references held in `self._tasks` (asyncio only keeps weak ones, so an
un-referenced task can be garbage collected mid-run).

Measured: two concurrent agent turns while the web UI served 31 requests, slowest 44 ms.

### Bot conversations are mirrored into normal chats

Agent history contains `tool` / `tool_calls` messages the `messages` table has no columns
for, so it stays in memory per user. The plain user/assistant text is mirrored into a real
samsu chat titled `📱 …`, so a phone-driven session is readable in the browser sidebar while
it happens. That mirroring is what makes "app and bot at the same time" visible rather than
merely true.

No bot framework — long polling with httpx, same as `llm.py` talks to llama-server. Keeps
the dependency list at seven and keeps retry/timeout behaviour visible.

### Verified live on a real phone (2026-08-30)

`@rifat_samsu_bot` → `/pair` (owner claimed from the terminal code) → `/status` correctly
showed **file tools: off** with no workspace → `/dir ~/Desktop/bot-test` flipped it to **on**
→ a plain-English bug report produced a **surgical one-line fix in 15s over 2 tool calls**
(`len(items)` → `sum(items.values())`), nothing else in the file touched. The conversation
appeared in the browser sidebar as `📱 …` while the phone was driving it.

**Setup gotcha worth remembering:** BotFather wraps the token across two lines in the chat
bubble, and the ambiguous glyphs are `O`/`0` and `I`/`l`/`1`. The first hand-entered token
was the right *length* and passed a format regex but Telegram returned `Unauthorized`.
Validate with `getMe` before restarting — a bad token just makes the bot task exit with
`stopped — getMe: Unauthorized` and no pairing code prints, which looks like a code bug.

## Application 1 — Void Runner (apps/asteroids, 2026-08-29)

Browser 3D asteroid shooter. Three sectors, five ships (differing hull/speed/handling plus
single/twin/spread weapons), Three.js r160 vendored and loaded via a **native import map** —
no Node, no build step, matching how `web/vendor/` already works.

**All sound is synthesised at runtime** with the Web Audio API — no `.wav` or `.mp3`, so the
game is text-only on disk and runs with the network unplugged.

### Verifying a browser app with no browser automation

This machine has no Node, no headless driver, `screencapture` is blocked (no Screen Recording
permission) and Chrome's "Allow JavaScript from Apple Events" is off — so there was no way to
*see* the page. Solution: `js/devlog.js`, active only with `?debug`, reports boot, uncaught
errors and live engine state by fetching URLs that do not exist. `python -m http.server` logs
the path and 404s, and **the access log is the channel back out of the page**. `?debug&autoplay`
launches a run and holds thrust+fire; `&level=3` jumps sectors to exercise the drone code.

This is the technique to reuse for any future browser app here.

Verified this way: 120 fps, canvas 3024×1566, zero errors; level 1 score 20→1370 with rocks
splitting 8→19 and hull 3→1; level 3 spawning 13 rocks + 4 drones with a drone killed (+250).

**Confirmed visually by rifat 2026-08-30**: rotating 3D ship behind the menu, all five cards
swapping the model, audio firing on first click, rocks reading as lumpy 3D geometry, and the
ship flying in the direction it points (the +Y nose convention is correct as built).

### Three.js traps hit while building this

- **`IcosahedronGeometry` is non-indexed** — the same corner appears several times in the
  vertex list. Displacing rocks by `Math.random()` per vertex pushes those copies apart and
  splits the mesh open. `lumpify()` displaces by a *function of position* instead, so every
  copy of a corner moves identically.
- **Light intensity is in candela since r155** (`useLegacyLights` now false). A `PointLight`
  at intensity 320 that looked right in older three is invisible at 46 units; it needed ~3800.
- **`scene.remove()` does not free GPU buffers.** Everything culled goes through `disposeTree`.
  Shared geometry (the one `BULLET_GEO` every shot uses) is tagged `userData.shared` and
  skipped — disposing it with the first spent bullet breaks every shot after it.

## Open questions / deferred

- Settings panel (temperature, system prompt) deferred out of v1 — edit `config.json` and
  restart instead. `/api/health` already reports `n_ctx` and `enable_thinking`, so a panel
  has something to bind to.
- Syntax highlighting in code blocks deferred; basic markdown + copy button only.
- Math rendering (KaTeX) deferred — see the LaTeX note above.
- No search across chats, no export.
- Scanned/image-only PDFs are rejected (no OCR). Legacy `.doc` unsupported — save as .docx.
- **Qwen3-4B follows attached specs imperfectly.** Given §8.4 (which states
  "Score is clamped to [0.00, 100.00]"), it produced a correct-looking
  `apply_delivery_outcome` that **omitted the clamp entirely**. The plumbing worked; the
  model dropped a stated requirement. Treat every output as a draft to review against the
  section it came from — this is the core limitation of the whole workflow, not a bug.
- If generation feels slow: close VMs first, then turn Think off, then drop `n_ctx` to 4096.
