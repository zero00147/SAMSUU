# Project Index

Map of every file in the project. Update whenever a file is added or its role changes.

## Root

| File | Purpose |
|------|---------|
| `README.md` | How to run it, prerequisites, config reference, troubleshooting |
| `samsu` | **Main launcher.** `./samsu` (CLI), `web`, `serve`, `game`, `status`, `stop` |
| `.env` | `TELEGRAM_BOT_TOKEN=…` — gitignored, never committed |
| `start.sh` | Older web-only launcher. Superseded by `./samsu web`; note its `kill 0` trap |
| `config.json` | Single source of tunables: model path, ports, `n_ctx`, sampling params, system prompt |
| `requirements.txt` | fastapi, uvicorn[standard], httpx, huggingface_hub |
| `model_link.txt` | Source model URL + note on why the GGUF build is used instead |
| `DEMO.md` | 12-minute demo script: game, bot auth, authorisation, concurrency. Rehearsed timings |
| `tests/` | `run_tests.py` (groups A–E, G–I) and `run_api_tests.py` (group F). Results to JSON |
| `memory.md` | Durable decisions, reasoning, RAM budget, gotchas — read first when resuming |
| `progress.md` | Terse timestamped milestone log, newest at top |
| `project_index.md` | This file |
| `llama-server.log` | Inference server output (created at runtime; gitignored) |

## `server/` — FastAPI backend

| File | Purpose | Key symbols |
|------|---------|-------------|
| `config.py` | Loads `config.json` once with defaults applied | `CONFIG`, `LLAMA_URL`, `DB_PATH`, `WEB_DIR`, `ROOT` |
| `db.py` | SQLite schema + all queries | `init`, `list_chats`, `create_chat`, `get_chat`, `rename_chat`, `delete_chat`, `get_messages`, `add_message`, `truncate_from`, `next_seq` |
| `llm.py` | Streaming client + **context trimming** | `stream_completion` (yields `(kind, payload)`), `build_payload` (async, trims history to fit), `expand` (inlines attachments), `health` |
| `tokens.py` | Exact token counts via llama-server `/tokenize`, cached by content hash | `count`, `count_many`, `prompt_budget` |
| `documents.py` | Text extraction (PDF/DOCX/MD/TXT) + heading-based section splitting | `extract_text`, `split_sections`, `ExtractError` |
| `workspace.py` | **Sandboxed file tools.** Path confinement + the 7 tool implementations and their OpenAI schemas | `Workspace` (`resolve`, `list_dir`, `read_file`, `write_file`, `edit_file`, `make_dir`, `delete_path`, `move_path`, `run`, `describe`), `TOOL_SCHEMAS`, `DESTRUCTIVE`, `WorkspaceError` |
| `cli.py` | Terminal REPL + its own sync agent loop. **Not yet migrated onto `agent.py`** | `main`, `chat`, `run_tools`, `confirm`, `trim`, `Spinner`, `ask_for_dir` |
| `agent.py` | **Async tool loop, frontend-agnostic.** Emits events instead of printing | `run_agent` (async generator), `complete`, `trim`, `_execute`, `GENERATION_LOCK`, `Truncated` |
| `auth.py` | **Telegram authn/authz.** Pairing codes, roles, audit log | `new_code`, `redeem`, `may_use_tools`, `may_administer`, `is_authenticated`, `set_role`, `set_workspace`, `audit` |
| `voice.py` | **Speech in and out**, all local subprocesses: opusdec → whisper.cpp, and `say` → opusenc | `transcribe`, `synthesize`, `speakable`, `status`, `available`, `VoiceError` |
| `clarify.py` | **Requirement clarification.** Walks a fixed checklist, asks about what is missing, writes the spec | `ClarifySession` (`begin`, `answer`, `render_spec`, `build_prompt`), `CHECKLIST`, `_verified`, `_is_deferral` |
| `telegram.py` | Bot: long polling, dispatch, commands, progress rendering | `TelegramBot` (`run`, `_dispatch`, `_command`, `_converse`), `Session`, `start`, `load_token`, `chunk` |
| `main.py` | Routes, SSE generation, static mount, **starts the bot in lifespan** | `api_send` (the streaming endpoint), `api_truncate`, `_auto_title`, `_sse` |

### Agent events (`server/agent.py`)

`run_agent()` is an async generator yielding `(kind, payload)`. The CLI would print these;
the bot edits one Telegram message with them.

| kind | payload |
|---|---|
| `thinking` | reasoning text (only when thinking is on) |
| `tool` | `{name, args}` — about to run |
| `result` | `{name, ok, summary}` — what it did |
| `warn` | string; the loop is recovering, not failing |
| `text` | the final prose answer |
| `done` | `{rounds, timings}` |

### Telegram tables (`server/auth.py`)

| Table | Holds |
|---|---|
| `tg_accounts` | `tg_user_id` → role, bound samsu `chat_id`, `workspace` |
| `tg_pairing` | single-use codes with `expires_at` / `used_by` |
| `tg_audit` | access events: pairing attempts, denials, workspace changes, turns |

Roles: `owner` (tools + admin) · `user` (chat only) · `blocked` (silence) · `pending`.
File tools need role `owner` **and** a bound workspace — see `may_use_tools`.

### Clarification checklist (`server/clarify.py`)

Questions are asked in this order, skipping whatever the request already settled. The
**code** picks the next unsettled dimension; the model only judges answers and writes the
question. Its own readiness judgment is not trusted — see `memory.md`.

