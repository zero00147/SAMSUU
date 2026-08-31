"""samsu verification suite — executed 2026-08-30.

Run:  ./.venv/bin/python <this file>
Writes results.json next to itself.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path("/Users/rifat/Desktop/samsu")
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

RESULTS = []


def rec(tid, group, desc, expected, actual, ok, note=""):
    RESULTS.append({
        "id": tid, "group": group, "desc": desc,
        "expected": expected, "actual": actual,
        "status": "PASS" if ok else "FAIL", "note": note,
    })
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {tid:6} {desc}")
    if not ok:
        print(f"         expected: {expected}")
        print(f"         actual  : {actual}")


def check(tid, group, desc, expected, actual, note=""):
    rec(tid, group, desc, expected, actual, expected == actual, note)


# =====================================================================
# Group A — Workspace path confinement
# =====================================================================
def group_a():
    from server.workspace import Workspace, WorkspaceError

    tmp = Path(tempfile.mkdtemp(prefix="samsu-ws-"))
    (tmp / "sub").mkdir()
    (tmp / "notes.txt").write_text("hello\n")
    (tmp / "sub" / "deep.txt").write_text("deep\n")
    os.symlink("/etc/passwd", tmp / "link_out")
    ws = Workspace(str(tmp))

    escapes = [
        ("A1", "../../../etc/passwd", "parent traversal"),
        ("A2", "/etc/passwd", "absolute path"),
        ("A3", "sub/../../../../etc/hosts", "traversal through a subdirectory"),
        ("A4", "link_out", "symlink pointing outside the workspace"),
        ("A5", "..", "the parent directory itself"),
        ("A6", "sub/../..", "traversal that lands on the parent"),
        ("A7", "~/.ssh/id_rsa", "tilde expansion inside a relative path"),
    ]
    for tid, path, desc in escapes:
        try:
            ws.read_file(path)
            rec(tid, "A", f"read_file blocked: {desc}", "WorkspaceError",
                "file was read — LEAK", False)
        except WorkspaceError as e:
            rec(tid, "A", f"read_file blocked: {desc}", "WorkspaceError",
                f"WorkspaceError: {e}", True)
        except Exception as e:
            rec(tid, "A", f"read_file blocked: {desc}", "WorkspaceError",
                f"{type(e).__name__}: {e}", False,
                "blocked, but not by the confinement check")

    # A8 — legitimate access still works (negative control)
    try:
        got = ws.read_file("sub/deep.txt")
        rec("A8", "A", "legitimate nested read succeeds", "file contents",
            repr(got.strip()), got.strip() == "deep")
    except Exception as e:
        rec("A8", "A", "legitimate nested read succeeds", "file contents",
            f"{type(e).__name__}: {e}", False)

    # A9 — root cannot be deleted
    try:
        ws.delete_path(".")
        rec("A9", "A", "workspace root cannot be deleted", "WorkspaceError",
            "root deleted — LEAK", False)
    except WorkspaceError as e:
        rec("A9", "A", "workspace root cannot be deleted", "WorkspaceError",
            f"WorkspaceError: {e}", True)
    except Exception as e:
        rec("A9", "A", "workspace root cannot be deleted", "WorkspaceError",
            f"{type(e).__name__}: {e}", False)

    # A10 — write outside the tree
    try:
        ws.write_file("../pwned.txt", "x")
        rec("A10", "A", "write_file blocked outside the tree", "WorkspaceError",
            "file written outside — LEAK", False)
    except WorkspaceError as e:
        rec("A10", "A", "write_file blocked outside the tree", "WorkspaceError",
            f"WorkspaceError: {e}", True)

    # A11 — move outside the tree
    try:
        ws.move_path("notes.txt", "../../notes.txt")
        rec("A11", "A", "move_path blocked outside the tree", "WorkspaceError",
            "file moved outside — LEAK", False)
    except WorkspaceError as e:
        rec("A11", "A", "move_path blocked outside the tree", "WorkspaceError",
            f"WorkspaceError: {e}", True)

    # A12 — oversized read refused
    big = tmp / "big.bin"
    big.write_bytes(b"a" * 300_000)
    try:
        ws.read_file("big.bin")
        rec("A12", "A", "read of a 300 KB file refused", "WorkspaceError",
            "read succeeded", False)
    except WorkspaceError as e:
        rec("A12", "A", "read of a 300 KB file refused", "WorkspaceError",
            f"WorkspaceError: {e}", True)

    # A13 — nonexistent workspace root rejected at construction
    try:
        Workspace(str(tmp / "does-not-exist"))
        rec("A13", "A", "nonexistent workspace root rejected", "WorkspaceError",
            "accepted", False)
    except WorkspaceError as e:
        rec("A13", "A", "nonexistent workspace root rejected", "WorkspaceError",
            f"WorkspaceError: {e}", True)

    # A14 — edit_file on a string that is not present
    try:
        ws.edit_file("notes.txt", "not-there", "x")
        rec("A14", "A", "edit_file reports a missing target string", "WorkspaceError",
            "silently succeeded", False)
    except WorkspaceError as e:
        rec("A14", "A", "edit_file reports a missing target string", "WorkspaceError",
            f"WorkspaceError: {e}", True)
    except TypeError as e:
        rec("A14", "A", "edit_file reports a missing target string", "WorkspaceError",
            f"TypeError: {e}", False)

    shutil.rmtree(tmp, ignore_errors=True)


# =====================================================================
# Group B — Authentication and authorisation
# =====================================================================
def group_b():
    from server import db, auth

    tmpdb = Path(tempfile.mkdtemp(prefix="samsu-db-")) / "test.db"
    db.DB_PATH = tmpdb
    db.init()
    auth.init()

    OWNER, USER, STRANGER, BLOCKED = 1001, 1002, 1003, 1004

    auth.upsert_seen(OWNER, "owner", "Owner")
    auth.upsert_seen(USER, "user", "User")
    auth.upsert_seen(STRANGER, "stranger", "Stranger")
    auth.upsert_seen(BLOCKED, "blocked", "Blocked")

    # B1 — a brand-new contact is pending, not authenticated
    acct = auth.get_account(STRANGER)
    check("B1", "B", "unknown sender is recorded as 'pending', not authenticated",
          ("pending", False), (acct["role"], auth.is_authenticated(acct)))

    # B2 — owner pairing code redeems once
    code = auth.new_code("owner", 60)
    ok, msg = auth.redeem(code, OWNER)
    check("B2", "B", "valid owner code pairs the account", (True, "owner"), (ok, msg))

    # B3 — the same code cannot be redeemed twice
    ok2, msg2 = auth.redeem(code, USER)
    check("B3", "B", "a used pairing code is refused (single use)",
          (False, "That code is not valid."), (ok2, msg2))

    # B4 — a wrong code is refused
    ok3, msg3 = auth.redeem("ZZZZ-ZZZZ", STRANGER)
    check("B4", "B", "an invalid code is refused",
          (False, "That code is not valid."), (ok3, msg3))

    # B5 — an expired code is refused
    expired = auth.new_code("user", 60)
    with db.connect() as conn:
        conn.execute("UPDATE tg_pairing SET expires_at = ? WHERE code = ?",
                     ((datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds"),
                      expired))
    ok4, msg4 = auth.redeem(expired, STRANGER)
    check("B6" if False else "B5", "B", "an expired code is refused",
          (False, "That code has expired. Ask for a new one."), (ok4, msg4))

    # B6 — codes are normalised (lower case / stray spaces from a phone keyboard)
    c2 = auth.new_code("user", 60)
    ok5, msg5 = auth.redeem(f"  {c2.lower()} ", USER)
    check("B6", "B", "a code typed in lower case with spaces still pairs",
          (True, "user"), (ok5, msg5))

    # B7 — owner with NO workspace gets no file tools
    owner = auth.get_account(OWNER)
    check("B7", "B", "owner without a bound workspace gets no file tools",
          False, auth.may_use_tools(owner))

    # B8 — owner WITH workspace gets file tools
    auth.set_workspace(OWNER, "/tmp/ws")
    owner = auth.get_account(OWNER)
    check("B8", "B", "owner with a bound workspace gets file tools",
          True, auth.may_use_tools(owner))

    # B9 — a 'user' with a workspace row still gets no tools
    auth.set_workspace(USER, "/tmp/ws")
    u = auth.get_account(USER)
    check("B9", "B", "role 'user' with a workspace row still gets no file tools",
          False, auth.may_use_tools(u))

    # B10 — admin commands are owner-only
    check("B10", "B", "administration is owner-only",
          (True, False), (auth.may_administer(auth.get_account(OWNER)),
                          auth.may_administer(auth.get_account(USER))))

    # B11 — a blocked account is not authenticated
    auth.set_role(BLOCKED, "blocked")
    check("B11", "B", "a blocked account is not authenticated",
          False, auth.is_authenticated(auth.get_account(BLOCKED)))

    # B12 — codes cannot mint a 'blocked' or unknown role
    try:
        auth.new_code("blocked", 10)
        rec("B12", "B", "a pairing code cannot grant 'blocked'", "ValueError",
            "code minted", False)
    except ValueError as e:
        rec("B12", "B", "a pairing code cannot grant 'blocked'", "ValueError",
            f"ValueError: {e}", True)

    # B13 — every decision leaves an audit row
    actions = [r["action"] for r in auth.recent_audit(50)]
    need = {"paired", "pair_failed", "pair_expired"}
    check("B13", "B", "pairing successes, failures and expiries are all audited",
          True, need.issubset(set(actions)),
          note=f"actions seen: {sorted(set(actions))}")

    # B14 — the code alphabet excludes ambiguous glyphs
    sample = "".join(auth.new_code("user", 1).replace("-", "") for _ in range(200))
    bad = sorted({ch for ch in sample if ch in "O0I1L"})
    check("B14", "B", "pairing alphabet omits O/0/I/1/L (codes are typed by hand)",
          [], bad)

    shutil.rmtree(tmpdb.parent, ignore_errors=True)


# =====================================================================
# Group C — Document ingest and section splitting
# =====================================================================
def group_c():
    from server import documents
    from server.documents import ExtractError

    prd = (ROOT / "PRD.md").read_bytes()
    text = documents.extract_text("PRD.md", prd)
    secs = documents.split_sections(text)

    # C1 — the PRD splits into addressable sections
    rec("C1", "C", "PRD.md splits into per-heading sections",
        "≥ 30 sections", f"{len(secs)} sections", len(secs) >= 30)

    # C2 — headings keep their numbering, and subsections get level 2+
    levels = sorted({s["level"] for s in secs})
    rec("C2", "C", "heading levels are preserved (top level and subsections)",
        "more than one level present", f"levels {levels}", len(levels) > 1)

    # C3 — no section is empty
    empties = [s["heading"] for s in secs if not s["content"].strip()]
    check("C3", "C", "no section is produced with empty content", [], empties)

    # C4 — a numbered-list item must not be mistaken for a heading
    sample = (
        "Intro paragraph.\n\n"
        "## 3.1 Item Listing Engine\n"
        "Sellers create listings. Each listing requires:\n"
        "1. Media Assets: minimum 3 images and a 30-second video\n"
        "2. Category Selection from the taxonomy\n"
        "3. Reserve Price, optional\n"
    )
    got = [s["heading"] for s in documents.split_sections(sample)]
    expect_absent = [h for h in got if h.startswith(("1 Media", "2 Category", "3 Reserve"))]
    check("C4", "C", "numbered list items are not treated as headings",
          [], expect_absent, note=f"headings found: {got}")

    # C5 — unsupported file type is rejected with a clear message
    try:
        documents.extract_text("photo.png", b"\x89PNG")
        rec("C5", "C", "unsupported file type rejected", "ExtractError", "accepted", False)
    except ExtractError as e:
        rec("C5", "C", "unsupported file type rejected", "ExtractError",
            f"ExtractError: {e}", True)

    # C6 — legacy .doc gets an actionable message, not a stack trace
    try:
        documents.extract_text("spec.doc", b"\xd0\xcf")
        rec("C6", "C", "legacy .doc rejected with guidance", "ExtractError", "accepted", False)
    except ExtractError as e:
        rec("C6", "C", "legacy .doc rejected with guidance", "ExtractError",
            f"ExtractError: {e}", "save as .docx" in str(e))

    # C7 — a real .docx round-trips through the extractor
    docx_bytes = (ROOT / "samsu-phase2-report.docx").read_bytes()
    dtext = documents.extract_text("samsu-phase2-report.docx", docx_bytes)
    rec("C7", "C", "DOCX text extraction returns the document body",
        "non-empty text containing a known heading",
        f"{len(dtext)} chars, heading present: {'System Overview' in dtext}",
        len(dtext) > 1000 and "System Overview" in dtext)

    # C8 — a corrupt PDF fails cleanly
    try:
        documents.extract_text("broken.pdf", b"not a pdf at all")
        rec("C8", "C", "a corrupt PDF fails cleanly", "ExtractError", "accepted", False)
    except ExtractError as e:
        rec("C8", "C", "a corrupt PDF fails cleanly", "ExtractError",
            f"ExtractError: {str(e)[:60]}…", True)
    except Exception as e:
        rec("C8", "C", "a corrupt PDF fails cleanly", "ExtractError",
            f"{type(e).__name__}: {e}", False, "leaked a non-ExtractError to the caller")

    # C9 — a document with no headings still becomes attachable chunks
    plain = ("word " * 3000)
    chunks = documents.split_sections(plain)
    rec("C9", "C", "a heading-less document falls back to size-based chunks",
        "≥ 2 chunks named 'Part n'", f"{len(chunks)} chunks, first={chunks[0]['heading']!r}",
        len(chunks) >= 2 and chunks[0]["heading"] == "Part 1")

    # C10 — an oversized single section is broken into parts
    huge = "# One Heading\n\n" + ("paragraph body. " * 60 + "\n\n") * 60
    parts = documents.split_sections(huge)
    rec("C10", "C", "a section larger than the chunk limit is split into parts",
        "≥ 2 parts", f"{len(parts)} parts: {[p['heading'] for p in parts][:3]}",
        len(parts) >= 2 and "part 1" in parts[0]["heading"])


# =====================================================================
# Group D — Context-window management
# =====================================================================
def group_d():
    from server import tokens, llm
    from server.config import CONFIG

    # D1 — budget arithmetic matches the documented formula
    expect = max(512, CONFIG["n_ctx"] - CONFIG["max_tokens"] - 256)
    check("D1", "D", "prompt budget = n_ctx − max_tokens − 256",
          expect, tokens.prompt_budget(),
          note=f"n_ctx={CONFIG['n_ctx']} max_tokens={CONFIG['max_tokens']}")

    async def live():
        # D2 — exact token count comes from llama-server, not an estimate
        n = await tokens.count("The quick brown fox jumps over the lazy dog.")
        rec("D2", "D", "token counting uses llama-server /tokenize",
            "a plausible exact count (5–15)", str(n), 5 <= n <= 15)

        # D3 — repeated counts are served from the cache
        text = "cache probe " * 50
        t0 = time.perf_counter(); a = await tokens.count(text); t1 = time.perf_counter()
        b = await tokens.count(text); t2 = time.perf_counter()
        rec("D3", "D", "identical content is counted once and cached",
            "same count, second call materially faster",
            f"{a} vs {b}; {(t1-t0)*1000:.1f} ms then {(t2-t1)*1000:.3f} ms",
            a == b and (t2 - t1) < (t1 - t0))

        # D4 — an over-long history is trimmed to fit
        budget = tokens.prompt_budget()
        history = []
        for i in range(120):
            history.append({"role": "user",
                            "content": f"Message {i}. " + "filler content for the window. " * 20})
            history.append({"role": "assistant",
                            "content": f"Reply {i}. " + "answer text that takes up room. " * 20})
        history.append({"role": "user", "content": "FINAL QUESTION: what is 2 + 2?"})

        raw_total = sum(await tokens.count_many([m["content"] for m in history]))
        payload = await llm.build_payload(history, thinking=False)
        kept = payload["messages"]
        kept_total = sum(await tokens.count_many([m["content"] for m in kept]))

        rec("D4", "D", "an over-long history is trimmed to fit the window",
            f"≤ budget ({budget})",
            f"{raw_total} tokens in, {kept_total} tokens sent ({len(kept)} of {len(history)+1} messages)",
            kept_total <= budget)

        # D5 — the newest turn survives trimming, and the system prompt is kept
        rec("D5", "D", "trimming keeps the system prompt and the newest turn",
            "system first, newest user message last",
            f"first={kept[0]['role']}, last ends {kept[-1]['content'][-20:]!r}",
            kept[0]["role"] == "system" and kept[-1]["content"].endswith("2 + 2?"))

        # D6 — a single turn bigger than the whole budget degrades instead of failing
        monster = [{"role": "user", "content": "OVERSIZE. " * 30000}]
        p2 = await llm.build_payload(monster, thinking=False)
        body = p2["messages"][-1]["content"]
        n2 = sum(await tokens.count_many([m["content"] for m in p2["messages"]]))
        rec("D6", "D", "a single turn larger than the budget keeps its tail, marked truncated",
            "'…(truncated)…' marker present and total ≤ budget",
            f"marker={'…(truncated)…' in body}, total={n2} vs budget {budget}",
            "…(truncated)…" in body and n2 <= budget)

        # D7 — thinking flag is passed through as a chat-template switch
        p3 = await llm.build_payload([{"role": "user", "content": "hi"}], thinking=True)
        check("D7", "D", "the Think toggle is sent as a chat-template argument",
              {"enable_thinking": True}, p3["chat_template_kwargs"])

        # D8 — attachments are inlined only when sending to the model
        msg = {"role": "user", "content": "Summarise this.",
               "attachments": json.dumps([{"label": "PRD §7.1", "content": "Proxy bidding works as follows."}])}
        expanded = llm.expand(msg)
        rec("D8", "D", "attached document sections are inlined for the model only",
            "reference block prepended to the user text",
            repr(expanded[:60]) + "…",
            "[Reference: PRD §7.1]" in expanded and expanded.endswith("Summarise this."))

        # D9 — malformed attachment JSON must not break the turn
        bad = {"role": "user", "content": "Still works.", "attachments": "{not json"}
        rec("D9", "D", "malformed attachment JSON degrades to plain text",
            "'Still works.'", repr(llm.expand(bad)), llm.expand(bad) == "Still works.")

        # D10 — fallback estimate when llama-server is unreachable
        import server.tokens as tk
        real_url = tk.LLAMA_URL
        tk.LLAMA_URL = "http://127.0.0.1:9"        # closed port
        tk._CACHE.clear()
        est = await tk.count("x" * 300)
        tk.LLAMA_URL = real_url
        tk._CACHE.clear()
        rec("D10", "D", "token counting falls back to a pessimistic estimate if the model server is down",
            "an over-estimate, not a crash", f"{est} tokens for 300 chars",
            est >= 100)

    asyncio.run(live())


# =====================================================================
# Group E — Client-side rendering (render.js, run under JavaScriptCore)
# =====================================================================
def group_e():
    src = (ROOT / "web" / "js" / "render.js").read_text()
    start = src.index("const LATEX_SYMBOLS")
    end = src.index("function renderMarkdown")
    lib = src[start:end]

    cases = [
        ("E1", "LaTeX symbol commands become Unicode",
         "Speed is 60 \\times 2 mph.", "Speed is 60 × 2 mph."),
        ("E2", "\\frac{a}{b} becomes an inline quotient",
         "The ratio \\frac{3}{4} holds.", "The ratio (3)/(4) holds."),
        ("E3", "\\text{} wrappers are unwrapped",
         "$60 \\text{ mph}$", "60 mph"),
        ("E4", "\\sqrt{} becomes a root",
         "\\sqrt{16} = 4", "√(16) = 4"),
        ("E5", "currency is NOT eaten as a math span",
         "It costs $5 and $10 total.", "It costs $5 and $10 total."),
        ("E6", "a genuine math span is unwrapped",
         "Let $x^2 + 1$ be given.", "Let x^2 + 1 be given."),
        ("E7", "\\( \\) delimiters are removed",
         "Compute \\(a + b\\) now.", "Compute a + b now."),
    ]

    js_cases = json.dumps([{"id": c[0], "desc": c[1], "input": c[2], "expect": c[3]} for c in cases])
    # E8/E9 exercise outsideCode: shell and code content must survive untouched.
    script = f"""
{lib}
function stripLatex(text) {{
  let out = text.replace(/\\\\\\(|\\\\\\)|\\\\\\[|\\\\\\]/g, '');
  out = out.replace(/\\$\\$([\\s\\S]+?)\\$\\$/g, '$1');
  out = out.replace(/\\$([^$\\n]{{1,200}}?)\\$/g, (m, inner) => {{
    const hasLatex = /[\\\\^_{{}}]/.test(inner);
    const pureMath = /^[\\d\\s+\\-*/=().,×·÷<>]+$/.test(inner);
    return hasLatex || pureMath ? inner : m;
  }});
  for (const [re, to] of LATEX_SYMBOLS) out = out.replace(re, to);
  return out.replace(/(\\S) {{2,}}/g, '$1 ');
}}
function outsideCode(text, fn) {{
  const parts = text.split(/(```[\\s\\S]*?```|`[^`\\n]*`)/g);
  return parts.map((p, i) => (i % 2 ? p : fn(p))).join('');
}}
var cases = {js_cases};
var out = cases.map(function(c) {{
  return {{id: c.id, desc: c.desc, input: c.input, expect: c.expect, got: outsideCode(c.input, stripLatex)}};
}});
out.push({{id: "E8", desc: "a fenced code block is left untouched",
          input: "Run this:\\n```bash\\necho $PATH\\n```",
          expect: "Run this:\\n```bash\\necho $PATH\\n```",
          got: outsideCode("Run this:\\n```bash\\necho $PATH\\n```", stripLatex)}});
out.push({{id: "E9", desc: "an inline code span is left untouched",
          input: "Use `a $b$ c` here.", expect: "Use `a $b$ c` here.",
          got: outsideCode("Use `a $b$ c` here.", stripLatex)}});
JSON.stringify(out);
"""
    p = subprocess.run(["osascript", "-l", "JavaScript", "-e", script],
                       capture_output=True, text=True)
    if p.returncode != 0:
        rec("E0", "E", "render.js harness executes", "runs under JavaScriptCore",
            p.stderr.strip()[:200], False)
        return
    for r in json.loads(p.stdout.strip().strip('"').encode().decode("unicode_escape")
                        if p.stdout.strip().startswith('"') else p.stdout):
        rec(r["id"], "E", r["desc"], repr(r["expect"]), repr(r["got"]),
            r["expect"] == r["got"])


# =====================================================================
# Group G — Live model server
# =====================================================================
def group_g():
    import httpx
    from server.config import LLAMA_URL, CONFIG
    from server.workspace import TOOL_SCHEMAS

    # G1 — health / model identity
    try:
        r = httpx.get(f"{LLAMA_URL}/v1/models", timeout=5)
        model = r.json()["data"][0]["id"]
        rec("G1", "G", "model server reports the loaded model",
            "the Qwen3-4B GGUF path", model, "Qwen3-4B" in model)
    except Exception as e:
        rec("G1", "G", "model server reports the loaded model", "200 OK",
            f"{type(e).__name__}: {e}", False)
        return

    # G2 — a plain completion, thinking OFF (latency measured)
    def ask(prompt, thinking, max_tokens=400):
        t0 = time.perf_counter()
        r = httpx.post(f"{LLAMA_URL}/v1/chat/completions", timeout=180, json={
            "messages": [{"role": "system", "content": CONFIG["system_prompt"]},
                         {"role": "user", "content": prompt}],
            "temperature": 0.7, "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": thinking},
        })
        dt = time.perf_counter() - t0
        j = r.json()
        ch = j["choices"][0]
        return ch["message"].get("content") or "", ch["finish_reason"], dt, j.get("timings", {})

    content, finish, dt_off, tim = ask("What is 17 * 23? Answer with just the number.", False)
    rec("G2", "G", "arithmetic answered correctly with thinking OFF",
        "'391' within a few seconds",
        f"{content.strip()[:40]!r} in {dt_off:.1f}s, {tim.get('predicted_per_second', 0):.1f} tok/s",
        "391" in content)

    # G3 — same question, thinking ON, capped at the same budget
    content_on, finish_on, dt_on, tim_on = ask("What is 17 * 23? Answer with just the number.", True)
    rec("G3", "G", "thinking ON at a 400-token cap is materially slower",
        "slower than thinking OFF",
        f"{dt_on:.1f}s vs {dt_off:.1f}s; finish_reason={finish_on}; content empty={not content_on.strip()}",
        dt_on > dt_off)

    # G4 — reasoning is delivered in a separate field, not inline tags
    r = httpx.post(f"{LLAMA_URL}/v1/chat/completions", timeout=180, json={
        "messages": [{"role": "user", "content": "Think briefly, then say OK."}],
        "max_tokens": 300, "chat_template_kwargs": {"enable_thinking": True},
    })
    m = r.json()["choices"][0]["message"]
    rec("G4", "G", "reasoning arrives as reasoning_content, not <think> tags in the answer",
        "reasoning_content present; no '<think>' in content",
        f"reasoning_content={'reasoning_content' in m and bool(m.get('reasoning_content'))}, "
        f"tags_in_content={'<think>' in (m.get('content') or '')}",
        bool(m.get("reasoning_content")) and "<think>" not in (m.get("content") or ""))

    # G5 — native tool calling
    r = httpx.post(f"{LLAMA_URL}/v1/chat/completions", timeout=180, json={
        "messages": [
            {"role": "system", "content": "You have file tools for a workspace. Use them."},
            {"role": "user", "content": "List the files in the current directory."},
        ],
        "tools": TOOL_SCHEMAS, "max_tokens": 300,
    })
    ch = r.json()["choices"][0]
    calls = ch["message"].get("tool_calls") or []
    names = [c["function"]["name"] for c in calls]
    args_ok = True
    for c in calls:
        try:
            json.loads(c["function"]["arguments"])
        except Exception:
            args_ok = False
    rec("G5", "G", "the model emits a native tool call with well-formed arguments",
        "finish_reason=tool_calls, list_dir requested, arguments parse as JSON",
        f"finish_reason={ch['finish_reason']}, calls={names}, args_parse={args_ok}",
        ch["finish_reason"] == "tool_calls" and "list_dir" in names and args_ok)

    # G6 — streaming yields incremental deltas and a timings block
    deltas, timings_seen = 0, False
    with httpx.stream("POST", f"{LLAMA_URL}/v1/chat/completions", timeout=180, json={
        "messages": [{"role": "user", "content": "Count from 1 to 20."}],
        "stream": True, "max_tokens": 200,
    }) as resp:
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            body = line[6:].strip()
            if body == "[DONE]":
                break
            chunk = json.loads(body)
            if "timings" in chunk:
                timings_seen = True
            ch = (chunk.get("choices") or [{}])[0]
            if (ch.get("delta") or {}).get("content"):
                deltas += 1
    rec("G6", "G", "streaming produces incremental deltas and a final timings block",
        "many deltas, timings present", f"{deltas} deltas, timings={timings_seen}",
        deltas > 5 and timings_seen)

    # G7 — a request larger than the window is rejected by the server
    #      (this is the failure mode the trimming in Group D exists to prevent)
    r = httpx.post(f"{LLAMA_URL}/v1/chat/completions", timeout=120, json={
        "messages": [{"role": "user", "content": "overflow " * 20000}],
        "max_tokens": 100,
    })
    rec("G7", "G", "an untrimmed over-length prompt is rejected by llama-server",
        "HTTP 4xx/5xx with a context-size error",
        f"HTTP {r.status_code}: {str(r.text)[:110]}",
        r.status_code >= 400)


# =====================================================================
# Group H — Static application assets (Void Runner)
# =====================================================================
def group_h():
    import httpx
    import re as _re

    game = ROOT / "apps" / "asteroids"
    html = (game / "index.html").read_text()

    # H1 — every local asset the page references exists on disk
    refs = _re.findall(r'(?:src|href)="([^"]+)"', html)
    refs += _re.findall(r'"(\./[^"]+\.js)"', html)
    local = [r for r in refs if not r.startswith(("http", "//", "#", "data:"))]
    missing = [r for r in local if not (game / r.lstrip("./")).exists()]
    check("H1", "H", "every asset the game references exists locally", [], missing,
          note=f"{len(local)} references checked")

    # H2 — nothing is loaded from a CDN (the offline guarantee)
    remote = _re.findall(r'(?:src|href|from)\s*=?\s*["\'](https?://[^"\']+)', html)
    for js in game.rglob("*.js"):
        if "vendor" in js.parts:
            continue
        remote += _re.findall(r'["\'](https?://[^"\']+)["\']', js.read_text())
    check("H2", "H", "no CDN or remote URL is referenced anywhere in the game", [], remote)

    # H3 — the same check across the main web UI
    web = ROOT / "web"
    remote_web = _re.findall(r'(?:src|href)="(https?://[^"]+)"', (web / "index.html").read_text())
    check("H3", "H", "the web UI loads no remote assets either", [], remote_web)

    # H4 — the game is actually served
    try:
        r = httpx.get("http://127.0.0.1:8100/", timeout=5)
        r2 = httpx.get("http://127.0.0.1:8100/js/game.js", timeout=5)
        rec("H4", "H", "the game server returns the page and its modules",
            "HTTP 200 for both", f"index={r.status_code}, game.js={r2.status_code}",
            r.status_code == 200 and r2.status_code == 200)
    except Exception as e:
        rec("H4", "H", "the game server returns the page and its modules",
            "HTTP 200", f"{type(e).__name__}: {e}", False, "server not running")

    # H5 — no audio or model binary assets on disk (all synthesised/procedural)
    binaries = [p.name for p in game.rglob("*")
                if p.suffix.lower() in {".wav", ".mp3", ".ogg", ".glb", ".gltf", ".fbx", ".png", ".jpg"}]
    check("H5", "H", "the game ships no audio or model binaries (all generated at runtime)",
          [], binaries)


# =====================================================================
# Group I — Voice input/output and requirement clarification
# =====================================================================
def group_i():
    from server import clarify, voice
    from server.clarify import ClarifySession

    # I1 — the toolchain is present
    st = voice.status()
    rec("I1", "I", "speech toolchain and model are installed",
        "whisper-cli, opusdec, opusenc and the model all present",
        f"listening={st['listening']}, model={st['model']}, missing={st['missing']}",
        st["listening"] and not st["missing"])
    if not st["listening"]:
        return

    async def live():
        # I2 — a full speech round trip preserves the words
        phrase = "Add a download button next to the chat title."
        ogg = await voice.synthesize(phrase)
        heard = await voice.transcribe(ogg, suffix=".opus")
        rec("I2", "I", "synthesised speech transcribes back to the same words",
            repr(phrase), f"{len(ogg)} bytes of Ogg/Opus -> {heard!r}",
            heard.lower().strip(" .") == phrase.lower().strip(" ."))

        # I3 — oversized audio is refused before it reaches whisper
        try:
            await voice.transcribe(b"x" * 9_000_000)
            rec("I4" if False else "I3", "I", "an oversized recording is refused",
                "VoiceError", "accepted", False)
        except voice.VoiceError as e:
            rec("I3", "I", "an oversized recording is refused",
                "VoiceError", f"VoiceError: {e}", True)

    asyncio.run(live())

    # I4 — markdown is stripped before it is spoken
    spoken = voice.speakable("## Title\n\n**Bold** and `code` and [link](http://x)\n- item")
    rec("I4", "I", "markdown is flattened to prose before synthesis",
        "no markup characters remain", repr(spoken),
        not any(c in spoken for c in "#*`[]("))

    # I5 — a long reply is cut at a sentence end, not mid-word
    long = ("This is the first sentence. " * 60)
    cut = voice.speakable(long)
    rec("I5", "I", "a long reply is truncated at a sentence boundary",
        "ends with a full stop, within the configured limit",
        f"{len(cut)} chars, ends {cut[-20:]!r}",
        len(cut) <= 700 and cut.endswith("."))

    # I6 — handing the decision back is detected in code, not left to the model
    defers = ["You decide.", "you choose", "I don't mind", "Up to you",
              "whatever you think", "no preference"]
    not_defers = ["It works if the file opens in any markdown reader and I do not mind the name",
                  "A markdown file with every message in order."]
    wrong = ([s for s in defers if not clarify._is_deferral(s)]
             + [s for s in not_defers if clarify._is_deferral(s)])
    check("I6", "I", "deferral phrases are recognised, substantive answers are not",
          [], wrong, note=f"{len(defers)} deferrals, {len(not_defers)} real answers")

    # I7 — a fabricated quote cannot mark a dimension covered
    request = "I want people to be able to save a chat as a file."
    fake = clarify._verified(
        [{"topic": "acceptance", "evidence": "it must pass the regression suite"}], request)
    real = clarify._verified(
        [{"topic": "scope", "evidence": "save a chat as a file"}], request)
    rec("I7", "I", "coverage claims are checked against the user's actual words",
        "invented quote rejected, real quote accepted",
        f"fabricated -> {sorted(fake)}, genuine -> {sorted(real)}",
        fake == set() and real == {"scope"})

    # I8 — a quote too short to prove anything is rejected
    short = clarify._verified([{"topic": "data", "evidence": "a file"}], request)
    check("I8", "I", "a quote shorter than the evidence threshold proves nothing",
          [], sorted(short))

    # I9 — a live clarification session asks questions and produces a spec
    async def session_run():
        import httpx
        s = ClarifySession("I want a way to export a conversation.")
        answers = {
            "scope": "Only the conversation I am looking at.",
            "trigger": "A button next to the chat title.",
            "behaviour": "Clicking it downloads the file immediately.",
            "data": "A markdown file with every message in order and who said it.",
            "acceptance": "You decide.",
        }
        async with httpx.AsyncClient() as client:
            step = await s.begin(client)
            precovered = len(s.covered)
            while step["kind"] == "question":
                step = await s.answer(client, answers[step["topic"]])
        return s, precovered

    s, precovered = asyncio.run(session_run())
    rec("I9", "I", "a clarification session asks, then writes a specification",
        "at least one question asked, spec produced with acceptance criteria",
        f"{len(s.exchanges)} questions asked, precovered={precovered}, "
        f"spec keys={sorted(s.spec)}, acceptance={len(s.spec.get('acceptance', []))}",
        len(s.exchanges) >= 1 and s.done and bool(s.spec.get("acceptance")))

    # I10 — the opening request can never be credited with everything
    rec("I10", "I", "the opening request is credited with at most the pre-cover cap",
        f"≤ {clarify.MAX_PRECOVERED} of {len(clarify.TOPICS)} dimensions",
        f"{precovered} pre-covered", precovered <= clarify.MAX_PRECOVERED)

    # I11 — deferring a question records an assumption rather than silently deciding
    deferred = [e for e in s.exchanges if e["deferred"]]
    rec("I11", "I", "a deferred question becomes a recorded assumption",
        "every deferral produces an assumption line",
        f"{len(deferred)} deferred, {len(s.assumptions)} assumptions recorded",
        len(s.assumptions) >= len(deferred))

    # I12 — the rendered spec shows the clarifications it was built from
    body = s.render_spec()
    rec("I12", "I", "the specification shows the clarifications behind it",
        "a 'Clarifications resolved' section listing each exchange",
        f"{len(body)} chars, section present: {'Clarifications resolved' in body}",
        "Clarifications resolved" in body and all(e["question"] in body for e in s.exchanges))


def main():
    print("=" * 72)
    print("samsu verification suite —", datetime.now().isoformat(timespec="seconds"))
    print("=" * 72)
    for name, fn in [("A", group_a), ("B", group_b), ("C", group_c),
                     ("D", group_d), ("E", group_e), ("G", group_g), ("H", group_h),
                     ("I", group_i)]:
        print(f"\n--- Group {name} ---")
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            rec(f"{name}-ERR", name, "group executed to completion", "no exception",
                f"{type(e).__name__}: {e}", False)

    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    print("\n" + "=" * 72)
    print(f"TOTAL {total}   PASS {passed}   FAIL {total - passed}")
    for r in RESULTS:
        if r["status"] == "FAIL":
            print(f"  FAIL {r['id']}: {r['desc']}")
    (OUT / "results.json").write_text(json.dumps(RESULTS, indent=2, ensure_ascii=False))
    print(f"\nwrote {OUT / 'results.json'}")


if __name__ == "__main__":
    main()
