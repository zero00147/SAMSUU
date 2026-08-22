"""FastAPI app: serves the frontend, owns SQLite, proxies streaming from llama-server."""

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, llm
from .config import CONFIG, WEB_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    yield


app = FastAPI(title="samsu", lifespan=lifespan)


# --- schemas -------------------------------------------------------------


class RenameBody(BaseModel):
    title: str


class TruncateBody(BaseModel):
    from_seq: int


class SendBody(BaseModel):
    content: str
    thinking: bool = False


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

    user_msg = db.add_message(chat_id, "user", body.content)

    # Title the chat from its first user message. A second LLM call would force a full
    # prompt reprocess, which is too expensive on an 8 GB machine.
    if user_msg["seq"] == 0:
        db.rename_chat(chat_id, _auto_title(body.content))

    history = db.get_messages(chat_id)

    async def generator():
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        completed = False
        try:
            yield _sse({"type": "user", "message": user_msg})
            async for kind, delta in llm.stream_completion(history, body.thinking):
                if kind == "thinking":
                    thinking_parts.append(delta)
                else:
                    content_parts.append(delta)
                yield _sse({"type": kind, "delta": delta})
            completed = True
        except Exception as exc:  # llama-server down, refused, mid-stream failure
            yield _sse({"type": "error", "error": str(exc)})
        finally:
            # Runs on normal completion AND on client abort (Stop), so a partial
            # response is never lost.
            content = "".join(content_parts)
            thinking = "".join(thinking_parts) or None
            if content or thinking:
                saved = db.add_message(
                    chat_id, "assistant", content, thinking, stopped=not completed
                )
                if completed:
                    yield _sse({"type": "done", "message_id": saved["id"]})
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
