# Demo script

A ~12 minute walkthrough proving the four claims: the bot works, it is genuinely
authorised, it runs concurrently with the app, and the game runs.

Every timing here was rehearsed on this machine — no step is guessed.

---

## Before you start (2 minutes, do this off-stage)

```bash
cd ~/Desktop/samsu
./samsu status          # model server + web UI should both be running
```

If either is stopped:

```bash
./samsu web             # starts llama-server too; leave this terminal open and visible
```

In a second terminal:

```bash
./samsu game            # serves the game on :8100
```

Reset the demo workspace so the bug is present and the tree is clean:

```bash
cd ~/Desktop/bot-test && git checkout -- . && git clean -fdq && git status --short
```

Open on the Mac, ready to switch to:

- Terminal running `./samsu web` (proves it is **one** process)
- <http://127.0.0.1:8000> — samsu web UI
- <http://127.0.0.1:8100> — the game

On the phone: the Telegram chat with **@rifat_samsu_bot**, scrolled to the bottom.

**Screen layout that sells it:** terminal on the left, browser on the right, phone in hand.
The audience should be able to see all three at once.

---

## Act 1 — The game (2 min)

Opens with something visual before any explanation.

| Do | They see | It proves |
|---|---|---|
| Open <http://127.0.0.1:8100> | Title screen, a 3D ship rotating over a starfield | Real 3D, not sprites |
| Click through the 5 ship cards | Model swaps, UI recolours, stat bars animate | 5 distinct ships with real stats |
| Click **Launch**, play ~30s | Laser, explosions, thrust rumble; rocks split when shot | Sound effects, physics, splitting |
| Let yourself die, or press **P** | Overlay with score | Full game loop |

**The line to say:** "Three.js is vendored locally and every sound is synthesised at runtime
with the Web Audio API — no audio files, no CDN, no build step. Unplug the network and it
still runs."

### The self-playing finale (optional, strong)

```
http://127.0.0.1:8100/?debug&autoplay&level=3
```

It launches itself and flies level 3 — 13 asteroids and 4 hunter drones — hands off.

**The line to say:** "This machine has no Node and no headless browser, and screen recording
is disabled, so I couldn't automate a browser to test this. The page reports its own state
back through the static server's access log instead. That's how I verified 120 fps, the
collision handling and the drone AI without ever being able to script the browser."

---

## Act 2 — The bot is authenticated (2 min)

From the phone, send:

```
/status
```

| They see | It proves |
|---|---|
| `model server : up` | The bot is talking to the local Qwen3-4B |
| `role : owner` | An authenticated identity, not an open endpoint |
| `file tools : off` | **Say nothing yet — this is the setup for Act 3** |

Then ask it something ordinary:

```
what model are you running on?
```

A normal reply comes back. The model is running on your laptop; the phone is just a
terminal onto it.

---

## Act 3 — The authorisation is real (3 min)

This is the act that separates a working demo from a convincing one. Everything here is a
*negative* proof — showing what the system refuses to do.

### 3a. Two conditions, not one

```
/dir ~/Desktop/bot-test
```
```
/status
```

`file tools` flips **off → on**.

**The line to say:** "It said off a moment ago even though I'm the owner. File tools need
two independent conditions — owner role *and* a bound workspace. Either one alone gets you
nothing. That's deliberate: role alone isn't enough, because tools with no workspace have
no safe root to resolve paths against."

### 3b. A stranger is refused

Have **someone else in the room** open `t.me/rifat_samsu_bot` and send anything.

They get:

> This samsu instance is private.
> If you are meant to have access, ask whoever is running it for a pairing code, then send: /pair CODE-CODE

Then from your phone:

```
/who
```

Their account appears as **pending** — logged, but with no access.

```
/audit
```

Shows the `unauthorised` attempt with a timestamp.

**The line to say:** "Telegram will deliver a message from anyone who finds the bot. This is
the only part of the system reachable from outside the laptop, so nothing reaches the model
until it has been through that gate."

### 3c. Granting limited access

```
/invite
```

Gives a 15-minute single-use code granting **conversation-only** access. Have them pair
with it, then have them try:

```
/dir /etc
```

They get: **"That command is owner-only."**

**The line to say:** "They can talk to the model. They cannot touch the filesystem, and the
tool definitions are never even sent to the model for their account — so there is nothing
for the model to call."

