"""Telegram bot front end for samsu.

Runs as an asyncio task inside the same uvicorn process as the web UI, so `./samsu web`
brings up both and they share one SQLite handle, one httpx pool and one llama-server.
Generation is serialised by `agent.GENERATION_LOCK`, so a long build started from the
phone queues against a browser message instead of both degrading.

No bot framework: this is long polling against the HTTP API with httpx, the same way
`llm.py` talks to llama-server. It keeps the dependency list at seven and keeps the
retry and timeout behaviour visible rather than buried in a library.

Security posture — the bot is the only part of samsu reachable from outside this
machine, so every inbound message is checked in `_dispatch` before it can reach the
model, and file tools are withheld unless the sender is an owner *with* a workspace
bound. See `server/auth.py`.
"""

import asyncio
import html
import os
import time
from pathlib import Path
from typing import Optional

import httpx

from . import auth, db
from .agent import run_agent
from .config import CONFIG, LLAMA_URL, ROOT
from .workspace import Workspace, WorkspaceError

API = "https://api.telegram.org"

# Telegram hard-caps a message at 4096 characters; leave room for the progress header.
MAX_MESSAGE = 3800

# Telegram rate-limits edits to roughly one per second per chat. Progress updates are
# throttled well under that — going over gets the bot 429'd mid-build.
EDIT_INTERVAL = 1.6

POLL_TIMEOUT = 25          # seconds Telegram holds the long poll open
HISTORY_TURNS = 40         # in-memory agent history cap per user


def load_token() -> Optional[str]:
    """Token from the environment, or from a .env file that is never committed."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token

    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "TELEGRAM_BOT_TOKEN":
                return value.strip().strip("'\"")
    return None


def chunk(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split a long reply on line boundaries where possible, mid-line if it must."""
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []

    out, buf = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if buf:
                out.append(buf)
                buf = ""
            out.append(line[:limit])
            line = line[limit:]
        if len(buf) + len(line) > limit:
            out.append(buf)
            buf = line
        else:
            buf += line
    if buf:
        out.append(buf)
    return out


class Session:
    """Per-user conversation state, held in memory.

    The agent history contains `tool` and `tool_calls` messages that the `messages`
    table has no columns for, so it lives here. The plain user/assistant text is
    mirrored into a normal samsu chat so the same conversation is readable in the
    browser while it is happening.
    """

    def __init__(self, tg_user_id: int):
        self.tg_user_id = tg_user_id
        self.history: list[dict] = []
        self.thinking = CONFIG.get("enable_thinking", False)
        self.busy = False

    def trim(self):
        if len(self.history) > HISTORY_TURNS * 3:
            # Cut at a user boundary so a tool result is never left orphaned from the
            # assistant message it answers.
            cut = 0
            for i, m in enumerate(self.history[-HISTORY_TURNS * 2:], start=len(self.history) - HISTORY_TURNS * 2):
                if m.get("role") == "user":
                    cut = i
                    break
            self.history = self.history[cut:]


