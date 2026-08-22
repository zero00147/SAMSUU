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

      const actions = document.createElement('div');
      actions.className = 'actions';
      const edit = document.createElement('button');
      edit.innerHTML = `${ICONS.edit} Edit`;
      edit.addEventListener('click', () => startEdit(el, msg));
      actions.appendChild(edit);
      el.appendChild(actions);
    } else {
      if (msg.thinking) el.appendChild(thinkingEl(msg.thinking));

      const body = document.createElement('div');
      body.className = 'body';
      setMarkdown(body, msg.content);
      el.appendChild(body);

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

  function thinkingEl(text, open = false) {
    const d = document.createElement('details');
    d.className = 'thinking';
    if (open) d.open = true;
    const s = document.createElement('summary');
    s.textContent = 'Thought process';
    const b = document.createElement('div');
    b.className = 'thinking-body';
    b.textContent = text;
    d.append(s, b);
    return d;
  }

  function render() {
    thread.innerHTML = '';
    messages.forEach((m) => thread.appendChild(messageEl(m)));
    empty.classList.toggle('hidden', messages.length > 0);
    toBottom();
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

  async function send(text) {
    if (streaming() || !App.chatId) return;

    controller = new AbortController();
    appEl.classList.add('streaming');
    empty.classList.add('hidden');

    // optimistic user turn; the server echoes the authoritative row back
    const optimistic = {
      seq: messages.length ? messages[messages.length - 1].seq + 1 : 0,
      role: 'user',
      content: text,
    };
    messages.push(optimistic);
    thread.appendChild(messageEl(optimistic));
    toBottom();

    // assistant placeholder we stream into
    const el = document.createElement('div');
    el.className = 'msg assistant';
    const body = document.createElement('div');
    body.className = 'body cursor';
    el.appendChild(body);
    thread.appendChild(el);
    toBottom();

    let content = '';
    let thinking = '';
    let thinkNode = null;

    try {
      const resp = await fetch(`/api/chats/${App.chatId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: text, thinking: thinkingOn() }),
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
            thinking += ev.delta;
            if (!thinkNode) {
              thinkNode = thinkingEl('', true);
              el.insertBefore(thinkNode, body);
            }
            thinkNode.querySelector('.thinking-body').textContent = thinking;
          } else if (ev.type === 'content') {
            if (thinkNode) thinkNode.open = false;
            content += ev.delta;
            setMarkdown(body, content);
            body.classList.add('cursor');
          } else if (ev.type === 'error') {
            const err = document.createElement('div');
            err.className = 'error-box';
            err.textContent = `Generation failed: ${ev.error}`;
            el.appendChild(err);
          }
          if (stick) toBottom();
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
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

      // Re-sync with the server so seq values and the persisted (possibly
      // partial) assistant message are authoritative.
      await load(App.chatId);
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
    input.value = '';
    autogrow();
    App.ensureChat().then(() => send(text));
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
