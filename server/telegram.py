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

from . import auth, clarify, db, voice
from .agent import run_agent
from .clarify import ClarifySession
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
        # Requirement clarification runs beside the conversation rather than inside it:
        # while a spec session is open, plain messages are answers to its question, not
        # prompts for the agent. See server/clarify.py.
        self.spec: Optional[ClarifySession] = None
        self.awaiting_request = False
        self.speak = bool(CONFIG.get("voice_speak_replies", True))

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

    async def fetch_file(self, file_id: str) -> bytes:
        """Download an attachment. getFile yields a path, which hangs off a second host."""
        assert self.client is not None
        info = await self.call("getFile", file_id=file_id)
        path = info.get("file_path")
        if not path:
            raise RuntimeError("getFile returned no path")
        resp = await self.client.get(f"{API}/file/bot{self.token}/{path}", timeout=60.0)
        resp.raise_for_status()
        return resp.content

    async def send_voice(self, chat_id, ogg: bytes, caption: Optional[str] = None):
        """sendVoice needs multipart, not the JSON body `call` uses."""
        assert self.client is not None
        files = {"voice": ("reply.opus", ogg, "audio/ogg")}
        data = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption[:1024]
        resp = await self.client.post(f"{self.base}/sendVoice", data=data, files=files,
                                      timeout=60.0)
        body = resp.json()
        if not body.get("ok"):
            raise RuntimeError(f"sendVoice: {body.get('description')}")
        return body["result"]["message_id"]

    async def say(self, chat_id, session, text: str):
        """Speak a reply, if speech is on and available.

        Never fatal: a voice note is an enhancement to the text that was already sent, so
        a synthesis failure is logged and swallowed rather than losing the reply.
        """
        if not (session.speak and voice.status()["speaking"]):
            return
        try:
            ogg = await voice.synthesize(text)
            if ogg:
                await self.send_voice(chat_id, ogg)
        except (voice.VoiceError, RuntimeError, OSError) as e:
            print(f"  telegram tts error: {e}", flush=True)

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
            vstate = voice.status()
            print("  voice        : "
                  + (f"{vstate['model']} in"
                     + (", say out" if vstate["speaking"] else ", no speech out")
                     if vstate["listening"]
                     else "unavailable — " + ", ".join(vstate["missing"])), flush=True)
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
        if not msg:
            return

        sender = msg.get("from") or {}
        tg_id = sender.get("id")
        if tg_id is None or sender.get("is_bot"):
            return

        chat_id = msg["chat"]["id"]
        name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")]))
        account = auth.upsert_seen(tg_id, sender.get("username") or "", name or "?")

        text = (msg.get("text") or "").strip()
        spoken = False

        if not text:
            media = msg.get("voice") or msg.get("audio") or msg.get("video_note")
            if not media:
                return

            # Authorisation happens *before* the download, not in _dispatch with
            # everything else. Transcription costs real CPU on a shared 8 GB machine, and
            # anyone who finds the bot can send audio to it — so a stranger must not be
            # able to make it fetch and decode a file at all.
            if not auth.is_authenticated(account):
                if not (account and account["role"] == "blocked"):
                    auth.audit(tg_id, "unauthorised", "voice message")
                    await self.send(chat_id, "This samsu instance is private. Send /help.")
                return

            text = await self._transcribe(chat_id, tg_id, media)
            if not text:
                return
            spoken = True

        await self._dispatch(chat_id, tg_id, account, text, spoken=spoken)

    async def _transcribe(self, chat_id, tg_id, media: dict) -> str:
        """Voice note to text, with the transcript echoed back to the sender.

        Showing what was heard is not a nicety. A misheard feature request produces a
        confidently wrong clarifying question, and the user has no way to tell that is
        what happened unless the transcript is in front of them.
        """
        state = voice.status()
        if not state["enabled"]:
            await self.send(chat_id, "Voice is switched off on this instance.")
            return ""
        if not state["listening"]:
            await self.send(
                chat_id,
                "I cannot transcribe audio yet — missing: " + ", ".join(state["missing"])
                + "\n\nInstall with:  brew install whisper-cpp opus-tools",
            )
            return ""

        limit = int(CONFIG.get("voice_max_seconds", 180))
        duration = int(media.get("duration") or 0)
        if duration > limit:
            await self.send(
                chat_id,
                f"That is {duration}s of audio and I cap it at {limit}s. "
                "Send it in shorter pieces.",
            )
            return ""

        status_id = await self.send(chat_id, "🎧 listening…")
        started = time.monotonic()
        try:
            audio = await self.fetch_file(media["file_id"])
            suffix = ".oga" if media.get("mime_type", "").endswith("ogg") else ".bin"
            text = await voice.transcribe(audio, suffix=suffix)
        except voice.VoiceError as e:
            await self.edit(chat_id, status_id, f"Could not transcribe that: {e}")
            auth.audit(tg_id, "voice_failed", str(e)[:120])
            return ""
        except Exception as e:
            await self.edit(chat_id, status_id, f"Could not fetch that audio: {e}")
            return ""

        elapsed = time.monotonic() - started
        if not text:
            await self.edit(chat_id, status_id, "I could not make out any speech in that.")
            return ""

        await self.edit(
            chat_id, status_id,
            f"🎤 <i>heard:</i> “{html.escape(text)}”\n<i>{duration}s audio · "
            f"transcribed in {elapsed:.1f}s</i>",
            html_mode=True,
        )
        auth.audit(tg_id, "voice_in", f"{duration}s, {len(text)} chars")
        return text

    async def _dispatch(self, chat_id, tg_id, account, text, spoken: bool = False):
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
            await self._command(chat_id, tg_id, account, cmd, rest, spoken=spoken)
            return

        # An open spec session captures plain messages: they are answers to the question
        # it is waiting on, not new prompts for the agent.
        session = self.session(tg_id)
        if session.spec is not None and not session.spec.done:
            await self._clarify_answer(chat_id, tg_id, account, text, spoken)
            return
        if session.awaiting_request:
            session.awaiting_request = False
            await self._clarify_start(chat_id, tg_id, account, text, spoken)
            return

        await self._converse(chat_id, tg_id, account, text, spoken=spoken)

    def _help(self, account) -> str:
        if not auth.is_authenticated(account):
            return (
                "<b>samsu</b> — a local Qwen3-4B assistant running on someone's laptop.\n\n"
                "This instance is private. To get in, send:\n"
                "<code>/pair CODE-CODE</code>"
            )

        state = voice.status()
        lines = [
            "<b>samsu</b> — local Qwen3-4B, running offline on the owner's machine.\n",
            "Send a message to talk to it"
            + (", or hold the microphone and speak.\n" if state["listening"] else ".\n"),
            "<b>Clarify a feature</b>",
            "/spec — describe a feature; I ask what I still need to know, then write the spec",
            "/spec &lt;text&gt; — same, with the request inline",
            "/build — implement the agreed spec (needs a workspace)",
            "/cancel — drop the spec session",
            "",
            "<b>Conversation</b>",
            "/new — start a fresh conversation",
            "/think — toggle extended reasoning (slower, better on hard problems)",
            "/voice on|off — spoken replies as voice notes",
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

    async def _command(self, chat_id, tg_id, account, cmd, rest, spoken: bool = False):
        session = self.session(tg_id)

        if cmd == "new":
            session.history.clear()
            session.spec = None
            session.awaiting_request = False
            auth.set_chat(tg_id, None)
            await self.send(chat_id, "Fresh conversation.")
            return

        if cmd == "spec":
            await self._cmd_spec(chat_id, tg_id, account, rest, spoken)
            return

        if cmd == "cancel":
            if session.spec is None:
                await self.send(chat_id, "Nothing to cancel.")
                return
            session.spec = None
            session.awaiting_request = False
            await self.send(chat_id, "Spec session dropped.")
            return

        if cmd == "build":
            await self._cmd_build(chat_id, tg_id, account)
            return

        if cmd == "voice":
            if not voice.status()["speaking"] and rest.lower() != "off":
                await self.send(chat_id, "Spoken replies are not available: "
                                         + ", ".join(voice.status()["missing"] or ["disabled in config"]))
                return
            session.speak = rest.lower() != "off" if rest else not session.speak
            await self.send(
                chat_id,
                f"Spoken replies {'on — I will send a voice note with each answer' if session.speak else 'off'}.",
            )
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

        vstate = voice.status()
        if session.spec is None:
            spec_line = "none"
        elif session.spec.done:
            spec_line = "agreed — /build to implement"
        else:
            spec_line = (f"open · {session.spec.asked} answered, "
                         f"waiting on {session.spec.pending['topic'] if session.spec.pending else '—'}")

        return "\n".join([
            "<b>samsu status</b>",
            f"model server : {model}",
            f"context      : {CONFIG['n_ctx']} tokens",
            f"role         : {account['role']}",
            f"workspace    : {html.escape(account['workspace']) if account['workspace'] else 'none (chat only)'}",
            f"file tools   : {'on' if auth.may_use_tools(account) else 'off'}",
            f"reasoning    : {'on' if session.thinking else 'off'}",
            f"voice in     : {vstate['model'] or 'unavailable'}"
            + (f" ({', '.join(vstate['missing'])})" if vstate["missing"] else ""),
            f"voice out    : {'on' if session.speak and vstate['speaking'] else 'off'}",
            f"spec session : {spec_line}",
            f"history      : {len(session.history)} messages",
        ])

    # --- requirement clarification ---------------------------------------

    async def _cmd_spec(self, chat_id, tg_id, account, rest, spoken):
        session = self.session(tg_id)
        if session.busy:
            await self.send(chat_id, "Still working on the last one — one at a time.")
            return

        if rest.strip():
            await self._clarify_start(chat_id, tg_id, account, rest.strip(), spoken)
            return

        session.spec = None
        session.awaiting_request = True
        await self.send(
            chat_id,
            "Tell me the feature you want — hold the microphone and just say it, or type it.\n\n"
            "I will ask up to "
            f"{CONFIG.get('clarify_max_questions', 4)} questions before I write anything "
            "down, so we agree on what it means first.",
        )

    async def _clarify_start(self, chat_id, tg_id, account, request, spoken):
        session = self.session(tg_id)
        if session.busy:
            await self.send(chat_id, "Still working on the last one — one at a time.")
            return

        session.spec = ClarifySession(request, spoken=spoken)
        session.busy = True

        samsu_chat = self._ensure_chat(tg_id, account, request)
        db.add_message(
            samsu_chat, "user",
            f"**{'🎤 Spoken' if spoken else '📝 Typed'} feature request**\n\n“{request}”",
        )
        auth.audit(tg_id, "clarify_start", request[:80])

        status_id = await self.send(chat_id, "· working out what I still need to know…")
        try:
            step = await session.spec.begin(self.client)
        except clarify.ClarifyError as e:
            session.spec = None
            session.busy = False
            await self.edit(chat_id, status_id, f"Could not start: {e}")
            return
        finally:
            session.busy = False

        try:
            await self.call("deleteMessage", chat_id=chat_id, message_id=status_id)
        except RuntimeError:
            pass
        await self._clarify_emit(chat_id, tg_id, account, session, step)

    async def _clarify_answer(self, chat_id, tg_id, account, reply, spoken):
        session = self.session(tg_id)
        if session.busy:
            await self.send(chat_id, "One moment — still thinking about the last answer.")
            return

        spec = session.spec
        samsu_chat = self._ensure_chat(tg_id, account, spec.request)
        db.add_message(samsu_chat, "user",
                       f"{'🎤 ' if spoken else ''}“{reply}”")

        session.busy = True
        status_id = await self.send(chat_id, "· noting that…")
        try:
            step = await spec.answer(self.client, reply)
        except clarify.ClarifyError as e:
            await self.edit(chat_id, status_id, f"That did not go through: {e}\nTry again, or /cancel.")
            return
        finally:
            session.busy = False

        try:
            await self.call("deleteMessage", chat_id=chat_id, message_id=status_id)
        except RuntimeError:
            pass
        await self._clarify_emit(chat_id, tg_id, account, session, step)

    async def _clarify_emit(self, chat_id, tg_id, account, session, step):
        """Render one step of the loop: another question, or the finished specification."""
        spec = session.spec
        samsu_chat = self._ensure_chat(tg_id, account, spec.request)

        if step["kind"] == "question":
            n, total = step["index"], max(step["total"], step["index"])
            header = f"❓ Clarification {n} of {total} · {step['topic']}"
            body = step["question"]
            why = step["why"]

            await self.send(
                chat_id,
                f"<b>{html.escape(header)}</b>\n\n{html.escape(body)}"
                + (f"\n\n<i>Why I am asking: {html.escape(why)}</i>" if why else "")
                + "\n\n<i>Answer by voice or text · /cancel to stop</i>",
                html_mode=True,
            )
            db.add_message(
                samsu_chat, "assistant",
                f"**{header}**\n\n{body}" + (f"\n\n_Why I am asking: {why}_" if why else ""),
            )
            auth.audit(tg_id, "clarify_question", f"{step['topic']}: {body[:60]}")
            await self.say(chat_id, session, body)
            return

        # Finished — the specification, and what it was built from.
        rendered = spec.render_spec()
        deferred = sum(1 for e in spec.exchanges if e["deferred"])
        summary = (
            f"✅ Specification agreed after {len(spec.exchanges)} "
            f"clarification{'s' if len(spec.exchanges) != 1 else ''}"
            + (f", {deferred} left to me" if deferred else "")
        )

        await self.send(chat_id, f"{summary}\n\n{rendered}")
        db.add_message(samsu_chat, "assistant", f"**{summary}**\n\n{rendered}")
        auth.audit(tg_id, "clarify_done",
                   f"{len(spec.exchanges)} questions, {deferred} deferred")

        if auth.may_use_tools(account):
            await self.send(chat_id, "Send /build and I will implement exactly that, "
                                     "or /cancel to drop it.")
        else:
            await self.send(chat_id, "Set a workspace with /dir and I can build it, "
                                     "or /cancel to drop it.")

        spoken_summary = (
            f"{spec.spec.get('title') or 'The specification'} is agreed. "
            f"{spec.spec.get('goal') or ''} "
            f"I asked {len(spec.exchanges)} clarifying questions."
        )
        await self.say(chat_id, session, spoken_summary)

    async def _cmd_build(self, chat_id, tg_id, account):
        session = self.session(tg_id)
        spec = session.spec
        if spec is None or not spec.done:
            await self.send(chat_id, "No agreed specification yet. Start one with /spec.")
            return
        if not auth.may_use_tools(account):
            await self.send(chat_id, "I need a workspace before I can build. Use /dir <path>.")
            return

        prompt = spec.build_prompt()
        session.spec = None            # the spec has become the instruction
        await self.send(chat_id, "Building to that specification…")
        await self._converse(chat_id, tg_id, account, prompt, mirror_as="🔨 Build to the agreed specification")

    # --- conversation ----------------------------------------------------

    async def _converse(self, chat_id, tg_id, account, text, spoken: bool = False,
                        mirror_as: Optional[str] = None):
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
        samsu_chat = self._ensure_chat(tg_id, account, mirror_as or text)
        db.add_message(samsu_chat, "user",
                       mirror_as or (f"🎤 “{text}”" if spoken else text))

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
        await self.say(chat_id, session, answer)

    def _ensure_chat(self, tg_id, account, first_message) -> str:
        """The samsu chat this Telegram user's conversation is mirrored into.

        The binding is read back from the database rather than from `account`. A
        clarification turn calls this more than once, and the caller's copy of the
        account still says `chat_id: None` after the first call created the chat — which
        produced a second, near-identical 📱 chat for every spec session.
        """
        current = auth.get_account(tg_id) or account
        existing = current.get("chat_id")
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
