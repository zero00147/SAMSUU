"""FastAPI app: serves the frontend, owns SQLite, proxies streaming from llama-server."""

import asyncio
import contextlib
import json
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, db, documents, llm, telegram, tokens
from .config import CONFIG, WEB_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    auth.init()

    # The bot runs as a task in this same event loop, so `./samsu web` serves the
    # browser and the phone together. It never raises on a missing token — the web UI
    # must come up either way.
    bot_task = await telegram.start(app.state)

    yield

    if bot_task:
        bot_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await bot_task


app = FastAPI(title="samsu", lifespan=lifespan)


# --- schemas -------------------------------------------------------------


class RenameBody(BaseModel):
    title: str


class TruncateBody(BaseModel):
    from_seq: int


class SendBody(BaseModel):
    content: str
    thinking: bool = False
    section_ids: list[int] = []


# --- documents -----------------------------------------------------------

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@app.get("/api/budget")
async def api_budget():
    """Prompt space available, so the composer can warn before a send fails."""
    return {
        "budget": tokens.prompt_budget(),
        "n_ctx": CONFIG["n_ctx"],
        "max_tokens": CONFIG["max_tokens"],
    }


@app.post("/api/tokenize")
async def api_tokenize(body: dict):
    return {"tokens": await tokens.count(body.get("text", ""))}


@app.get("/api/documents")
async def api_list_documents():
    return db.list_documents()


@app.post("/api/documents")
async def api_upload_document(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file larger than 20 MB")

    try:
        text = documents.extract_text(file.filename or "upload", data)
    except documents.ExtractError as exc:
        raise HTTPException(400, str(exc))

    if len(text) > documents.MAX_CHARS:
        raise HTTPException(413, "document too large to index")
    if not text.strip():
        raise HTTPException(400, "no text found in file")

    sections = documents.split_sections(text)
    counts = await tokens.count_many(s["content"] for s in sections)
    for s, n in zip(sections, counts):
        s["tokens"] = n

    return db.add_document(str(uuid.uuid4()), file.filename or "upload", sections)


@app.get("/api/documents/{doc_id}/sections")
async def api_document_sections(doc_id: str):
    return db.get_sections(doc_id)


@app.delete("/api/documents/{doc_id}")
async def api_delete_document(doc_id: str):
    db.delete_document(doc_id)
    return {"ok": True}


# --- chat CRUD -----------------------------------------------------------


@app.get("/api/health")
async def health():
    status = await llm.health()
    return {
        **status,
        "n_ctx": CONFIG["n_ctx"],
        "enable_thinking": CONFIG["enable_thinking"],
    }


@app.get("/api/chats")
async def api_list_chats():
    return db.list_chats()


@app.post("/api/chats")
async def api_create_chat():
    return db.create_chat()


@app.get("/api/chats/{chat_id}")
async def api_get_chat(chat_id: str):
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "chat not found")
    return {**chat, "messages": db.get_messages(chat_id)}


@app.patch("/api/chats/{chat_id}")
async def api_rename_chat(chat_id: str, body: RenameBody):
    if not db.get_chat(chat_id):
        raise HTTPException(404, "chat not found")
    title = body.title.strip() or "New chat"
    db.rename_chat(chat_id, title)
    return {"ok": True, "title": title}


@app.delete("/api/chats/{chat_id}")
async def api_delete_chat(chat_id: str):
    if not db.get_chat(chat_id):
        raise HTTPException(404, "chat not found")
    db.delete_chat(chat_id)
    return {"ok": True}


@app.post("/api/chats/{chat_id}/truncate")
async def api_truncate(chat_id: str, body: TruncateBody):
    if not db.get_chat(chat_id):
        raise HTTPException(404, "chat not found")
    removed = db.truncate_from(chat_id, body.from_seq)
    return {"ok": True, "removed": removed}


# --- streaming generation ------------------------------------------------


def _auto_title(text: str) -> str:
    words = text.strip().split()
    title = " ".join(words[:6])
    if len(words) > 6:
        title += "…"
    return title[:80] or "New chat"


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


@app.post("/api/chats/{chat_id}/messages")
async def api_send(chat_id: str, body: SendBody):
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "chat not found")

    # Resolve attached document sections to their text. Stored on the message so the
    # turn reproduces identically on reload, and so a later edit/regenerate keeps them.
    attach_json = None
    if body.section_ids:
        rows = db.get_sections_by_ids(body.section_ids)
        if rows:
            attach_json = json.dumps([
                {"label": f"{r['filename']} › {r['heading']}", "content": r["content"]}
                for r in rows
            ])

    user_msg = db.add_message(chat_id, "user", body.content, attachments=attach_json)

    # Title the chat from its first user message. A second LLM call would force a full
    # prompt reprocess, which is too expensive on an 8 GB machine.
    if user_msg["seq"] == 0:
        db.rename_chat(chat_id, _auto_title(body.content))

    history = db.get_messages(chat_id)

    async def generator():
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        completed = False

        # Timing. `ttft` is how long the model took before producing anything at all
        # (prompt processing); `think_ms` is how long it then spent reasoning before
        # the first word of the actual answer.
        t0 = time.perf_counter()
        t_first = None
        t_think_start = None
        t_content_start = None
        timings: dict = {}

        try:
            yield _sse({"type": "user", "message": user_msg})
            async for kind, payload in llm.stream_completion(history, body.thinking):
                if kind == "timings":
                    timings = payload or {}
                    continue

                now = time.perf_counter()
                if t_first is None:
                    t_first = now
                if kind == "thinking":
                    if t_think_start is None:
                        t_think_start = now
                    thinking_parts.append(payload)
                else:
                    if t_content_start is None:
                        t_content_start = now
                    content_parts.append(payload)
                yield _sse({"type": kind, "delta": payload})
            completed = True
        except Exception as exc:  # llama-server down, refused, mid-stream failure
            yield _sse({"type": "error", "error": str(exc)})
        finally:
            # Runs on normal completion AND on client abort (Stop), so a partial
            # response is never lost.
            t_end = time.perf_counter()
            content = "".join(content_parts)
            thinking = "".join(thinking_parts) or None

            def ms(a, b):
                return None if a is None or b is None else int((b - a) * 1000)

            stats = {
                "ttft_ms": ms(t0, t_first),
                # reasoning phase ends when the first real answer token arrives; if the
                # model was still reasoning when it stopped, run it to the end instead.
                "think_ms": ms(t_think_start, t_content_start or t_end),
                "duration_ms": ms(t0, t_end),
                "tokens": timings.get("predicted_n"),
                "tokens_per_sec": (
                    round(timings["predicted_per_second"], 1)
                    if timings.get("predicted_per_second") is not None
                    else None
                ),
            }

            if content or thinking:
                saved = db.add_message(
                    chat_id, "assistant", content, thinking,
                    stopped=not completed, stats=stats,
                )
                if completed:
                    yield _sse(
                        {"type": "done", "message_id": saved["id"], "stats": stats}
                    )
            else:
                db.touch_chat(chat_id)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- static frontend -----------------------------------------------------


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
