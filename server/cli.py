"""samsu CLI — chat with the local model, optionally with file tools scoped to one directory.

On launch it asks for a working directory. Give it one and the model gets file tools
confined to that tree; press Enter to decline and it behaves exactly like the web chat,
with no filesystem access exposed at all.
"""

import argparse
import itertools
import json
import os
import sys
import threading
import time

import httpx

from .config import CONFIG, LLAMA_URL
from .workspace import DESTRUCTIVE, TOOL_SCHEMAS, Workspace, WorkspaceError

REQUEST_TIMEOUT = 600.0

# Upper bound on tool calls per turn. Autonomous work needs a lot of them, but this
# still stops a confused model from looping forever.
MAX_TOOL_ROUNDS = int(CONFIG.get("max_tool_rounds", 40))

# Output budget per model call. Higher than the web UI's because a write_file call must
# fit an entire file into a JSON string; too low and it truncates mid-string.
CLI_MAX_TOKENS = int(CONFIG.get("cli_max_tokens", CONFIG["max_tokens"]))

C = {
    "dim": "\033[2m", "b": "\033[1m", "r": "\033[0m",
    "orange": "\033[38;5;173m", "green": "\033[32m",
    "red": "\033[31m", "yellow": "\033[33m", "blue": "\033[38;5;110m",
}
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C = {k: "" for k in C}


def paint(s, *styles):
    return "".join(C[x] for x in styles) + s + C["r"]


SYSTEM_CHAT = CONFIG["system_prompt"]

SYSTEM_TOOLS = """You are samsu, a coding assistant working inside a single directory.

Workspace root: {root}

Rules:
- All paths are RELATIVE to the workspace root. Never use absolute paths or '..'.
- Use the tools to inspect and change files. Do not describe what you would do — do it.
- Read a file before editing it.
- Keep every write_file call SHORT — at most about 80 lines. A long file must be built
  up across several calls: write a small first version, then extend it with edit_file.
  One oversized call gets truncated and fails.
- Work on ONE file at a time.
- After finishing, reply with a short plain-text summary of what you changed.
"""

# llama-server returns 500 when a tool call's arguments aren't valid JSON — which in
# practice means generation hit the token limit part-way through a long string.
TRUNCATION_HINT = (
    "Your previous tool call was cut off because it was too long, so nothing was "
    "written. Retry with a much smaller write_file call — at most 40 lines. "
    "Build the file up over several calls instead of one."
)


# --- spinner -------------------------------------------------------------

class Spinner:
    """Shows the model is working, with elapsed time."""

    def __init__(self, label="thinking"):
        self.label = label
        self._stop = threading.Event()
        self._t = None

    def __enter__(self):
        if not sys.stdout.isatty():
            return self
        self._t = threading.Thread(target=self._spin, daemon=True)
        self._t.start()
        return self

    def _spin(self):
        t0 = time.time()
        for ch in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
            if self._stop.is_set():
                break
            sys.stdout.write(
                f"\r{paint(ch, 'orange')} {paint(self.label, 'dim')} "
                f"{paint(f'{time.time() - t0:.1f}s', 'dim')}  "
            )
            sys.stdout.flush()
            time.sleep(0.08)

    def __exit__(self, *a):
        self._stop.set()
        if self._t:
            self._t.join(timeout=0.5)
        if sys.stdout.isatty():
            sys.stdout.write("\r" + " " * 48 + "\r")
            sys.stdout.flush()


# --- model ---------------------------------------------------------------

def count_tokens(client, text):
    try:
        r = client.post(f"{LLAMA_URL}/tokenize", json={"content": text}, timeout=15.0)
        return len(r.json().get("tokens", []))
    except Exception:
        return len(text) // 3 + 1