### 3d. Confinement (if asked how the sandbox holds)

```bash
cd ~/Desktop/samsu
./.venv/bin/python -c "
from server.workspace import Workspace, WorkspaceError
ws = Workspace('$HOME/Desktop/bot-test')
for p in ['../../../../etc/passwd', '/etc/passwd', 'sub/../../../etc/hosts']:
    try: ws.read_file(p); print('LEAKED', p)
    except WorkspaceError as e: print('blocked:', p, '->', e)
"
```

All three are refused. Absolute paths, `..` traversal and symlinks pointing out of the tree
are all rejected at a single choke point.

---

## Act 4 — Both at the same time (4 min) — the climax

### Set it running from the phone

Send this **exact** message (the "workspace root" wording matters — without it the model
scatters files into subdirectories):

```
Read inventory.py first. Then create these files directly in the workspace root, not in any subdirectory: stock.py with add_item(name, qty) and remove_item(name, qty); report.py with low_stock() returning names with quantity under 3; test_stock.py with tests for add_item and remove_item; and README.md describing all of it.
```

**Rehearsed: ~29 seconds, 5 tool calls.** You have about 25 seconds of working time.

### While it is still running

1. **Point at the phone** — one message editing itself in place:
   `✓ read_file` → `✓ write_file — Created stock.py` → … with a live timer
2. **Switch to the browser** at <http://127.0.0.1:8000> — a chat titled **📱 Read inventory.py
   first…** appears in the sidebar. That is the phone's conversation, live.
3. **Type a message in the browser chat** and send it. It answers, while the phone job runs.
4. **Point at the terminal** — `./samsu status` shows **one** web UI process.

### Then land it

```bash
cd ~/Desktop/bot-test && ls && git status --short
```

Four new files that did not exist 30 seconds ago, created from a phone.

**The line to say:** "One process is serving the browser and the phone. They share one
SQLite file, one HTTP pool and one llama-server. Agent turns are serialised in-process so
tool rounds don't interleave against a single model slot — but the web UI deliberately does
*not* take that lock, so the browser never blocks behind a long build from the phone.
Measured: 31 requests served during a concurrent run, slowest 44 ms."

### If you want the shortest possible version of Act 4

```
total_items() in inventory.py is meant to return the total quantity of all items, not how many kinds there are. Read the file and fix it.
```

~15 seconds, 2 tool calls. Then `git diff` shows a one-line surgical fix:
`return len(items)` → `return sum(items.values())`. Good if you are short on time, but too
fast to demonstrate concurrency.

---

## Reset between runs

```bash
cd ~/Desktop/bot-test && git checkout -- . && git clean -fdq
```

To hand out a fresh owner code (e.g. demoing on someone else's phone):

```bash
cd ~/Desktop/samsu
./.venv/bin/python -c "
from server import auth, db
db.init(); auth.init()
print('/pair', auth.new_code('owner', 60))"
```

---

## If something goes wrong

| Symptom | Fix |
|---|---|
| Bot silent | Is `./samsu web` still running? Check that terminal for `telegram : @rifat_samsu_bot connected` |
| `model server : unreachable` in `/status` | `./samsu serve` |
| Bot says "Still working on the last one" | A previous turn is unfinished — wait, or `/new` |
| Model scatters files into subdirectories | Expected 4B drift. Use the exact Act 4 wording, which pins the root |
| Game is blank | Wrong port. It is **:8100**, not :8000 |
| No sound in the game | Browsers block audio until a real click. Click **Launch**, not just the page |
| Telegram `Conflict` in terminal | Two processes polling the same bot. `./samsu stop`, then `./samsu web` once |

---

## Honest caveats, if you are asked

Worth saying before someone finds them, because they read as confidence rather than
weakness:

- **Qwen3-4B is small.** It handles single-file work reliably. On multi-file work it drifts —
  in rehearsal it put files in subdirectories until the prompt pinned the location. Treat
  its output as a draft to review, which is why the demo workspace is under git.
- **The bot ends the offline property.** Everything else binds `127.0.0.1`. The bot long-polls
  Telegram's servers, and messages transit their infrastructure. That is exactly why the
  authorisation work in Act 3 exists.
- **The agent cannot run code.** It writes files but never executes them, so it never sees a
  test fail. Adding a sandboxed `run_command` tool is the single biggest improvement
  available, and the next thing worth building.
