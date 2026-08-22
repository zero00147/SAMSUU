/* App shell: fetch helper, chat selection, sidebar collapse, health polling. */

async function api(path, method = 'GET', body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opts);
  if (!resp.ok) throw new Error(`${method} ${path} → ${resp.status}`);
  return resp.status === 204 ? null : resp.json();
}

const App = (() => {
  const appEl = document.querySelector('.app');
  let chatId = null;

  async function open(id) {
    if (Chat.streaming()) Chat.stop();
    chatId = id;
    if (location.hash.slice(1) !== id) location.hash = id;
    Sidebar.render();
    await Chat.load(id);
  }

  /* "New chat" is intentionally lazy — no empty chat row is created until the
     first message is actually sent. */
  function openNew() {
    if (Chat.streaming()) Chat.stop();
    chatId = null;
    if (location.hash) location.hash = '';
    Sidebar.render();
    Chat.reset();
  }

  async function ensureChat() {
    if (chatId) return chatId;
    const chat = await api('/api/chats', 'POST');
    chatId = chat.id;
    location.hash = chat.id;
    await Sidebar.refresh();
    return chatId;
  }

  async function pollHealth() {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    try {
      const h = await api('/api/health');
      if (h.ok) {
        dot.className = 'status-dot ok';
        text.textContent = `${h.model.split('/').pop()} · ${h.n_ctx} ctx`;
      } else {
        dot.className = 'status-dot bad';
        text.textContent = 'model server offline';
      }
    } catch {
      dot.className = 'status-dot bad';
      text.textContent = 'app server offline';
    }
  }

  document.getElementById('new-chat-btn').addEventListener('click', openNew);
  document.getElementById('collapse-btn')
    .addEventListener('click', () => appEl.classList.add('collapsed'));
  document.getElementById('expand-btn')
    .addEventListener('click', () => appEl.classList.remove('collapsed'));

  window.addEventListener('hashchange', () => {
    const id = location.hash.slice(1);
    if (id && id !== chatId) open(id);
  });

  (async function init() {
    await Sidebar.refresh();
    const initial = location.hash.slice(1);
    if (initial) {
      try { await open(initial); } catch { openNew(); }
    } else {
      Chat.reset();
    }
    pollHealth();
    setInterval(pollHealth, 15000);
    document.getElementById('input').focus();
  })();

  return {
    open,
    openNew,
    ensureChat,
    get chatId() { return chatId; },
  };
})();