def trim(client, messages):
    """Drop oldest turns to fit the window, never orphaning a tool result.

    A `tool` message is only valid immediately after the `assistant` message whose
    tool_calls it answers, so cuts are only made at user-message boundaries.
    """
    budget = max(512, CONFIG["n_ctx"] - CLI_MAX_TOKENS - 512)
    system, rest = messages[0], messages[1:]
    total = count_tokens(client, json.dumps(messages))
    if total <= budget:
        return messages

    while rest and total > budget:
        cut = 1
        while cut < len(rest) and rest[cut].get("role") != "user":
            cut += 1
        dropped, rest = rest[:cut], rest[cut:]
        total -= count_tokens(client, json.dumps(dropped))
        if cut >= len(rest) + cut:      # nothing left to drop
            break
    return [system] + rest


class Truncated(Exception):
    """Generation ran out of tokens mid tool-call, so the arguments are unparseable."""


def chat(client, messages, tools, thinking):
    payload = {
        "messages": messages,
        "stream": False,
        "temperature": CONFIG["temperature"],
        "top_p": CONFIG["top_p"],
        "max_tokens": CLI_MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": thinking},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    resp = client.post(
        f"{LLAMA_URL}/v1/chat/completions", json=payload, timeout=REQUEST_TIMEOUT
    )

    if resp.status_code == 500 and "tool call arguments" in resp.text:
        raise Truncated()
    resp.raise_for_status()

    data = resp.json()
    choice = data["choices"][0]
    # Cut off mid tool-call, but llama-server happened to parse it anyway — the
    # arguments are still incomplete, so treat it the same way.
    if choice.get("finish_reason") == "length" and choice["message"].get("tool_calls"):
        raise Truncated()
    return choice["message"], data.get("timings", {})


# --- tool execution ------------------------------------------------------

# Required arguments per tool, derived from the schemas so the two can't drift apart.
SIGNATURES = {
    t["function"]["name"]: t["function"]["parameters"].get("required", [])
    for t in TOOL_SCHEMAS
}


def confirm(ws, name, args, auto_yes):
    if name not in DESTRUCTIVE or auto_yes:
        return True
    print(f"  {paint('⚠', 'yellow')} {paint(ws.describe(name, args), 'b')}")
    if name == "write_file":
        preview = (args.get("content") or "").splitlines()[:6]
        for line in preview:
            print(paint(f"      │ {line[:96]}", "dim"))
        if len((args.get("content") or "").splitlines()) > 6:
            print(paint("      │ …", "dim"))
    try:
        return input(f"    {paint('Proceed? [y/N] ', 'yellow')}").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def run_tools(ws, calls, auto_yes):
    """Execute tool calls, returning the `tool` role messages to feed back."""
    out = []
    for call in calls:
        fn = call.get("function", {})
        name = fn.get("name", "")
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw)
            bad_args = not isinstance(args, dict)
        except json.JSONDecodeError:
            args, bad_args = {}, True

        if bad_args:
            # Almost always a truncated argument string. Say so, so the model retries
            # smaller instead of seeing a confusing Python TypeError.
            print(f"  {paint('✗', 'red')} {paint(name, 'blue')} "
                  f"{paint('arguments were cut off', 'red')}")
            out.append({
                "role": "tool", "tool_call_id": call.get("id", ""), "name": name,
                "content": f"Error: {TRUNCATION_HINT}",
            })
            continue

        if not confirm(ws, name, args, auto_yes):
            result = "User declined this operation."
            print(f"  {paint('✗', 'red')} {paint('declined', 'dim')} {name}")
        else:
            try:
                result = ws.run(name, args)
                first = result.splitlines()[0] if result else ""
                print(f"  {paint('✓', 'green')} {paint(name, 'blue')} {paint(first[:88], 'dim')}")
            except WorkspaceError as e:
                result = f"Error: {e}"
                print(f"  {paint('✗', 'red')} {paint(name, 'blue')} {paint(str(e)[:88], 'red')}")
            except TypeError:
                expected = ", ".join(SIGNATURES.get(name, []))
                result = (f"Error: {name} needs these arguments: {expected}. "
                          f"You supplied: {sorted(args) or 'none'}. Call it again with all of them.")
                print(f"  {paint('✗', 'red')} {paint(name, 'blue')} "
                      f"{paint(f'missing arguments (needs {expected})', 'red')}")

        out.append({
            "role": "tool",
            "tool_call_id": call.get("id", ""),
            "name": name,
            "content": result,
        })
    return out


