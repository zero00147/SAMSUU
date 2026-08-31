"""Group F — API / end-to-end integration tests against a running samsu server."""

import json
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path("/Users/rifat/Desktop/samsu")
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8099"
RESULTS = []


def rec(tid, desc, expected, actual, ok, note=""):
    RESULTS.append({"id": tid, "group": "F", "desc": desc, "expected": expected,
                    "actual": actual, "status": "PASS" if ok else "FAIL", "note": note})
    print(f"[{'PASS' if ok else 'FAIL'}] {tid:5} {desc}")
    if not ok:
        print(f"        expected: {expected}\n        actual  : {actual}")


def sse(resp):
    for line in resp.iter_lines():
        if line.startswith("data: "):
            yield json.loads(line[6:])


def main():
    tmpdb = Path(tempfile.mkdtemp(prefix="samsu-api-")) / "test.db"
    proc = subprocess.Popen(
        [str(ROOT / ".venv/bin/python"),
         str(OUT / "serve_test.py"), str(tmpdb)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    for _ in range(60):
        try:
            httpx.get(f"{BASE}/api/health", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    else:
        print("server did not start"); proc.kill(); return

    try:
        # F1 — health
        h = httpx.get(f"{BASE}/api/health", timeout=10).json()
        rec("F1", "health endpoint reports the model server and context size",
            "ok=True, Qwen3-4B, n_ctx 8192",
            json.dumps(h)[:120],
            h.get("ok") and "Qwen3-4B" in str(h.get("model", "")))

        # F2 — budget
        b = httpx.get(f"{BASE}/api/budget", timeout=10).json()
        rec("F2", "budget endpoint reports usable prompt space",
            "budget = 8192 − 3072 − 256 = 4864", json.dumps(b),
            b["budget"] == 4864)

        # F3 — tokenize
        t = httpx.post(f"{BASE}/api/tokenize", json={"text": "hello world"}, timeout=10)
        rec("F3", "tokenize endpoint returns an exact count",
            "HTTP 200 with a small count", f"{t.status_code} {t.text[:60]}",
            t.status_code == 200 and 1 <= t.json().get("tokens", 0) <= 6)

        # F4 — chat lifecycle
        c = httpx.post(f"{BASE}/api/chats", timeout=10).json()
        cid = c["id"]
        listed = httpx.get(f"{BASE}/api/chats", timeout=10).json()
        r = httpx.patch(f"{BASE}/api/chats/{cid}", json={"title": "Renamed"}, timeout=10)
        after = httpx.get(f"{BASE}/api/chats/{cid}", timeout=10).json()
        rec("F4", "chat create / list / rename round-trips",
            "chat appears in the list and the new title persists",
            f"listed={len(listed)}, title={after['title']!r}",
            len(listed) >= 1 and after["title"] == "Renamed")

        # F5 — 404 for an unknown chat
        r404 = httpx.get(f"{BASE}/api/chats/does-not-exist", timeout=10)
        rec("F5", "an unknown chat id returns 404, not a 500",
            "404", str(r404.status_code), r404.status_code == 404)

        # F6 — streaming generation, end to end
        chat = httpx.post(f"{BASE}/api/chats", timeout=10).json()
        cid = chat["id"]
        events, deltas, done = [], 0, None
        t0 = time.perf_counter()
        with httpx.stream("POST", f"{BASE}/api/chats/{cid}/messages", timeout=300,
                          json={"content": "Name three primary colours, briefly.",
                                "thinking": False}) as resp:
            for ev in sse(resp):
                events.append(ev["type"])
                if ev["type"] == "content":
                    deltas += 1
                if ev["type"] == "done":
                    done = ev
        dt = time.perf_counter() - t0
        rec("F6", "sending a message streams user → content deltas → done",
            "'user' first, many content events, a final 'done'",
            f"{len(events)} events, {deltas} content deltas, done={done is not None}, {dt:.1f}s",
            events and events[0] == "user" and deltas > 3 and done is not None)

        # F7 — per-message statistics are persisted
        msgs = httpx.get(f"{BASE}/api/chats/{cid}", timeout=10).json()["messages"]
        a = [m for m in msgs if m["role"] == "assistant"][-1]
        rec("F7", "generation statistics are persisted with the reply",
            "ttft_ms, duration_ms, tokens, tokens_per_sec all present",
            f"ttft={a['ttft_ms']}ms duration={a['duration_ms']}ms "
            f"tokens={a['tokens']} tok/s={a['tokens_per_sec']}",
            all(a[k] is not None for k in ("ttft_ms", "duration_ms", "tokens", "tokens_per_sec")))

        # F8 — the chat is auto-titled from the first user message
        title = httpx.get(f"{BASE}/api/chats/{cid}", timeout=10).json()["title"]
        rec("F8", "a new chat is titled from its first user message",
            "first six words of the message", repr(title),
            title.startswith("Name three primary colours"))

        # F9 — Stop: abort mid-stream, partial reply must survive
        chat2 = httpx.post(f"{BASE}/api/chats", timeout=10).json()
        cid2 = chat2["id"]
        got = 0
        try:
            with httpx.stream("POST", f"{BASE}/api/chats/{cid2}/messages", timeout=300,
                              json={"content": "Write a long description of the solar system.",
                                    "thinking": False}) as resp:
                for ev in sse(resp):
                    if ev["type"] == "content":
                        got += 1
                        if got >= 12:
                            break          # client abort — this is what Stop does
        except Exception:
            pass
        time.sleep(2.0)
        msgs2 = httpx.get(f"{BASE}/api/chats/{cid2}", timeout=10).json()["messages"]
        partial = [m for m in msgs2 if m["role"] == "assistant"]
        ok = bool(partial) and partial[-1]["stopped"] == 1 and len(partial[-1]["content"]) > 0
        rec("F9", "aborting mid-stream persists the partial reply and marks it stopped",
            "one assistant row, stopped=1, non-empty content",
            f"rows={len(partial)}, stopped={partial[-1]['stopped'] if partial else None}, "
            f"chars={len(partial[-1]['content']) if partial else 0}",
            ok)

        # F10 — llama-server actually abandons the cancelled generation
        time.sleep(1.0)
        cpu = subprocess.run(
            ["ps", "-o", "%cpu=", "-p", subprocess.run(
                ["pgrep", "-f", "llama-server -m"], capture_output=True, text=True
            ).stdout.split()[0]], capture_output=True, text=True).stdout.strip()
        rec("F10", "cancelling the client drops upstream generation (model server goes idle)",
            "llama-server CPU back near idle", f"{cpu}% CPU 3s after abort",
            float(cpu or 100) < 40)

        # F11 — truncate is the edit / regenerate primitive
        before = len(httpx.get(f"{BASE}/api/chats/{cid}", timeout=10).json()["messages"])
        tr = httpx.post(f"{BASE}/api/chats/{cid}/truncate", json={"from_seq": 1}, timeout=10).json()
        after_n = len(httpx.get(f"{BASE}/api/chats/{cid}", timeout=10).json()["messages"])
        rec("F11", "truncate removes every message from a sequence number onward",
            "the assistant reply is removed, the user message remains",
            f"{before} → {after_n} messages, removed={tr['removed']}",
            after_n == 1 and tr["removed"] >= 1)

        # F12 — document upload and section listing
        prd = (ROOT / "PRD.md").read_bytes()
        up = httpx.post(f"{BASE}/api/documents",
                        files={"file": ("PRD.md", prd, "text/markdown")}, timeout=120)
        doc = up.json()
        secs = httpx.get(f"{BASE}/api/documents/{doc['id']}/sections", timeout=60).json()
        rows = secs["sections"] if isinstance(secs, dict) else secs
        rec("F12", "uploading a document returns per-section token counts",
            "≥ 30 sections, each with a token count",
            f"{len(rows)} sections, total {doc.get('total_tokens')} tokens, "
            f"largest section {max(r['tokens'] for r in rows)} tokens",
            len(rows) >= 30 and all(r["tokens"] > 0 for r in rows))

        # F13 — a document too big for the window, one section that fits
        rec("F13", "the whole document exceeds the window but individual sections fit",
            "total > 4864 tokens, smallest sections well under it",
            f"total={doc.get('total_tokens')}, min section={min(r['tokens'] for r in rows)}",
            (doc.get("total_tokens") or 0) > 4864 and min(r["tokens"] for r in rows) < 4864)

        # F14 — attaching a section stores it separately from the visible message
        chat3 = httpx.post(f"{BASE}/api/chats", timeout=10).json()["id"]
        small = sorted(rows, key=lambda r: r["tokens"])[len(rows) // 4]
        with httpx.stream("POST", f"{BASE}/api/chats/{chat3}/messages", timeout=300,
                          json={"content": "In one sentence, what does this section cover?",
                                "thinking": False, "section_ids": [small["id"]]}) as resp:
            for _ in sse(resp):
                pass
        m3 = httpx.get(f"{BASE}/api/chats/{chat3}", timeout=10).json()["messages"][0]
        att = json.loads(m3.get("attachments") or "[]")
        rec("F14", "an attached section is stored alongside the message, not inlined into it",
            "message text unchanged; attachment recorded separately",
            f"content={m3['content'][:40]!r}, attachments={len(att)}, label={att[0]['label'] if att else None}",
            len(att) == 1 and "In one sentence" in m3["content"])

        # F15 — document deletion cascades
        httpx.delete(f"{BASE}/api/documents/{doc['id']}", timeout=30)
        gone = httpx.get(f"{BASE}/api/documents", timeout=10).json()
        ids = [d["id"] for d in (gone["documents"] if isinstance(gone, dict) else gone)]
        rec("F15", "deleting a document removes it and its sections",
            "document no longer listed", f"remaining={ids}", doc["id"] not in ids)

        # F16 — concurrency: the UI stays responsive while a generation runs
        latencies = []

        def hammer():
            for _ in range(30):
                t = time.perf_counter()
                httpx.get(f"{BASE}/api/chats", timeout=30)
                latencies.append((time.perf_counter() - t) * 1000)
                time.sleep(0.05)

        chat4 = httpx.post(f"{BASE}/api/chats", timeout=10).json()["id"]

        def generate():
            with httpx.stream("POST", f"{BASE}/api/chats/{chat4}/messages", timeout=300,
                              json={"content": "Explain how a bicycle works, in detail.",
                                    "thinking": False}) as resp:
                for _ in sse(resp):
                    pass

        with ThreadPoolExecutor(max_workers=3) as ex:
            f1 = ex.submit(generate)
            f2, f3 = ex.submit(hammer), ex.submit(hammer)
            f1.result(); f2.result(); f3.result()

        slowest = max(latencies)
        rec("F16", "the API stays responsive while a generation is streaming",
            "every request served, slowest well under 1 s",
            f"{len(latencies)} requests during generation, "
            f"median {statistics.median(latencies):.0f} ms, slowest {slowest:.0f} ms",
            len(latencies) == 60 and slowest < 1000)

        # F17 — the frontend is served and references no remote assets
        idx = httpx.get(f"{BASE}/", timeout=10)
        css = httpx.get(f"{BASE}/css/style.css", timeout=10)
        rec("F17", "the frontend and its assets are served from the same origin",
            "HTTP 200 for the page and the stylesheet",
            f"index={idx.status_code}, style.css={css.status_code}, "
            f"remote refs={idx.text.count('http://') + idx.text.count('https://')}",
            idx.status_code == 200 and css.status_code == 200
            and "http://" not in idx.text and "https://" not in idx.text)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    print(f"\nGroup F: TOTAL {total}  PASS {passed}  FAIL {total - passed}")
    (OUT / "results_api.json").write_text(json.dumps(RESULTS, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
