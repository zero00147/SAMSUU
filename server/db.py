"""SQLite persistence for chats and messages.

Ordering and truncation both hang off `messages.seq`. Edit and regenerate are the same
primitive: delete every message with `seq >= N`, then re-run generation.
"""

import sqlite3
import uuid
from datetime import datetime
from typing import Optional

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
  id         TEXT PRIMARY KEY,
  title      TEXT NOT NULL DEFAULT 'New chat',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id        TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  seq            INTEGER NOT NULL,
  role           TEXT NOT NULL,
  content        TEXT NOT NULL,
  thinking       TEXT,
  stopped        INTEGER NOT NULL DEFAULT 0,
  ttft_ms        INTEGER,
  think_ms       INTEGER,
  duration_ms    INTEGER,
  tokens         INTEGER,
  tokens_per_sec REAL,
  created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, seq);

CREATE TABLE IF NOT EXISTS documents (
  id           TEXT PRIMARY KEY,
  filename     TEXT NOT NULL,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_sections (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  idx     INTEGER NOT NULL,
  heading TEXT NOT NULL,
  level   INTEGER NOT NULL DEFAULT 1,
  content TEXT NOT NULL,
  tokens  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sections_doc ON document_sections(doc_id, idx);
"""

# Columns added after the first release. SQLite has no "ADD COLUMN IF NOT EXISTS",
# so existing databases are upgraded by diffing against PRAGMA table_info.
_ADDED_COLUMNS = [
    ("ttft_ms", "INTEGER"),
    ("think_ms", "INTEGER"),
    ("duration_ms", "INTEGER"),
    ("tokens", "INTEGER"),
    ("tokens_per_sec", "REAL"),
    ("attachments", "TEXT"),  # JSON list of {label, content} attached to this turn
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE messages ADD COLUMN {name} {ddl}")


# --- chats ---------------------------------------------------------------


def list_chats() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM chats ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def create_chat(title: str = "New chat") -> dict:
    chat_id = str(uuid.uuid4())
    ts = _now()
    with connect() as conn:
        conn.execute(
            "INSERT INTO chats (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (chat_id, title, ts, ts),
        )
    return {"id": chat_id, "title": title, "created_at": ts, "updated_at": ts}


def get_chat(chat_id: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    return dict(row) if row else None


def rename_chat(chat_id: str, title: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE chats SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), chat_id),
        )


def touch_chat(chat_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (_now(), chat_id))


def delete_chat(chat_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))


# --- messages ------------------------------------------------------------


def get_messages(chat_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE chat_id = ? ORDER BY seq", (chat_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def next_seq(chat_id: str) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 AS n FROM messages WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    return row["n"]


def add_message(
    chat_id: str,
    role: str,
    content: str,
    thinking: Optional[str] = None,
    stopped: bool = False,
    stats: Optional[dict] = None,
    attachments: Optional[str] = None,
) -> dict:
    """Insert a message. `stats` carries generation timings for assistant turns;
    `attachments` is a JSON string of document sections attached to a user turn."""
    seq = next_seq(chat_id)
    ts = _now()
    s = stats or {}
    row = {
        "ttft_ms": s.get("ttft_ms"),
        "think_ms": s.get("think_ms"),
        "duration_ms": s.get("duration_ms"),
        "tokens": s.get("tokens"),
        "tokens_per_sec": s.get("tokens_per_sec"),
    }
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO messages (chat_id, seq, role, content, thinking, stopped,"
            " ttft_ms, think_ms, duration_ms, tokens, tokens_per_sec, attachments,"
            " created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chat_id, seq, role, content, thinking, int(stopped),
                row["ttft_ms"], row["think_ms"], row["duration_ms"],
                row["tokens"], row["tokens_per_sec"], attachments, ts,
            ),
        )
        msg_id = cur.lastrowid
        conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (ts, chat_id))
    return {
        "id": msg_id,
        "chat_id": chat_id,
        "seq": seq,
        "role": role,
        "content": content,
        "thinking": thinking,
        "stopped": int(stopped),
        "attachments": attachments,
        "created_at": ts,
        **row,
    }


# --- documents -----------------------------------------------------------


def add_document(doc_id: str, filename: str, sections: list[dict]) -> dict:
    """Store a document and its sections. `sections` need heading/level/content/tokens."""
    ts = _now()
    total = sum(s.get("tokens", 0) for s in sections)
    with connect() as conn:
        conn.execute(
            "INSERT INTO documents (id, filename, total_tokens, created_at)"
            " VALUES (?, ?, ?, ?)",
            (doc_id, filename, total, ts),
        )
        conn.executemany(
            "INSERT INTO document_sections (doc_id, idx, heading, level, content, tokens)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (doc_id, i, s["heading"], s.get("level", 1), s["content"], s.get("tokens", 0))
                for i, s in enumerate(sections)
            ],
        )
    return {
        "id": doc_id,
        "filename": filename,
        "total_tokens": total,
        "section_count": len(sections),
        "created_at": ts,
    }


def list_documents() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT d.id, d.filename, d.total_tokens, d.created_at,"
            " (SELECT COUNT(*) FROM document_sections s WHERE s.doc_id = d.id)"
            "   AS section_count"
            " FROM documents d ORDER BY d.created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_sections(doc_id: str, with_content: bool = False) -> list[dict]:
    cols = "id, idx, heading, level, tokens" + (", content" if with_content else "")
    with connect() as conn:
        rows = conn.execute(
            f"SELECT {cols} FROM document_sections WHERE doc_id = ? ORDER BY idx",
            (doc_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_sections_by_ids(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT s.id, s.heading, s.content, s.tokens, d.filename"
            f" FROM document_sections s JOIN documents d ON d.id = s.doc_id"
            f" WHERE s.id IN ({marks}) ORDER BY s.doc_id, s.idx",
            ids,
        ).fetchall()
    return [dict(r) for r in rows]


def delete_document(doc_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM document_sections WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


def truncate_from(chat_id: str, from_seq: int) -> int:
    """Delete every message with seq >= from_seq. Returns how many were removed."""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM messages WHERE chat_id = ? AND seq >= ?", (chat_id, from_seq)
        )
        conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (_now(), chat_id))
        return cur.rowcount