class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.base = f"{API}/bot{token}"
        self.offset = 0
        self.me: dict = {}
        self.sessions: dict[int, Session] = {}
        self.client: Optional[httpx.AsyncClient] = None
        # Strong references to in-flight handlers: asyncio only holds weak ones, and a
        # task that gets garbage collected mid-run just vanishes.
        self._tasks: set[asyncio.Task] = set()

    # --- transport -------------------------------------------------------

    async def call(self, method: str, **params):
        assert self.client is not None
        resp = await self.client.post(f"{self.base}/{method}", json=params)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"{method}: {data.get('description')}")
        return data["result"]

    async def send(self, chat_id, text, html_mode=False, silent=False):
        """Send text, splitting oversized replies. Returns the last message id."""
        last = None
        for part in chunk(text) or [""]:
            if not part.strip():
                continue
            params = {
                "chat_id": chat_id,
                "text": part,
                "disable_notification": silent,
                "link_preview_options": {"is_disabled": True},
            }
            if html_mode:
                params["parse_mode"] = "HTML"
            last = await self.call("sendMessage", **params)
        return last["message_id"] if last else None

    async def edit(self, chat_id, message_id, text, html_mode=False):
        try:
            await self.call(
                "editMessageText",
                chat_id=chat_id, message_id=message_id,
                text=text[:MAX_MESSAGE],
                **({"parse_mode": "HTML"} if html_mode else {}),
            )
        except RuntimeError as e:
            # "message is not modified" is normal when progress text repeats.
            if "not modified" not in str(e):
                raise

    # --- lifecycle -------------------------------------------------------

    async def run(self):
        async with httpx.AsyncClient(timeout=POLL_TIMEOUT + 15) as client:
            self.client = client
            self.me = await self.call("getMe")
            print(f"  telegram     : @{self.me.get('username')} connected", flush=True)
            self._announce_owner_code()

            while True:
                try:
                    updates = await self.call(
                        "getUpdates",
                        offset=self.offset,
                        timeout=POLL_TIMEOUT,
                        allowed_updates=["message"],
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    # Network blips and 409s (another instance polling) should not kill
                    # the task — back off and keep the web UI alive.
                    print(f"  telegram poll error: {e}", flush=True)
                    await asyncio.sleep(5)
                    continue

                for update in updates:
                    self.offset = max(self.offset, update["update_id"] + 1)
                    # Handled in its own task, not awaited here. A build driven from the
                    # phone can run for minutes; awaiting it inline would stall polling,
                    # so nobody else could be served and the per-user busy guard would
                    # never even get a message to reject.
                    task = asyncio.create_task(self._guarded(update))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)

    async def _guarded(self, update: dict):
        try:
            await self._handle(update)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"  telegram handler error: {e}", flush=True)

    def _announce_owner_code(self):
        """Print a one-time owner code when nobody owns this instance yet.

        Printing it to the terminal is the whole authentication story: only someone who
        can see this console can claim ownership.
        """
        auth.purge_expired()
        if auth.has_owner():
            return
        code = auth.new_code("owner", ttl_minutes=60)
        print("", flush=True)
        print("  ┌─ Telegram pairing ────────────────────────────────┐", flush=True)
        print(f"  │  Send this to @{self.me.get('username', 'your bot'):<34}│", flush=True)
        print("  │                                                   │", flush=True)
        print(f"  │      /pair {code:<39}│", flush=True)
        print("  │                                                   │", flush=True)
        print("  │  Valid 60 minutes, single use, grants owner.      │", flush=True)
        print("  └───────────────────────────────────────────────────┘", flush=True)
        print("", flush=True)

    def session(self, tg_user_id: int) -> Session:
        if tg_user_id not in self.sessions:
            self.sessions[tg_user_id] = Session(tg_user_id)
        return self.sessions[tg_user_id]

    # --- dispatch --------------------------------------------------------

    async def _handle(self, update: dict):
        msg = update.get("message")
        if not msg or "text" not in msg:
            return

        sender = msg.get("from") or {}
        tg_id = sender.get("id")
        if tg_id is None or sender.get("is_bot"):
            return

        chat_id = msg["chat"]["id"]
        text = msg["text"].strip()

        name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")]))
        account = auth.upsert_seen(tg_id, sender.get("username") or "", name or "?")

        await self._dispatch(chat_id, tg_id, account, text)

    async def _dispatch(self, chat_id, tg_id, account, text):
        cmd, _, rest = text.partition(" ")
        cmd, rest = cmd.lower().lstrip("/"), rest.strip()
        is_command = text.startswith("/")

        # --- open to everyone, authenticated or not ---
        if is_command and cmd in ("start", "help"):
            await self.send(chat_id, self._help(account), html_mode=True)
            return

        if is_command and cmd == "pair":
            if auth.is_authenticated(account):
                await self.send(chat_id, "You are already paired.")
                return
            if not rest:
                await self.send(chat_id, "Usage: /pair CODE-CODE")
                return
            ok, result = auth.redeem(rest, tg_id)
            if not ok:
                await self.send(chat_id, result)
                return
            await self.send(
                chat_id,
                f"Paired as <b>{html.escape(result)}</b>.\n\n"
                + ("You can set a workspace with /dir and I will edit files in it.\n"
                   if result == "owner" else "You have conversation access.\n")
                + "Send /help for what I can do.",
                html_mode=True,
            )
            return

        # --- the authorisation gate ---
        if not auth.is_authenticated(account):
            if account and account["role"] == "blocked":
                # Say nothing useful to a blocked id.
                return
            auth.audit(tg_id, "unauthorised", text[:80])
            await self.send(
                chat_id,
                "This samsu instance is private.\n\n"
                "If you are meant to have access, ask whoever is running it for a "
                "pairing code, then send:  /pair CODE-CODE",
            )
            return

        if is_command:
            await self._command(chat_id, tg_id, account, cmd, rest)
            return

        await self._converse(chat_id, tg_id, account, text)

    def _help(self, account) -> str:
        if not auth.is_authenticated(account):
            return (
                "<b>samsu</b> — a local Qwen3-4B assistant running on someone's laptop.\n\n"
                "This instance is private. To get in, send:\n"
                "<code>/pair CODE-CODE</code>"
            )

        lines = [
            "<b>samsu</b> — local Qwen3-4B, running offline on the owner's machine.\n",
            "Just send a message to talk to it.\n",
            "<b>Conversation</b>",
            "/new — start a fresh conversation",
            "/think — toggle extended reasoning (slower, better on hard problems)",
            "/status — model, workspace and role",
        ]
        if auth.may_administer(account):
            lines += [
                "",
                "<b>Workspace</b> (owner)",
                "/dir &lt;path&gt; — give me a directory to edit files in",
                "/dir off — drop it, conversation only",
                "/pwd — where I am working",
                "/ls [path] — list files directly, without asking the model",
                "",
                "<b>People</b> (owner)",
                "/invite — mint a 15-minute pairing code for someone else",
                "/who — list paired accounts",
                "/block &lt;id&gt; · /unblock &lt;id&gt;",
                "/audit — recent access events",
            ]
        return "\n".join(lines)

    # --- commands --------------------------------------------------------

    async def _command(self, chat_id, tg_id, account, cmd, rest):
        session = self.session(tg_id)

        if cmd == "new":
            session.history.clear()
            auth.set_chat(tg_id, None)
            await self.send(chat_id, "Fresh conversation.")
            return

        if cmd == "think":
            session.thinking = not session.thinking
            await self.send(
                chat_id,
                f"Extended reasoning {'on — slower, better on hard problems' if session.thinking else 'off'}.",
            )
            return

        if cmd == "status":
            await self.send(chat_id, await self._status(account, session), html_mode=True)
            return

        # --- owner only ---
        if cmd in ("dir", "pwd", "ls", "invite", "who", "block", "unblock", "audit"):
            if not auth.may_administer(account):
                auth.audit(tg_id, "denied", cmd)
                await self.send(chat_id, "That command is owner-only.")
                return

        if cmd == "dir":
            await self._cmd_dir(chat_id, tg_id, rest)
            return

        if cmd == "pwd":
            await self.send(chat_id, account["workspace"] or "No workspace — conversation only.")
            return

        if cmd == "ls":
            if not account["workspace"]:
                await self.send(chat_id, "No workspace set. Use /dir <path> first.")
                return
            try:
                ws = Workspace(account["workspace"])
                listing = await asyncio.to_thread(ws.list_dir, rest or ".")
                await self.send(chat_id, listing)
            except WorkspaceError as e:
                await self.send(chat_id, str(e))
            return

        if cmd == "invite":
            code = auth.new_code("user")
            auth.audit(tg_id, "invited", code)
            await self.send(
                chat_id,
                f"Pairing code (15 minutes, single use, conversation-only access):\n\n"
                f"<code>{code}</code>\n\n"
                f"They send:  /pair {code}",
                html_mode=True,
            )
            return

        if cmd == "who":
            rows = auth.list_accounts()
            if not rows:
                await self.send(chat_id, "Nobody has contacted the bot yet.")
                return
            lines = ["<b>Accounts</b>"]
            for r in rows:
                who = f"@{r['username']}" if r["username"] else r["display_name"]
                lines.append(
                    f"<code>{r['tg_user_id']}</code> · {html.escape(who)} · <b>{r['role']}</b>"
                    + (f" · {html.escape(r['workspace'])}" if r["workspace"] else "")
                )
            await self.send(chat_id, "\n".join(lines), html_mode=True)
            return

        if cmd in ("block", "unblock"):
            if not rest.lstrip("-").isdigit():
                await self.send(chat_id, f"Usage: /{cmd} <telegram user id>   (see /who)")
                return
            target = int(rest)
            if target == tg_id:
                await self.send(chat_id, "You cannot block yourself.")
                return
            if not auth.get_account(target):
                await self.send(chat_id, "No such account.")
                return
            auth.set_role(target, "blocked" if cmd == "block" else "user")
            auth.audit(tg_id, cmd, str(target))
            self.sessions.pop(target, None)
            await self.send(chat_id, f"{target} is now {'blocked' if cmd == 'block' else 'a user'}.")
            return

        if cmd == "audit":
            rows = auth.recent_audit(15)
            if not rows:
                await self.send(chat_id, "Nothing logged yet.")
                return
            lines = [
                f"{r['created_at'][11:]} · {r['tg_user_id']} · {r['action']}"
                + (f" · {r['detail'][:40]}" if r["detail"] else "")
                for r in rows
            ]
            await self.send(chat_id, "\n".join(lines))
            return

        await self.send(chat_id, f"Unknown command: /{cmd}\nSend /help.")

    async def _cmd_dir(self, chat_id, tg_id, rest):
        if rest.lower() in ("off", "none", ""):
            auth.set_workspace(tg_id, None)
            auth.audit(tg_id, "workspace_off")
            await self.send(chat_id, "Workspace dropped — conversation only, no file access.")
            return

        # Relative paths are resolved against the samsu directory, so "apps/asteroids"
        # works from the phone without typing an absolute path.
        candidate = Path(rest).expanduser()
        if not candidate.is_absolute():
            candidate = ROOT / candidate

        try:
            ws = Workspace(str(candidate))
        except WorkspaceError as e:
            await self.send(chat_id, str(e))
            return

        auth.set_workspace(tg_id, str(ws.root))
        auth.audit(tg_id, "workspace_set", str(ws.root))
        await self.send(
            chat_id,
            f"Workspace: <code>{html.escape(str(ws.root))}</code>\n\n"
            "I can create, edit and delete files in there without asking, and cannot "
            "touch anything outside it. There is no undo — commit first.",
            html_mode=True,
        )

    async def _status(self, account, session) -> str:
        try:
            r = await self.client.get(f"{LLAMA_URL}/health", timeout=4.0)
            model = "up" if r.status_code == 200 else f"http {r.status_code}"
        except Exception:
            model = "unreachable"

        return "\n".join([
            "<b>samsu status</b>",
            f"model server : {model}",
            f"context      : {CONFIG['n_ctx']} tokens",
            f"role         : {account['role']}",
            f"workspace    : {html.escape(account['workspace']) if account['workspace'] else 'none (chat only)'}",
            f"file tools   : {'on' if auth.may_use_tools(account) else 'off'}",
            f"reasoning    : {'on' if session.thinking else 'off'}",
            f"history      : {len(session.history)} messages",
        ])

    # --- conversation ----------------------------------------------------

    async def _converse(self, chat_id, tg_id, account, text):
        session = self.session(tg_id)

        if session.busy:
            await self.send(chat_id, "Still working on the last one — one at a time.")
            return

        workspace = None
        if auth.may_use_tools(account):
            try:
                workspace = Workspace(account["workspace"])
            except WorkspaceError as e:
                await self.send(chat_id, f"Workspace is gone: {e}\nSet a new one with /dir.")
                auth.set_workspace(tg_id, None)
                return

        session.busy = True
        session.history.append({"role": "user", "content": text})
        session.trim()

        # Mirror into a real samsu chat so the same conversation is visible in the
        # browser while the phone is driving it.
        samsu_chat = self._ensure_chat(tg_id, account, text)
        db.add_message(samsu_chat, "user", text)

        status_id = await self.send(chat_id, "· thinking…")
        started = time.monotonic()
        steps: list[str] = []
        last_edit = 0.0
        answer = ""
        warnings: list[str] = []

        async def show(force=False):
            nonlocal last_edit
            now = time.monotonic()
            if not force and now - last_edit < EDIT_INTERVAL:
                return
            last_edit = now
            tail = steps[-8:]
            body = "\n".join(tail) if tail else "· thinking…"
            await self.edit(chat_id, status_id, f"{body}\n\n⏱ {now - started:.0f}s")

        try:
            async for kind, payload in run_agent(
                self.client, session.history, workspace, thinking=session.thinking,
            ):
                if kind == "tool":
                    steps.append(f"→ {payload['name']}")
                    await show()
                elif kind == "result":
                    mark = "✓" if payload["ok"] else "✗"
                    if steps:
                        steps[-1] = f"{mark} {payload['name']} — {payload['summary']}"
                    await show()
                elif kind == "warn":
                    warnings.append(payload)
                    steps.append(f"⚠ {payload}")
                    await show(force=True)
                elif kind == "text":
                    answer = payload
                elif kind == "done":
                    pass
        except Exception as e:
            answer = f"Something broke: {e}"
            auth.audit(tg_id, "error", str(e)[:200])
        finally:
            session.busy = False

        elapsed = time.monotonic() - started

        if steps:
            summary = "\n".join(steps[-12:]) + f"\n\n⏱ {elapsed:.0f}s"
            await self.edit(chat_id, status_id, summary)
        else:
            # Nothing to show but the timer — delete the placeholder rather than leave
            # a stray "thinking…" above the answer.
            try:
                await self.call("deleteMessage", chat_id=chat_id, message_id=status_id)
            except RuntimeError:
                pass

        if not answer:
            answer = "(no reply — try rephrasing, or /new to reset the conversation)"

        await self.send(chat_id, answer)
        db.add_message(samsu_chat, "assistant", answer)
        auth.audit(tg_id, "turn", f"{elapsed:.0f}s, {len(steps)} tool calls")

    def _ensure_chat(self, tg_id, account, first_message) -> str:
        """The samsu chat this Telegram user's conversation is mirrored into."""
        existing = account.get("chat_id")
        if existing and db.get_chat(existing):
            return existing

        title = " ".join(first_message.split()[:6])[:60] or "Telegram"
        chat = db.create_chat(f"📱 {title}")
        auth.set_chat(tg_id, chat["id"])
        return chat["id"]


# --- integration ---------------------------------------------------------


async def start(app_state) -> Optional[asyncio.Task]:
    """Start the bot as a background task, or explain why it is not starting.

    Never raises: a missing or bad token must not stop the web UI from coming up.
    """
    token = load_token()
    if not token:
        print("  telegram     : disabled (no TELEGRAM_BOT_TOKEN)", flush=True)
        return None

    auth.init()
    bot = TelegramBot(token)
    app_state.telegram_bot = bot

    async def supervised():
        try:
            await bot.run()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"  telegram     : stopped — {e}", flush=True)

    return asyncio.create_task(supervised(), name="telegram-bot")
