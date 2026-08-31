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
    # CLI: run file operations without confirming. Confinement to the chosen
    # workspace still applies — this only removes the y/N prompt inside it.
    "auto_approve": True,
    "max_tool_rounds": 40,
    # The CLI needs a bigger output budget than chat: a write_file call has to emit the
    # whole file inside a JSON string, and running out mid-string produces unparseable
    # arguments that llama-server rejects with a 500.
    "cli_max_tokens": 3072,
    # Voice, via the Telegram bot. Speech in is whisper.cpp against a local model file;
    # speech out is macOS `say` encoded to Ogg/Opus. Both are local subprocesses — see
    # server/voice.py. Set voice_enabled false to switch the whole path off.
    "voice_enabled": True,
    "voice_stt_model": "models/ggml-base.en.bin",
    "voice_stt_threads": 4,
    "voice_stt_timeout": 120,
    "voice_speak_replies": True,
    "voice_tts_voice": "Samantha",
    "voice_tts_rate": 180,
    "voice_max_seconds": 180,
    "voice_max_bytes": 8_000_000,
    "voice_max_spoken_chars": 700,
    # How many clarifying questions a spec session may ask before it stops and writes
    # the specification with whatever it has. See server/clarify.py.
    "clarify_max_questions": 4,
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