| Dimension | Settles |
|---|---|
| `scope` | exactly what changes, and what deliberately does not |
| `trigger` | who uses it and how they reach it |
| `behaviour` | what happens step by step, including edge cases |
| `data` | what is stored or remembered, and where |
| `acceptance` | how we will know it is finished |

A dimension leaves the list either answered or **deferred** — deferral records an explicit
assumption in the spec rather than deciding silently. Bot commands: `/spec`, `/build`,
`/cancel`, `/voice`.

### Routes (`server/main.py`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Is llama-server reachable; which model; context size |
| GET | `/api/budget` | Tokens available for the prompt (drives the composer meter) |
| POST | `/api/tokenize` | Token count for arbitrary text |
| GET/POST | `/api/documents` | List / upload (multipart) a document |
| GET | `/api/documents/{id}/sections` | Sections with headings, levels, token counts |
| DELETE | `/api/documents/{id}` | Delete a document and its sections |
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
| `js/status.js` | Top-right activity pill: phase + live elapsed timer | `Status.start`, `Status.setPhase`, `Status.finish`, `Status.stopped`, `Status.failed` |
| `js/docs.js` | Upload panel, section picker, token-budget enforcement | `Docs.takeSelection`, `Docs.over`, `Docs.attachedTokens` |
| `js/sidebar.js` | Chat list: select, inline rename, delete | `Sidebar.refresh`, `Sidebar.render` |
| `js/chat.js` | Thread rendering, SSE consumption, stop/regenerate/edit | `Chat.load`, `Chat.reset`, `Chat.stop`, `Chat.streaming`, `send`, `resendFrom` |
| `js/app.js` | fetch helper, chat selection, `#hash` routing, sidebar collapse, health poll | `api`, `App.open`, `App.openNew`, `App.ensureChat`, `pollHealth` |
| `vendor/` | `marked.min.js` (14.1.3), `dompurify.min.js` (3.2.3) — local copies so the app works offline |

**Script load order matters:** `render.js` → `status.js` → `sidebar.js` → `chat.js` → `app.js`.
`app.js` runs the init IIFE and depends on the rest.

### Generation timing

`messages` stores four timing columns, all nullable (messages predating the feature have
NULL, and the UI skips absent fields):

| Column | Meaning |
|---|---|
| `ttft_ms` | Send → first token of any kind. Prompt-processing latency. |
| `think_ms` | First reasoning token → first answer token. Only set when Think mode is on. |
| `duration_ms` | Total wall time for the turn. |
| `tokens` / `tokens_per_sec` | From llama.cpp's `timings` block in the final stream chunk (`predicted_n`, `predicted_per_second`) — real counts, not estimates. |

Surfaced in three places: the live pill (top right), the `Thought for Ns` summary on the
thinking block, and the stats line under each assistant reply.

## `apps/asteroids/` — Void Runner (Application 1)

Served by `./samsu game` on :8100. Static only; needs http (import maps fail on `file://`).

| File | Purpose | Key symbols |
|------|---------|-------------|
| `index.html` | Shell, import map, HUD and hangar markup | — |
| `css/style.css` | HUD, hangar, overlays | palette in `:root` |
| `js/config.js` | **All tuning.** Playfield, physics, the 3 level definitions | `WORLD`, `BASE`, `ASTEROID`, `LEVELS`, `DRONE`, `wrap`, `shortestDelta` |
| `js/ships.js` | The 5 ships: stats + procedural geometry (no model files) | `SHIPS`, `buildShipMesh`, `shipById` |
| `js/audio.js` | Every SFX synthesised at runtime; no audio assets | `audio.laser/explosion/hit/levelUp/gameOver/setThrust`, `unlock` |
| `js/entities.js` | Rocks, bullets, drones, pooled debris, starfield | `Asteroid`, `Bullet`, `Drone`, `Debris`, `spawnAsteroidField`, `makeStarfield` |
| `js/game.js` | Engine: scene, player, loop, collisions, level state machine | `Game`, `Player`, `collides`, `disposeTree` |
| `js/main.js` | Hangar UI, HUD, overlays; the only DOM code | `select`, `launch`, `refreshHud`, `showOverlay` |
| `js/devlog.js` | **`?debug` harness.** Reports state via the access log | beacons `boot`/`error`/`alive`/`autoplay` |
| `vendor/three.module.min.js` | Three.js r160, loaded through the import map |

Debug URLs: `?debug` · `?debug&autoplay` · `?debug&autoplay&level=3`.

Ship models point along **+Y** in local space; the engine sets `rotation.z`, so forward is
`(-sin a, cos a)`. Get that wrong and ships fly sideways.

## Generated / gitignored

| Path | Contents |
|------|----------|
| `models/Qwen3-4B-Q4_K_M.gguf` | 2.5 GB quantized model |
| `models/ggml-base.en.bin` | 148 MB whisper.cpp speech model |
| `data/chats.db` | SQLite: `chats` + `messages` |
| `.venv/` | Python 3.13 virtualenv |

## Where to change things

- **Model, context size, temperature, system prompt, thinking default** → `config.json`, then restart.
- **Colors / spacing / typography** → `:root` in `web/css/style.css`.
- **Inference flags (GPU layers, KV quant)** → the `llama-server` invocation in `start.sh`.
- **New API route** → `server/main.py`, with any query it needs in `server/db.py`.
