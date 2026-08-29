/* Thread rendering, SSE streaming, and stop / regenerate / edit. */

const Chat = (() => {
  const thread = document.getElementById('thread');
  const empty = document.getElementById('empty');
  const scroll = document.getElementById('scroll');
  const input = document.getElementById('input');
  const sendBtn = document.getElementById('send-btn');
  const thinkBtn = document.getElementById('think-btn');
  const appEl = document.querySelector('.app');

  const thinkingOn = () => thinkBtn.getAttribute('aria-pressed') === 'true';

  let messages = [];        // rows as stored server-side
  let controller = null;    // AbortController for the in-flight stream

  const streaming = () => controller !== null;

  function atBottom() {
    return scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 120;
  }
  function toBottom() {
    scroll.scrollTop = scroll.scrollHeight;
  }

  // --- rendering ---------------------------------------------------------

  function messageEl(msg) {
    const el = document.createElement('div');
    el.className = `msg ${msg.role}`;
    el.dataset.seq = msg.seq;

    if (msg.role === 'user') {
      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      bubble.textContent = msg.content;
      el.appendChild(bubble);

      const note = attachNote(msg);
      if (note) el.appendChild(note);

      const actions = document.createElement('div');
      actions.className = 'actions';
      const edit = document.createElement('button');
      edit.innerHTML = `${ICONS.edit} Edit`;
      edit.addEventListener('click', () => startEdit(el, msg));
      actions.appendChild(edit);
      el.appendChild(actions);
    } else {
      if (msg.thinking) {
        const t = thinkingEl(msg.thinking, false, msg.think_ms);
        // A row with reasoning but no answer text (the run spent its whole output
        // budget thinking) would otherwise render as an empty bubble.
        if (!msg.content) t.open = true;
        el.appendChild(t);
      }

      const body = document.createElement('div');
      body.className = 'body';
      setMarkdown(body, msg.content);
      el.appendChild(body);

      if (!msg.content) {
        const note = document.createElement('div');
        note.className = 'empty-reply';
        note.textContent = msg.thinking
          ? 'No answer text — the reply ran out of output budget while reasoning. '
            + 'The reasoning is above; turn Think off, or ask for a smaller piece, and retry.'
          : 'The model returned nothing. Retry, or check that llama-server is still up.';
        el.appendChild(note);
      }

      const stats = statsEl(msg);
      if (stats) el.appendChild(stats);

      const actions = document.createElement('div');
      actions.className = 'actions';

      const copy = document.createElement('button');
      copy.innerHTML = `${ICONS.copy} Copy`;
      copy.addEventListener('click', () => {
        navigator.clipboard.writeText(msg.content);
        copy.innerHTML = `${ICONS.copy} Copied`;
        setTimeout(() => { copy.innerHTML = `${ICONS.copy} Copy`; }, 1400);
      });

      const regen = document.createElement('button');
      regen.innerHTML = `${ICONS.refresh} Retry`;
      regen.addEventListener('click', () => regenerate(msg.seq));

      actions.append(copy, regen);
      el.appendChild(actions);
    }
    return el;
  }

  const secs = (ms) => (ms / 1000).toFixed(1) + 's';

  /* Attached sections are stored separately from the message text so the bubble
     stays readable; this just records what was sent with the turn. */
  function attachNote(msg) {
    if (!msg.attachments) return null;
    let items;
    try { items = JSON.parse(msg.attachments); } catch { return null; }
    if (!items || !items.length) return null;
    const el = document.createElement('div');
    el.className = 'attach-note';
    el.textContent = '📎 ' + items.map((a) => a.label).join(', ');
    return el;
  }

  /* `live` means the model is reasoning right now. It opens the block and gives it
     its own colour, so "still thinking" is distinguishable at a glance from
     "finished thinking, this is the answer". */
  function thinkingEl(text, live = false, thinkMs = null) {
    const d = document.createElement('details');
    d.className = live ? 'thinking live' : 'thinking';
    if (live) d.open = true;
    const s = document.createElement('summary');
    s.textContent = live
      ? 'Thinking…'
      : (thinkMs ? `Thought for ${secs(thinkMs)}` : 'Thought process');
    const b = document.createElement('div');
    b.className = 'thinking-body';
    b.textContent = text;
    d.append(s, b);
    return d;
  }

  function settleThinking(node, startedAt) {
    if (!node) return;
    node.open = false;
    node.classList.remove('live');
    node.querySelector('summary').textContent =
      `Thought for ${secs(performance.now() - startedAt)}`;
  }

  /* Timing line under an assistant reply. Older messages predate these columns,
     so every field is optional and the whole element is skipped if empty. */
  function statsEl(msg) {
    const bits = [];
    if (msg.duration_ms) bits.push(secs(msg.duration_ms));
    if (msg.tokens) bits.push(`${msg.tokens} tokens`);
    if (msg.tokens_per_sec) bits.push(`${msg.tokens_per_sec} tok/s`);
    if (!bits.length && !msg.stopped) return null;

    const el = document.createElement('div');
    el.className = 'msg-stats';
    el.innerHTML = bits.join('<span class="sep">·</span>');
    if (msg.stopped) {
      const flag = document.createElement('span');
      flag.className = 'stopped-flag';
      flag.textContent = bits.length ? ' · stopped early' : 'stopped early';
      el.appendChild(flag);
    }
    if (msg.ttft_ms) {
      el.title = `First token after ${secs(msg.ttft_ms)}`
        + (msg.think_ms ? ` · reasoned for ${secs(msg.think_ms)}` : '');
    }
    return el;
  }

  function render() {
    thread.innerHTML = '';
    messages.forEach((m) => thread.appendChild(messageEl(m)));
    empty.classList.toggle('hidden', messages.length > 0);
    toBottom();
  }

  /* The re-sync after a stream replaces the live element, so the moment it stops
     being "generating" coloured would pass unseen. Mark the settled reply in its
     outcome colour for a couple of seconds instead. */
  function flashLast(outcome) {
    const last = thread.lastElementChild;
    if (!last || !last.classList.contains('assistant')) return;
    last.dataset.settled = outcome || 'stopped';
    setTimeout(() => { delete last.dataset.settled; }, 2200);
  }

  // --- edit / regenerate -------------------------------------------------

  function startEdit(el, msg) {
    if (streaming()) return;
    el.innerHTML = '';

    const ta = document.createElement('textarea');
    ta.className = 'edit-area';
    ta.value = msg.content;
    el.appendChild(ta);

    const row = document.createElement('div');
    row.className = 'edit-row';

    const cancel = document.createElement('button');
    cancel.className = 'btn';
    cancel.textContent = 'Cancel';
    cancel.addEventListener('click', render);

    const save = document.createElement('button');
    save.className = 'btn primary';
    save.textContent = 'Send';
    save.addEventListener('click', () => {
      const text = ta.value.trim();
      if (text) resendFrom(msg.seq, text);
    });

    row.append(cancel, save);
    el.appendChild(row);

    ta.style.height = ta.scrollHeight + 'px';
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
  }

  /* Edit and regenerate are the same primitive: drop everything from `seq`
     onward, then re-send a user turn. */
  async function resendFrom(seq, text) {
    if (streaming()) return;
    await api(`/api/chats/${App.chatId}/truncate`, 'POST', { from_seq: seq });
    messages = messages.filter((m) => m.seq < seq);
    render();
    await send(text);
  }

  function regenerate(assistantSeq) {
    const prevUser = [...messages].reverse().find(
      (m) => m.role === 'user' && m.seq < assistantSeq
    );
    if (prevUser) resendFrom(prevUser.seq, prevUser.content);
  }

  // --- streaming ---------------------------------------------------------

  async function send(text, sectionIds = []) {
    if (streaming() || !App.chatId) return;

    controller = new AbortController();
    appEl.classList.add('streaming');
    empty.classList.add('hidden');
    Status.start();

    // optimistic user turn; the server echoes the authoritative row back
    const optimistic = {
      seq: messages.length ? messages[messages.length - 1].seq + 1 : 0,
      role: 'user',
      content: text,
    };
    messages.push(optimistic);
    thread.appendChild(messageEl(optimistic));
    toBottom();

    // assistant placeholder we stream into. `phase` drives the colour of the
    // whole reply window: waiting → reasoning → writing, cleared when it settles.
    const el = document.createElement('div');
    el.className = 'msg assistant live';
    el.dataset.phase = 'waiting';
    const body = document.createElement('div');
    body.className = 'body cursor';
    el.appendChild(body);
    thread.appendChild(el);
    toBottom();

    let content = '';
    let thinking = '';
    let thinkNode = null;
    let thinkStart = 0;
    let outcome = null;   // 'done' | 'stopped' | 'failed' — decided before finally

    try {
      const resp = await fetch(`/api/chats/${App.chatId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: text, thinking: thinkingOn(), section_ids: sectionIds,
        }),
        signal: controller.signal,
      });
      if (!resp.ok) throw new Error(`server returned ${resp.status}`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        const parts = buf.split('\n\n');
        buf = parts.pop();

        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith('data: ')) continue;

          let ev;
          try { ev = JSON.parse(line.slice(6)); } catch { continue; }
          const stick = atBottom();

          if (ev.type === 'user') {
            optimistic.seq = ev.message.seq;
            optimistic.id = ev.message.id;
          } else if (ev.type === 'thinking') {
            Status.setPhase('reasoning');
            el.dataset.phase = 'reasoning';
            thinking += ev.delta;
            if (!thinkNode) {
              thinkStart = performance.now();
              thinkNode = thinkingEl('', true);
              el.insertBefore(thinkNode, body);
            }
            thinkNode.querySelector('.thinking-body').textContent = thinking;
            thinkNode.querySelector('summary').textContent =
              `Thinking… ${secs(performance.now() - thinkStart)}`;
          } else if (ev.type === 'content') {
            Status.setPhase('writing');
            el.dataset.phase = 'writing';
            settleThinking(thinkNode, thinkStart);
            content += ev.delta;
            setMarkdown(body, content);
            body.classList.add('cursor');
          } else if (ev.type === 'done') {
            outcome = 'done';
            Status.finish(ev.stats);
          } else if (ev.type === 'error') {
            outcome = 'failed';
            Status.failed();
            const err = document.createElement('div');
            err.className = 'error-box';
            err.textContent = `Generation failed: ${ev.error}`;
            el.appendChild(err);
          }
          if (stick) toBottom();
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        outcome = 'stopped';
      } else {
        outcome = 'failed';
        const box = document.createElement('div');
        box.className = 'error-box';
        box.textContent =
          `Could not reach the model server. Is llama-server running on :8080? (${err.message})`;
        el.appendChild(box);
      }
    } finally {
      controller = null;
      appEl.classList.remove('streaming');
      body.classList.remove('cursor');
      el.classList.remove('live');
      el.dataset.phase = outcome || 'stopped';

      // Settle the reasoning block, but leave it open when it is all there is —
      // a run that spent every token thinking would otherwise render as a blank reply.
      if (thinkNode) {
        settleThinking(thinkNode, thinkStart);
        if (!content) thinkNode.open = true;
      }

      // The stream can end without a 'done' event (server closed early); treat any
      // unresolved outcome as a stop so the pill never spins forever.
      if (outcome === 'stopped') Status.stopped();
      else if (outcome === 'failed') Status.failed();
      else if (outcome !== 'done') Status.stopped();

      // Re-sync with the server so seq values and the persisted (possibly
      // partial) assistant message are authoritative.
      await load(App.chatId);
      flashLast(outcome);
      Sidebar.refresh();
    }
  }

  function stop() {
    if (controller) controller.abort();
  }

  // --- lifecycle ---------------------------------------------------------

  async function load(chatId) {
    const data = await api(`/api/chats/${chatId}`);
    messages = data.messages || [];
    render();
  }

  function reset() {
    messages = [];
    render();
  }

  // --- composer wiring ---------------------------------------------------

  function autogrow() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 220) + 'px';
  }

  function submit() {
    if (streaming()) { stop(); return; }
    const text = input.value.trim();
    if (!text) return;
    if (Docs.over()) {
      alert(
        'The attached sections are larger than the context window.\n\n'
        + 'Remove some before sending — the model cannot read more than it can hold.'
      );
      return;
    }
    const sectionIds = Docs.takeSelection();
    input.value = '';
    autogrow();
    App.ensureChat().then(() => send(text, sectionIds));
  }

  thinkBtn.addEventListener('click', () => {
    const next = !thinkingOn();
    thinkBtn.setAttribute('aria-pressed', String(next));
    localStorage.setItem('samsu.thinking', String(next));
  });
  thinkBtn.setAttribute(
    'aria-pressed', localStorage.getItem('samsu.thinking') === 'true' ? 'true' : 'false'
  );

  input.addEventListener('input', autogrow);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  });
  sendBtn.addEventListener('click', submit);

  return { load, reset, stop, streaming };
})();
