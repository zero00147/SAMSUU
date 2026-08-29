# samsu

An offline, Claude-like chat app running Qwen3-4B locally. White UI, no cloud, no accounts.
Everything — model, conversations, assets — stays on this machine.

## Run it

```bash
./samsu            # CLI  — asks for a working directory on launch
./samsu web        # web UI at http://127.0.0.1:8000  (+ Telegram bot, if configured)
./samsu serve      # model server only, in the background
./samsu game       # Void Runner at http://127.0.0.1:8100
./samsu status     # what's running
./samsu stop       # stop everything
```

`./samsu` and `./samsu web` start the model server automatically if it isn't already up.

### Stopping

| What | How |
|---|---|
| Leave the CLI | `/exit`, `/quit`, or Ctrl-D |
| Abort a running turn | Ctrl-C (stays in the CLI) |
| Stop the web UI | Ctrl-C in its terminal |
| Stop the model server | `./samsu stop` |

Leaving the CLI does **not** stop the model server — that's deliberate, so the next launch
is instant instead of reloading 2.3 GB. Use `./samsu stop` to free the memory.

## CLI with file access

On launch it asks for a working directory:

```
  Give samsu a directory to work in, or press Enter to skip.
  With a directory it can create, edit and delete files inside it —
  and nowhere else. Without one it is chat-only.

  directory (Enter to skip) › ~/Desktop/myproject
```

**Give it a directory** and the model gets file tools: `list_dir`, `read_file`,
`write_file`, `edit_file`, `make_dir`, `delete_path`, `move_path`.

**Press Enter** and no file tools are exposed at all — it behaves exactly like the web chat.

### Confinement

Every path is resolved and checked against the workspace root before anything runs.
Rejected: absolute paths, `..` traversal, and symlinks pointing outside the tree
(`Path.resolve()` follows symlinks, so those are caught too). The workspace root itself
cannot be deleted or moved.

### Automatic by default

samsu **creates, edits, overwrites and deletes inside the workspace without asking.**
That is the default (`"auto_approve": true` in `config.json`), and the banner says so on
every launch:

```
  AUTOMATIC — creates, edits and deletes here without asking
```

Confinement is unaffected — it still cannot touch anything outside the directory you gave
it. What is gone is the y/N prompt *within* that directory.

There is no undo and deletes are not recoverable, so it is worth running `git init` in the
workspace before a long session — `git diff` then shows you everything it changed.

To confirm before destructive operations, either run `./samsu --ask`, type `/ask` mid-session,
or set `"auto_approve": false` in `config.json`.

Tool calls are capped at `max_tool_rounds` (default **40**) per turn, so a confused model
stops rather than looping forever. Raise it in `config.json` for longer autonomous runs.

### CLI options and commands

```bash
./samsu -d ~/code/thing     # skip the prompt, use this directory
./samsu --no-dir            # skip the prompt, chat only
./samsu --ask               # confirm before overwrite/edit/delete/move
./samsu --think             # start with extended reasoning on
```

| Command | Does |
|---|---|
| `/help` | list commands |
| `/dir <path>` · `/dir off` | change or drop the workspace |
| `/pwd` · `/ls [path]` | where am I · list files directly |
| `/think` | toggle extended reasoning |
| `/auto` · `/ask` | run without confirmations (default) · confirm first |
| `/clear` | forget the conversation |
| `/exit` `/quit` | leave |

### What to expect from a 4B model

It handles single-file work well — read a file, spot a bug, apply an edit, write a small
new file. Verified: it found a bug in `add()`, fixed it, and wrote a passing test.

It is much weaker at multi-file changes and long chains of tool calls. Tool rounds are
capped at `max_tool_rounds` (default **40**) per turn so a confused model can't loop
forever. Review what it writes.

## What's running

```
Browser (localhost:8000)
  │  fetch + SSE, AbortController for Stop
  ▼
FastAPI :8000       serves the frontend, owns SQLite, proxies streaming
  │  httpx stream → /v1/chat/completions
  ▼
llama-server :8080  OpenAI-compatible, Metal GPU, reads models/*.gguf
```

Both bind to `127.0.0.1` only — nothing is exposed to the network.

## Telegram bot

The bot runs **inside the web process** — `./samsu web` serves the browser and the phone
together, sharing one SQLite file, one httpx pool and one llama-server.

### Setup

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token
2. Put it in `.env` (gitignored) or the environment:
   ```bash
   echo 'TELEGRAM_BOT_TOKEN=123456:AA...' >> .env
   ```
3. `./samsu web` — with no owner yet, it prints a one-time pairing code:
   ```
   ┌─ Telegram pairing ────────────────────────────────┐
   │  Send this to @your_bot                           │
   │      /pair QK4M-7TXB                              │
   │  Valid 60 minutes, single use, grants owner.      │
   └───────────────────────────────────────────────────┘
   ```
4. Send that to your bot. You are now the owner.

With no token set, the bot is simply skipped and the web UI starts as before.

### Why a pairing code

