# Project Index

Map of every file in the project. Update whenever a file is added or its role changes.

## Root

| File | Purpose |
|------|---------|
| `README.md` | How to run it, prerequisites, config reference, troubleshooting |
| `start.sh` | Boots `llama-server` + uvicorn together, waits for model load, cleans up both on Ctrl-C |
| `config.json` | Single source of tunables: model path, ports, `n_ctx`, sampling params, system prompt |
| `requirements.txt` | fastapi, uvicorn[standard], httpx, huggingface_hub |
| `model_link.txt` | Source model URL + note on why the GGUF build is used instead |
| `memory.md` | Durable decisions, reasoning, RAM budget, gotchas — read first when resuming |
| `progress.md` | Terse timestamped milestone log, newest at top |
| `project_index.md` | This file |
| `llama-server.log` | Inference server output (created at runtime; gitignored) |

## `server/` — FastAPI backend

| File | Purpose | Key symbols |
|------|---------|-------------|
| `config.py` | Loads `config.json` once with defaults applied | `CONFIG`, `LLAMA_URL`, `DB_PATH`, `WEB_DIR`, `ROOT` |
| `db.py` | SQLite schema + all queries | `init`, `list_chats`, `create_chat`, `get_chat`, `rename_chat`, `delete_chat`, `get_messages`, `add_message`, `truncate_from`, `next_seq` |
| `llm.py` | Streaming client for llama-server's OpenAI API | `stream_completion` (yields `(kind, delta)`), `build_payload`, `health` |
| `main.py` | Routes, SSE generation, static mount | `api_send` (the streaming endpoint), `api_truncate`, `_auto_title`, `_sse` |

### Routes (`server/main.py`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Is llama-server reachable; which model; context size |
| GET | `/api/chats` | Sidebar list, newest first |
| POST | `/api/chats` | Create chat |
| GET | `/api/chats/{id}` | Chat + all messages |
| PATCH | `/api/chats/{id}` | Rename |
| DELETE | `/api/chats/{id}` | Delete (messages cascade) |
| POST | `/api/chats/{id}/messages` | **SSE stream** of the assistant reply |
| POST | `/api/chats/{id}/truncate` | Drop messages with `seq >= from_seq` — backs edit *and* regenerate |

SSE event types: `user`, `thinking`, `content`, `error`, `done`.

## `web/` — frontend (no build step)

| File | Purpose | Key symbols |
|------|---------|-------------|
| `index.html` | Shell: sidebar, thread, composer. Loads scripts in dependency order |
| `css/style.css` | Entire claude.ai-like light theme; palette lives in `:root` |
| `js/render.js` | Markdown → sanitised HTML, LaTeX normalising, code copy buttons, SVG icons | `renderMarkdown`, `setMarkdown`, `stripLatex`, `outsideCode`, `decorateCodeBlocks`, `ICONS` |
| `js/sidebar.js` | Chat list: select, inline rename, delete | `Sidebar.refresh`, `Sidebar.render` |
| `js/chat.js` | Thread rendering, SSE consumption, stop/regenerate/edit | `Chat.load`, `Chat.reset`, `Chat.stop`, `Chat.streaming`, `send`, `resendFrom` |
| `js/app.js` | fetch helper, chat selection, `#hash` routing, sidebar collapse, health poll | `api`, `App.open`, `App.openNew`, `App.ensureChat`, `pollHealth` |
| `vendor/` | `marked.min.js` (14.1.3), `dompurify.min.js` (3.2.3) — local copies so the app works offline |

**Script load order matters:** `render.js` → `sidebar.js` → `chat.js` → `app.js`.
`app.js` runs the init IIFE and depends on all three.

## Generated / gitignored

| Path | Contents |
|------|----------|
| `models/Qwen3-4B-Q4_K_M.gguf` | 2.5 GB quantized model |
| `data/chats.db` | SQLite: `chats` + `messages` |
| `.venv/` | Python 3.13 virtualenv |

## Where to change things

- **Model, context size, temperature, system prompt, thinking default** → `config.json`, then restart.
- **Colors / spacing / typography** → `:root` in `web/css/style.css`.
- **Inference flags (GPU layers, KV quant)** → the `llama-server` invocation in `start.sh`.
- **New API route** → `server/main.py`, with any query it needs in `server/db.py`.
