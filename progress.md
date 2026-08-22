# Progress

<!-- newest at top, one line per milestone: YYYY-MM-DD HH:MM — what changed -->

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