# --- REPL ----------------------------------------------------------------

HELP = f"""
{paint('Commands', 'b')}
  /help              show this
  /dir <path>        set or change the working directory
  /dir off           drop the workspace (file tools disabled)
  /pwd               show the current workspace
  /ls [path]         list files without asking the model
  /think             toggle extended reasoning (slower, better on hard problems)
  /auto              run without confirmations (default)
  /ask               confirm before overwriting, editing, deleting or moving
  /clear             forget the conversation so far
  /exit  /quit       leave  (Ctrl-D also works, Ctrl-C aborts a running turn)
"""


def banner(ws, thinking, auto_yes):
    print(paint("\n  samsu", "b", "orange") + paint("  ·  Qwen3-4B  ·  offline", "dim"))
    if ws:
        print(paint(f"  workspace: {ws.root}", "dim"))
        if auto_yes:
            # Files here can be overwritten or deleted with no prompt, so say so
            # plainly rather than burying it — it is the one thing worth knowing.
            print(paint("  AUTOMATIC — creates, edits and deletes here without asking",
                        "yellow"))
            print(paint("  /ask to re-enable confirmations", "dim"))
        else:
            print(paint("  file tools: ON   destructive ops: ask first", "dim"))
    else:
        print(paint("  workspace: none — chat only, no file access", "dim"))
    print(paint(f"  thinking: {'on' if thinking else 'off'}    /help for commands\n", "dim"))