Telegram delivers messages from **anyone** who finds your bot, and the bot can drive file
tools. Proving you can read the terminal running samsu is what proves you are allowed in —
nothing secret is stored in `config.json`, and there is no password to leak. Codes are
single-use, expire, and are compared with `secrets.compare_digest`.

### Roles

| Role | Can |
|---|---|
| `owner` | Everything: file tools, `/dir`, invite and block others |
| `user` | Conversation only — **tool schemas are never sent** for these accounts |
| `blocked` | Nothing; receives no reply at all |
| `pending` | Contacted the bot but has not paired |

File tools require role `owner` **and** a bound workspace. Either one alone gets nothing.

### Commands

| Command | Does |
|---|---|
| `/pair CODE` | Redeem a pairing code |
| `/new` | Fresh conversation |
| `/think` | Toggle extended reasoning |
| `/status` | Model, workspace, role, history size |
| `/dir <path>` · `/dir off` | Set or drop the workspace (owner) |
| `/pwd` · `/ls [path]` | Where it's working · list files directly (owner) |
| `/invite` | Mint a 15-minute conversation-only code (owner) |
| `/who` · `/block <id>` · `/unblock <id>` | Manage accounts (owner) |
| `/audit` | Recent access events (owner) |

Relative paths in `/dir` resolve against the samsu directory, so `/dir apps/asteroids`
works from a phone without typing a full path.

### Working from both at once

Conversations held over Telegram are mirrored into a normal samsu chat (titled `📱 …`), so
you can watch from the browser sidebar while driving from your phone. Measured: two agent
turns running concurrently while the web UI served 31 requests, slowest 44 ms.

Agent generations are serialised in-process by `agent.GENERATION_LOCK` so tool rounds don't
interleave against a single llama-server slot. The web UI's own streaming path deliberately
does **not** take that lock — llama-server queues it instead, so the browser never blocks
behind a multi-minute build from the phone.

## Void Runner (apps/asteroids)

A browser 3D asteroid shooter, built as the first application developed *through* samsu.

```bash
./samsu game     # http://127.0.0.1:8100
```

Three sectors of rising difficulty, five ships with different hull/speed/handling and three
weapon types, and every sound synthesised at runtime with the Web Audio API — no `.wav`
files, so it works with the network unplugged. Three.js is vendored in `apps/asteroids/vendor/`
and loaded through a native import map, matching how `web/vendor/` already works: no Node,
no build step.

Add `?debug` to the URL for a beacon that reports boot, errors and live engine state through
the static server's access log; `?debug&autoplay&level=3` flies itself. That harness exists
because this machine has no Node and no headless browser, so the access log is the only
channel back out of the page.

## Features

- Streaming replies with markdown and copy-able code blocks
- Chat history sidebar backed by SQLite — rename, delete, resume across restarts
- **Stop** mid-generation; the partial reply is saved, not discarded
- **Retry** an answer, **Edit** any earlier message and re-run from that point
- **Think** toggle — Qwen3's extended reasoning, shown as a collapsible block

## Prerequisites

Already installed by setup, listed here for rebuilding elsewhere:

| Component | Install |
|-----------|---------|
| Homebrew | <https://brew.sh> |
| llama.cpp | `brew install llama.cpp` |
| Python 3.13 | `brew install python@3.13` |
| venv + deps | `/opt/homebrew/opt/python@3.13/bin/python3.13 -m venv .venv && ./.venv/bin/pip install -r requirements.txt` |
| Model (2.5 GB) | `./.venv/bin/huggingface-cli download Qwen/Qwen3-4B-GGUF Qwen3-4B-Q4_K_M.gguf --local-dir ./models` |

No Node, no Docker, no build step. After the model download, it runs with networking off.

## Configuration

Everything tunable lives in `config.json`; restart to apply.

| Key | Default | Notes |
|-----|---------|-------|
| `n_ctx` | `8192` | Context window. **Raising this costs ~144 KB/token of RAM** — see `memory.md` |
| `temperature` | `0.7` | Qwen3's recommended value for non-thinking mode |
| `max_tokens` | `2048` | Per-reply cap |
| `enable_thinking` | `false` | Default for the Think toggle |
| `auto_approve` | `true` | CLI runs file operations without confirming |
| `max_tool_rounds` | `40` | Tool calls allowed per turn before it stops |
| `system_prompt` | … | Also instructs the model to avoid LaTeX |

## Performance on this machine

Apple M3, 8 GB. Measured ~16–17 tok/s generation, ~22 tok/s prompt processing.

8 GB is tight. If generation feels slow, close memory-heavy apps first — VMs, Docker,
and Electron apps compete directly for the same unified memory the model needs. Dropping
`n_ctx` to `4096` is the next lever.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Sidebar shows "model server offline" | `llama-server` didn't start — check `llama-server.log` |
| `-fa on` rejected | Older llama.cpp; use bare `-fa` in `start.sh` |
| Very slow replies | Turn the Think toggle off; check for other apps eating RAM |
| Port in use | Change `llama_port` / `app_port` in `config.json` |

## Project docs

- `memory.md` — decisions and why, RAM budget, gotchas. Read first when resuming.
- `project_index.md` — map of every file and its key symbols.
- `progress.md` — timestamped milestone log.
