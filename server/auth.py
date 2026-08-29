"""Authentication and authorisation for the Telegram bot.

The bot is the one part of samsu that is reachable from outside this machine. Telegram
will deliver a message from *anybody* who finds the bot, and the bot can drive file
tools inside a workspace, so an unauthenticated message must never reach the model.

Authentication is a pairing code: a short single-use secret printed in the terminal of
whoever is running samsu. Proving you can read that terminal is what proves you are
allowed in — no password to leak, nothing stored in config.json, and the binding
survives restarts because it is written to SQLite.

Authorisation is a role on the resulting account:

    owner    file tools, workspace control, can invite and revoke others
    user     conversation only — tool schemas are never sent for these accounts
    blocked  refused, kept as a row so the id cannot silently re-pair
    pending  known but not yet paired

Roles are checked at the point of use, not just at the door: `may_use_tools` is what
decides whether TOOL_SCHEMAS goes into the model request at all, which is the same
belt-and-braces the CLI uses when no workspace is set.
"""

import secrets
from datetime import datetime, timedelta
from typing import Optional

from .db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS tg_accounts (
  tg_user_id   INTEGER PRIMARY KEY,
  username     TEXT,
  display_name TEXT,
  role         TEXT NOT NULL DEFAULT 'pending',
  chat_id      TEXT,
  workspace    TEXT,
  created_at   TEXT NOT NULL,
  last_seen    TEXT
);

CREATE TABLE IF NOT EXISTS tg_pairing (
  code        TEXT PRIMARY KEY,
  role        TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  used_by     INTEGER,
  used_at     TEXT
);

CREATE TABLE IF NOT EXISTS tg_audit (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  tg_user_id INTEGER,
  action     TEXT NOT NULL,
  detail     TEXT,
  created_at TEXT NOT NULL
);
"""

ROLES = ("owner", "user", "blocked", "pending")

# Codes are read off a screen and typed into a phone, so the alphabet omits the
# characters people confuse: O/0, I/1/L.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

PAIRING_TTL_MINUTES = 15


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


# --- accounts ------------------------------------------------------------


def get_account(tg_user_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM tg_accounts WHERE tg_user_id = ?", (tg_user_id,)
        ).fetchone()
    return dict(row) if row else None


def upsert_seen(tg_user_id: int, username: str, display_name: str) -> dict:
    """Record contact from a Telegram user, creating a `pending` row if new.

    Called before any authorisation decision, so an unknown sender still leaves an
    auditable trace instead of vanishing.
    """
    ts = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO tg_accounts (tg_user_id, username, display_name, role, created_at, last_seen)
            VALUES (?, ?, ?, 'pending', ?, ?)
            ON CONFLICT(tg_user_id) DO UPDATE SET
              username = excluded.username,
              display_name = excluded.display_name,
              last_seen = excluded.last_seen
            """,
            (tg_user_id, username, display_name, ts, ts),
        )
    return get_account(tg_user_id)


def set_role(tg_user_id: int, role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    with connect() as conn:
        conn.execute(
            "UPDATE tg_accounts SET role = ? WHERE tg_user_id = ?", (role, tg_user_id)
        )


def set_chat(tg_user_id: int, chat_id: Optional[str]) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE tg_accounts SET chat_id = ? WHERE tg_user_id = ?", (chat_id, tg_user_id)
        )


def set_workspace(tg_user_id: int, path: Optional[str]) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE tg_accounts SET workspace = ? WHERE tg_user_id = ?", (path, tg_user_id)
        )


def list_accounts() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tg_accounts ORDER BY role, created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def has_owner() -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM tg_accounts WHERE role = 'owner' LIMIT 1"
        ).fetchone()
    return row is not None


# --- authorisation -------------------------------------------------------


def is_authenticated(account: Optional[dict]) -> bool:
    return bool(account) and account["role"] in ("owner", "user")


def may_use_tools(account: Optional[dict]) -> bool:
    """Only an owner gets filesystem tools, and only with a workspace bound.

    Both halves matter: role alone is not enough, because tools with no workspace have
    nothing safe to resolve paths against.
    """
    return bool(account) and account["role"] == "owner" and bool(account["workspace"])


def may_administer(account: Optional[dict]) -> bool:
    return bool(account) and account["role"] == "owner"


# --- pairing codes -------------------------------------------------------


def new_code(role: str = "user", ttl_minutes: int = PAIRING_TTL_MINUTES) -> str:
    """Mint a single-use pairing code. The caller is responsible for showing it only
    to whoever should be allowed in."""
    if role not in ("owner", "user"):
        raise ValueError("codes may only grant 'owner' or 'user'")

    code = "-".join(
        "".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(2)
    )
    now = datetime.now()
    with connect() as conn:
        conn.execute(
            "INSERT INTO tg_pairing (code, role, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (
                code,
                role,
                now.isoformat(timespec="seconds"),
                (now + timedelta(minutes=ttl_minutes)).isoformat(timespec="seconds"),
            ),
        )
    return code


def redeem(code: str, tg_user_id: int) -> tuple[bool, str]:
    """Consume a pairing code and promote the account. Returns (ok, message).

    Compared against every unused, unexpired code with `compare_digest` rather than
    looked up by primary key: a direct SELECT on the supplied code leaks, through
    response timing, whether a prefix was right.
    """
    supplied = code.strip().upper().replace(" ", "")
    now = datetime.now()

    with connect() as conn:
        rows = conn.execute(
            "SELECT code, role, expires_at FROM tg_pairing WHERE used_by IS NULL"
        ).fetchall()

        match = None
        for row in rows:
            if secrets.compare_digest(row["code"], supplied):
                match = row
                # No early break: leaving the loop the moment it matches would make the
                # comparison count itself a signal.

        if match is None:
            audit(tg_user_id, "pair_failed", supplied[:16])
            return False, "That code is not valid."

        if datetime.fromisoformat(match["expires_at"]) < now:
            audit(tg_user_id, "pair_expired", match["code"])
            return False, "That code has expired. Ask for a new one."

        conn.execute(
            "UPDATE tg_pairing SET used_by = ?, used_at = ? WHERE code = ?",
            (tg_user_id, now.isoformat(timespec="seconds"), match["code"]),
        )
        conn.execute(
            "UPDATE tg_accounts SET role = ? WHERE tg_user_id = ?",
            (match["role"], tg_user_id),
        )

    audit(tg_user_id, "paired", match["role"])
    return True, match["role"]


def purge_expired() -> int:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM tg_pairing WHERE used_by IS NULL AND expires_at < ?",
            (_now(),),
        )
        return cur.rowcount


# --- audit ---------------------------------------------------------------


def audit(tg_user_id: Optional[int], action: str, detail: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO tg_audit (tg_user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (tg_user_id, action, detail[:400], _now()),
        )


def recent_audit(limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tg_audit ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
