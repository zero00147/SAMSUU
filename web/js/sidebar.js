/* Chat list: select, rename inline, delete. */

const Sidebar = (() => {
  const list = document.getElementById('chat-list');
  let chats = [];

  function render() {
    list.innerHTML = '';
    chats.forEach((chat) => {
      const item = document.createElement('div');
      item.className = 'chat-item' + (chat.id === App.chatId ? ' active' : '');

      const title = document.createElement('div');
      title.className = 'chat-title';
      title.textContent = chat.title;
      item.appendChild(title);

      const actions = document.createElement('div');
      actions.className = 'chat-actions';

      const rename = document.createElement('button');
      rename.innerHTML = ICONS.pencil;
      rename.title = 'Rename';
      rename.addEventListener('click', (e) => {
        e.stopPropagation();
        beginRename(title, chat);
      });

      const del = document.createElement('button');
      del.innerHTML = ICONS.trash;
      del.title = 'Delete';
      del.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete "${chat.title}"?`)) return;
        await api(`/api/chats/${chat.id}`, 'DELETE');
        if (App.chatId === chat.id) App.openNew();
        refresh();
      });

      actions.append(rename, del);
      item.appendChild(actions);

      item.addEventListener('click', () => App.open(chat.id));
      list.appendChild(item);
    });
  }

  function beginRename(titleEl, chat) {
    titleEl.contentEditable = 'true';
    titleEl.focus();
    document.execCommand('selectAll', false, null);

    const finish = async (save) => {
      titleEl.contentEditable = 'false';
      const next = titleEl.textContent.trim();
      if (save && next && next !== chat.title) {
        await api(`/api/chats/${chat.id}`, 'PATCH', { title: next });
        refresh();
      } else {
        titleEl.textContent = chat.title;
      }
    };

    titleEl.addEventListener('blur', () => finish(true), { once: true });
    titleEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); titleEl.blur(); }
      if (e.key === 'Escape') { titleEl.textContent = chat.title; titleEl.blur(); }
    });
  }

  async function refresh() {
    chats = await api('/api/chats');
    render();
  }

  return { refresh, render };
})();
