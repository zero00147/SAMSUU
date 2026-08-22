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
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  seq        INTEGER NOT NULL,
  role       TEXT NOT NULL,
  content    TEXT NOT NULL,
  thinking   TEXT,
  stopped    INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, seq);
"""


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
) -> dict:
    seq = next_seq(chat_id)
    ts = _now()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO messages (chat_id, seq, role, content, thinking, stopped, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, seq, role, content, thinking, int(stopped), ts),
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
        "created_at": ts,
    }


def truncate_from(chat_id: str, from_seq: int) -> int:
    """Delete every message with seq >= from_seq. Returns how many were removed."""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM messages WHERE chat_id = ? AND seq >= ?", (chat_id, from_seq)
        )
        conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (_now(), chat_id))
        return cur.rowcount
