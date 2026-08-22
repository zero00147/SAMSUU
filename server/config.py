"""Loads config.json once and exposes it as a module-level object."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_DEFAULTS = {
    "model_path": "models/Qwen3-4B-Q4_K_M.gguf",
    "llama_host": "127.0.0.1",
    "llama_port": 8080,
    "app_host": "127.0.0.1",
    "app_port": 8000,
    "n_ctx": 8192,
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "max_tokens": 2048,
    "enable_thinking": False,
    "system_prompt": "You are a helpful, thoughtful assistant.",
}


def _load() -> dict:
    path = ROOT / "config.json"
    cfg = dict(_DEFAULTS)
    if path.exists():
        cfg.update(json.loads(path.read_text()))
    return cfg


CONFIG = _load()

LLAMA_URL = f"http://{CONFIG['llama_host']}:{CONFIG['llama_port']}"
DB_PATH = ROOT / "data" / "chats.db"
WEB_DIR = ROOT / "web"
