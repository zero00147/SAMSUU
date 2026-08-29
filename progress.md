# Progress

<!-- newest at top, one line per milestone: YYYY-MM-DD HH:MM — what changed -->

2026-08-30 00:07 — Void Runner confirmed visually by a human: menu, ships, audio, rocks, flight all correct
2026-08-30 00:02 — LIVE E2E: @rifat_samsu_bot paired → /dir → fixed a real bug in 15s, 2 tool calls
2026-08-30 00:00 — bot verified end to end on a real phone; mirroring into the web sidebar confirmed
2026-08-29 23:53 — first real pairing: owner claimed via terminal code, audit row written
2026-08-29 23:45 — token gotcha: BotFather wraps the token across 2 lines; first paste was mistyped
2026-08-29 23:08 — docs updated for phase 2; README stale "12 tool rounds" corrected to 40
2026-08-29 23:05 — CONCURRENCY VERIFIED: 2 agent turns + web UI, 31 requests served, slowest 44ms
2026-08-29 23:00 — sandbox re-verified through the new agent path: 7/7 escapes blocked
2026-08-29 22:58 — auth verified: single-use codes, expiry, owner-without-workspace gets no tools
2026-08-29 22:55 — Telegram bot added (server/telegram.py + auth.py + agent.py), runs inside uvicorn
2026-08-29 22:47 — game verified in-browser via access-log beacon: lvl1 + lvl3, 120fps, no errors
2026-08-29 22:43 — Application 1 built: apps/asteroids, 3D Three.js, 3 levels, 5 ships, WebAudio SFX
2026-08-27 07:40 — fixed 500 crash: write_file truncating mid-JSON; cli_max_tokens 3072 + retry-smaller
2026-08-27 07:10 — automatic mode is now default (auto_approve=true); tool rounds 12→40
2026-08-27 06:40 — ./samsu launcher: cli/web/serve/status/stop all verified
2026-08-27 06:30 — CLI agent loop works: found a bug, fixed it, wrote a test that passes
2026-08-27 06:20 — sandbox verified: 6/6 escape attempts blocked incl. symlink to /etc/passwd
2026-08-27 06:10 — CLI added (server/cli.py + workspace.py); Qwen3-4B does native tool calling
2026-08-27 05:50 — docs panel verified: PRD.md → 32 sections, tick to attach, live token budget
2026-08-27 05:45 — document upload added (PDF/DOCX/MD/TXT) + heading-based section splitting
2026-08-27 05:30 — CONTEXT OVERFLOW FIXED: 24,191-token history trimmed to 5,602 and completed
2026-08-27 05:20 — added server/tokens.py (exact counts via llama-server /tokenize, cached)
2026-08-27 02:52 — CONFIRMED IN LOGS: context overflow hit 3× in real use (12.6k tokens vs 8192 window)
2026-08-27 02:50 — timing feature done: ttft/think/duration/tokens persisted + shown per message
2026-08-27 02:48 — top-right status pill added (Thinking/Reasoning/Writing/Done, live elapsed)
2026-08-27 02:45 — DB migration: messages gains ttft_ms, think_ms, duration_ms, tokens, tokens_per_sec
2026-08-23 01:25 — v1 complete: all features verified end-to-end, docs written
2026-08-23 01:22 — verified Stop persists partial reply + cancels llama-server (CPU→0.3%)
2026-08-23 01:20 — LaTeX normalizer added (Qwen3 emits raw $\text{}$); 8/8 edge cases pass
2026-08-23 01:19 — hash routing added; refresh keeps you in the current chat
2026-08-23 01:16 — UI renders correctly in Chrome; code blocks + thinking block confirmed
2026-08-23 01:14 — full stack live: streaming, auto-title, truncate all working
2026-08-23 01:13 — Think toggle added; thinking off by default (0.9s vs ~25s measured)
2026-08-23 01:12 — llama-server up, 16-17 tok/s, reasoning_content split confirmed
2026-08-23 01:10 — Qwen3-4B-Q4_K_M.gguf downloaded (2.3 GB), GGUF header valid
2026-08-23 01:08 — frontend written (white claude.ai-style theme, no build step)
2026-08-23 01:06 — backend written (db.py, llm.py, main.py)
2026-08-23 01:05 — python@3.13 + venv + deps installed; project scaffolded
2026-08-23 01:03 — project dirs created; llama.cpp installed
2026-08-23 00:56 — plan approved: llama.cpp + FastAPI + vanilla JS, Qwen3-4B Q4_K_M