def ask_for_dir(preset):
    if preset:
        return Workspace(preset)
    print(paint("\n  samsu", "b", "orange") + paint("  ·  Qwen3-4B  ·  offline", "dim"))
    print(paint("\n  Give samsu a directory to work in, or press Enter to skip.", "dim"))
    print(paint("  It creates, edits and deletes files inside that directory without", "dim"))
    print(paint("  asking — and cannot touch anything outside it.", "dim"))
    print(paint("  Press Enter for chat-only, with no file access at all.\n", "dim"))
    while True:
        try:
            raw = input(paint("  directory (Enter to skip) › ", "orange")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not raw:
            return None
        try:
            return Workspace(raw)
        except WorkspaceError as e:
            print(paint(f"  {e}\n", "red"))


def main():
    ap = argparse.ArgumentParser(prog="samsu", description="samsu CLI")
    ap.add_argument("-d", "--dir", help="workspace directory (skips the prompt)")
    ap.add_argument("--no-dir", action="store_true", help="start chat-only, no prompt")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="auto-approve everything (default; set auto_approve in config.json)")
    ap.add_argument("--ask", action="store_true",
                    help="confirm before overwriting, editing, deleting or moving")
    ap.add_argument("--think", action="store_true", help="start with reasoning enabled")
    args = ap.parse_args()

    client = httpx.Client()
    try:
        client.get(f"{LLAMA_URL}/health", timeout=3.0).raise_for_status()
    except Exception:
        print(paint(f"\n  Cannot reach llama-server at {LLAMA_URL}", "red"))
        print(paint("  Start it with:  ./samsu serve\n", "dim"))
        return 1

    try:
        ws = None if args.no_dir else ask_for_dir(args.dir)
    except WorkspaceError as e:
        print(paint(f"  {e}", "red"))
        return 1

    # --ask wins over -y; otherwise the config default applies.
    auto_yes = False if args.ask else (args.yes or bool(CONFIG.get("auto_approve", True)))
    thinking = args.think
    banner(ws, thinking, auto_yes)
    history = []

    while True:
        try:
            line = input(paint("› ", "orange", "b")).strip()
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print(paint("\n  (Ctrl-D or /exit to leave)", "dim"))
            continue

        if not line:
            continue

        # --- slash commands ---
        if line.startswith("/"):
            cmd, _, rest = line[1:].partition(" ")
            cmd, rest = cmd.lower(), rest.strip()

            if cmd in ("exit", "quit", "q"):
                break
            if cmd == "help":
                print(HELP)
            elif cmd == "dir":
                if rest.lower() in ("off", "none"):
                    ws, history = None, []
                    print(paint("  workspace dropped — chat only\n", "dim"))
                elif rest:
                    try:
                        ws, history = Workspace(rest), []
                        print(paint(f"  workspace: {ws.root}\n", "green"))
                    except WorkspaceError as e:
                        print(paint(f"  {e}\n", "red"))
                else:
                    print(paint("  usage: /dir <path>  |  /dir off\n", "dim"))
            elif cmd == "pwd":
                print(paint(f"  {ws.root if ws else 'no workspace'}\n", "dim"))
            elif cmd == "ls":
                if not ws:
                    print(paint("  no workspace set\n", "dim"))
                else:
                    try:
                        print(paint("  " + ws.list_dir(rest or ".").replace("\n", "\n  "), "dim"), "\n")
                    except WorkspaceError as e:
                        print(paint(f"  {e}\n", "red"))
            elif cmd == "think":
                thinking = not thinking
                print(paint(f"  thinking {'on' if thinking else 'off'}\n", "dim"))
            elif cmd in ("auto", "ask"):
                auto_yes = (cmd == "auto") if rest == "" else auto_yes
                if cmd == "ask":
                    auto_yes = False
                print(paint(
                    "  automatic — no confirmations\n" if auto_yes
                    else "  will ask before overwriting, editing, deleting or moving\n",
                    "yellow" if auto_yes else "green"))
            elif cmd == "clear":
                history = []
                print(paint("  conversation cleared\n", "dim"))
            else:
                print(paint(f"  unknown command: /{cmd}  (/help)\n", "dim"))
            continue

        # --- a turn ---
        system = SYSTEM_TOOLS.format(root=ws.root) if ws else SYSTEM_CHAT
        history.append({"role": "user", "content": line})
        tools = TOOL_SCHEMAS if ws else None

        truncations = 0
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                messages = trim(client, [{"role": "system", "content": system}] + history)
                try:
                    with Spinner("working" if ws else "thinking"):
                        msg, timings = chat(client, messages, tools, thinking)
                except Truncated:
                    # The model tried to write too much in one call. Nudge it smaller
                    # rather than aborting the whole turn.
                    truncations += 1
                    if truncations > 3:
                        print(paint(
                            "\n  Gave up: the model kept trying to write more than it can "
                            "emit in one call.\n  Ask for one small file at a time.\n",
                            "yellow"))
                        break
                    print(paint(
                        f"  ⚠ output truncated mid-write — retrying smaller "
                        f"({truncations}/3)", "yellow"))
                    history.append({"role": "user", "content": TRUNCATION_HINT})
                    continue

                calls = msg.get("tool_calls") or []
                reasoning = msg.get("reasoning_content")
                if reasoning and thinking:
                    print(paint(f"  thought for {len(reasoning)} chars", "dim"))

                history.append({
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    **({"tool_calls": calls} if calls else {}),
                })

                if not calls:
                    text = (msg.get("content") or "").strip()
                    print(("\n" + text if text else paint("  (no reply)", "dim")) + "\n")
                    tps = timings.get("predicted_per_second")
                    if tps:
                        print(paint(f"  {timings.get('predicted_n', 0)} tokens · {tps:.1f} tok/s\n", "dim"))
                    break

                history.extend(run_tools(ws, calls, auto_yes))
            else:
                print(paint(f"\n  Stopped after {MAX_TOOL_ROUNDS} tool rounds.\n", "yellow"))
        except KeyboardInterrupt:
            print(paint("\n  aborted\n", "dim"))
        except httpx.HTTPError as e:
            print(paint(f"\n  model server error: {e}\n", "red"))

    client.close()
    print(paint("  bye\n", "dim"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
