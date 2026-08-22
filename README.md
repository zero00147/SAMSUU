# samsu

An offline, Claude-like chat app running Qwen3-4B locally. White UI, no cloud, no accounts.
Everything — model, conversations, assets — stays on this machine.

## Run it

```bash
./start.sh
```

Then open <http://127.0.0.1:8000>. Ctrl-C stops both processes.

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
