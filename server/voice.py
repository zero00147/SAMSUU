"""Speech in and speech out, entirely on this machine.

Telegram voice notes arrive as Ogg/Opus. Whisper wants 16 kHz mono WAV. Replies go back
as Ogg/Opus again, so the round trip is:

    .oga ──opusdec──▶ 16 kHz WAV ──whisper-cli──▶ text
    text ──say──▶ 24 kHz WAV ──opusenc──▶ .opus

Everything here is a local subprocess. No speech service is contacted, which keeps the
same property the rest of samsu has: the only bytes that leave the machine are the ones
Telegram itself carries.

Why these tools:

* **whisper.cpp**, not a Python speech package. `openai-whisper` pulls in torch, which is
  a multi-gigabyte install and a poor fit for 8 GB of shared memory. whisper.cpp is the
  same lineage as the llama.cpp already in use, ships a 148 MB `base.en` model, and is
  driven exactly the way `llama-server` is — a binary, a model file on disk, a subprocess.
* **opus-tools**, not ffmpeg. macOS `afconvert` can *decode* Opus but its Ogg muxer fails
  on write (`ExtAudioFileWrite failed ('pck?')`), so it cannot produce the format Telegram
  requires for a voice note. `opusenc`/`opusdec` are a few hundred kilobytes and do both
  directions reliably. `afconvert` is kept as the decode fallback for non-Opus audio.
* **macOS `say`** for synthesis. It is built in, needs no model and no extra memory, and
  writes WAV directly.
"""

import asyncio
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from .config import CONFIG, ROOT

WHISPER_BIN = "whisper-cli"
OPUSDEC_BIN = "opusdec"
OPUSENC_BIN = "opusenc"
SAY_BIN = "say"
AFCONVERT_BIN = "afconvert"

# Whisper is given 16 kHz mono because that is what the model was trained on; anything
# else is resampled internally and simply wastes time.
STT_RATE = 16000
TTS_RATE = 24000


class VoiceError(Exception):
    """Raised when audio cannot be transcribed or synthesised."""


def model_path() -> Path:
    p = Path(CONFIG["voice_stt_model"]).expanduser()
    return p if p.is_absolute() else ROOT / p


def status() -> dict:
    """What is present and what is missing, for /status and the startup banner."""
    missing = [b for b in (WHISPER_BIN, OPUSDEC_BIN, OPUSENC_BIN) if not shutil.which(b)]
    model = model_path()
    if not model.exists():
        missing.append(f"model {model.name}")
    return {
        "enabled": bool(CONFIG.get("voice_enabled", True)),
        "listening": not missing,
        "speaking": bool(CONFIG.get("voice_speak_replies", True)) and not missing,
        "model": model.name if model.exists() else None,
        "missing": missing,
    }


def available() -> bool:
    s = status()
    return s["enabled"] and s["listening"]


async def _run(*args, timeout: float, stdin_ok: bool = False) -> str:
    """Run a subprocess, returning stdout. Raises VoiceError on failure or timeout."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise VoiceError(f"{args[0]} timed out after {timeout:.0f}s")

    if proc.returncode != 0:
        detail = (err or b"").decode("utf-8", "replace").strip().splitlines()
        raise VoiceError(f"{args[0]} failed: {detail[-1] if detail else proc.returncode}")
    return (out or b"").decode("utf-8", "replace")


# --- speech in ------------------------------------------------------------

async def _to_wav(src: Path, dst: Path) -> None:
    """Decode whatever Telegram sent into 16 kHz mono WAV.

    opusdec handles the Ogg/Opus that voice notes always use. An `audio` attachment may
    be m4a or mp3 instead, which opusdec refuses, so afconvert covers that case.
    """
    try:
        await _run(OPUSDEC_BIN, "--quiet", "--force-wav", "--rate", str(STT_RATE),
                   str(src), str(dst), timeout=60)
        return
    except VoiceError:
        if not shutil.which(AFCONVERT_BIN):
            raise
    await _run(AFCONVERT_BIN, "-f", "WAVE", "-d", f"LEI16@{STT_RATE}", "-c", "1",
               str(src), str(dst), timeout=60)


async def transcribe(audio: bytes, suffix: str = ".oga") -> str:
    """Voice note bytes to text. Returns '' when the model heard nothing."""
    if not available():
        raise VoiceError("voice input is not configured on this machine")

    limit = int(CONFIG.get("voice_max_bytes", 8_000_000))
    if len(audio) > limit:
        raise VoiceError(
            f"that recording is {len(audio) // 1000} KB — the limit is {limit // 1000} KB. "
            "Try a shorter message."
        )

    with tempfile.TemporaryDirectory(prefix="samsu-stt-") as tmp:
        d = Path(tmp)
        src, wav = d / f"in{suffix}", d / "in.wav"
        src.write_bytes(audio)
        await _to_wav(src, wav)

        out = await _run(
            WHISPER_BIN,
            "-m", str(model_path()),
            "-f", str(wav),
            "-l", "en",
            "-t", str(CONFIG.get("voice_stt_threads", 4)),
            "-nt", "-np",
            timeout=float(CONFIG.get("voice_stt_timeout", 120)),
        )

    text = " ".join(out.split())
    # Whisper emits bracketed markers for non-speech audio; they are not transcript.
    text = re.sub(r"[\[(](?:BLANK_AUDIO|INAUDIBLE|MUSIC|SOUND|NOISE)[^\])]*[\])]", "", text,
                  flags=re.IGNORECASE)
    return text.strip()


# --- speech out -----------------------------------------------------------

_MD = [
    (re.compile(r"```.*?```", re.S), " "),          # code fences are unspeakable
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"^\s*[#>]+\s*", re.M), ""),
    (re.compile(r"^\s*[-*•]\s+", re.M), ""),
    (re.compile(r"\*\*([^*]*)\*\*"), r"\1"),
    (re.compile(r"\*([^*]*)\*"), r"\1"),
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),
    (re.compile(r"[ \t]+"), " "),
    (re.compile(r"\n{2,}"), "\n"),
]


def speakable(text: str) -> str:
    """Markdown reads badly aloud — 'star star Goal star star'. Strip it to prose."""
    out = text or ""
    for pattern, repl in _MD:
        out = pattern.sub(repl, out)
    out = out.strip()

    limit = int(CONFIG.get("voice_max_spoken_chars", 700))
    if len(out) <= limit:
        return out
    # Cut at a sentence end so the voice note does not stop mid-word.
    cut = out[:limit]
    stop = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[: stop + 1] if stop > limit // 3 else cut).strip()


async def synthesize(text: str) -> Optional[bytes]:
    """Text to Ogg/Opus bytes for sendVoice. Returns None if nothing is speakable."""
    body = speakable(text)
    if not body:
        return None

    with tempfile.TemporaryDirectory(prefix="samsu-tts-") as tmp:
        d = Path(tmp)
        wav, ogg = d / "out.wav", d / "out.opus"
        await _run(
            SAY_BIN,
            "-v", str(CONFIG.get("voice_tts_voice", "Samantha")),
            "-r", str(CONFIG.get("voice_tts_rate", 180)),
            "-o", str(wav),
            "--data-format", f"LEI16@{TTS_RATE}",
            body,
            timeout=90,
        )
        await _run(OPUSENC_BIN, "--quiet", "--bitrate", "24", str(wav), str(ogg),
                   timeout=60)
        return ogg.read_bytes()
