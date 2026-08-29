/* Document upload and section attachment.

   The PRD is ~12.5k tokens against an 8k window, so whole-document attachment is
   impossible. Documents are split server-side by heading; you attach the one or two
   sections the current task needs and the running total is checked against the
   prompt budget before you can send. */

const Docs = (() => {
  const overlay = document.getElementById('docs-overlay');
  const list = document.getElementById('docs-list');
  const fileInput = document.getElementById('file-input');
  const uploadText = document.getElementById('upload-text');
  const budgetBar = document.getElementById('budget-bar');
  const chips = document.getElementById('chips');
  const budgetNote = document.getElementById('budget-note');

  let budget = 5888;
  let docs = [];
  const sectionCache = {};              // docId -> sections
  let selected = new Map();             // sectionId -> {heading, filename, tokens}

  const fmt = (n) => n.toLocaleString();

  // --- budget ------------------------------------------------------------

  function attachedTokens() {
    let n = 0;
    for (const s of selected.values()) n += s.tokens;
    return n;
  }

  function over() { return attachedTokens() > budget; }

  function renderBudget() {
    const used = attachedTokens();
    if (!selected.size) {
      budgetBar.textContent = 'Nothing attached';
      budgetBar.className = 'budget';
    } else {
      budgetBar.textContent =
        `${selected.size} section${selected.size > 1 ? 's' : ''} · ${fmt(used)} / ${fmt(budget)} tokens`;
      budgetBar.className = 'budget' + (over() ? ' over' : '');
    }
    renderChips();
  }

  function renderChips() {
    chips.innerHTML = '';
    if (!selected.size) {
      budgetNote.textContent = '';
      return;
    }
    for (const [id, s] of selected) {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.innerHTML =
        `<span class="chip-text">${s.heading}</span>` +
        `<span class="chip-tok">${fmt(s.tokens)}</span>`;
      const x = document.createElement('button');
      x.textContent = '×';
      x.setAttribute('aria-label', `Remove ${s.heading}`);
      x.addEventListener('click', () => { selected.delete(id); renderBudget(); renderList(); });
      chip.appendChild(x);
      chips.appendChild(chip);
    }
    const used = attachedTokens();
    budgetNote.innerHTML = over()
      ? `<b class="over">${fmt(used)} / ${fmt(budget)} tokens — too large to send.</b> `
      : `${fmt(used)} / ${fmt(budget)} tokens attached. `;
  }

  // --- rendering ---------------------------------------------------------

  async function renderList() {
    list.innerHTML = '';
    if (!docs.length) {
      list.innerHTML = '<div class="docs-empty">No documents yet.</div>';
      return;
    }
    for (const doc of docs) {
      const wrap = document.createElement('div');
      wrap.className = 'doc';

      const head = document.createElement('div');
      head.className = 'doc-head';
      head.innerHTML =
        `<span class="doc-name">${doc.filename}</span>` +
        `<span class="doc-meta">${doc.section_count} sections · ${fmt(doc.total_tokens)} tok</span>`;

      const del = document.createElement('button');
      del.className = 'doc-del';
      del.innerHTML = ICONS.trash;
      del.title = 'Delete document';
      del.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete "${doc.filename}"?`)) return;
        await api(`/api/documents/${doc.id}`, 'DELETE');
        for (const [id, s] of [...selected]) {
          if (s.docId === doc.id) selected.delete(id);
        }
        await refresh();
        renderBudget();
      });
      head.appendChild(del);

      const body = document.createElement('div');
      body.className = 'doc-sections';

      head.addEventListener('click', async () => {
        const open = wrap.classList.toggle('open');
        if (open && !body.childElementCount) {
          body.innerHTML = '<div class="docs-empty">Loading…</div>';
          const sections = sectionCache[doc.id]
            || (sectionCache[doc.id] = await api(`/api/documents/${doc.id}/sections`));
          body.innerHTML = '';
          sections.forEach((s) => body.appendChild(sectionRow(doc, s)));
        }
      });

      wrap.append(head, body);
      list.appendChild(wrap);
    }
  }

  function sectionRow(doc, s) {
    const row = document.createElement('label');
    row.className = 'section-row' + (s.tokens > budget ? ' too-big' : '');
    row.style.paddingLeft = `${8 + (s.level - 1) * 14}px`;

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = selected.has(s.id);
    cb.disabled = s.tokens > budget;
    cb.addEventListener('change', () => {
      if (cb.checked) {
        selected.set(s.id, {
          heading: s.heading, filename: doc.filename, tokens: s.tokens, docId: doc.id,
        });
      } else {
        selected.delete(s.id);
      }
      renderBudget();
    });

    const name = document.createElement('span');
    name.className = 'section-name';
    name.textContent = s.heading;

    const tok = document.createElement('span');
    tok.className = 'section-tok';
    tok.textContent = s.tokens > budget ? `${fmt(s.tokens)} — too big` : fmt(s.tokens);

    row.append(cb, name, tok);
    return row;
  }

  // --- upload ------------------------------------------------------------

  async function upload(file) {
    uploadText.textContent = `Reading ${file.name}…`;
    const form = new FormData();
    form.append('file', file);
    try {
      const resp = await fetch('/api/documents', { method: 'POST', body: form });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || `upload failed (${resp.status})`);
      uploadText.textContent =
        `Added ${data.filename} — ${data.section_count} sections, ${fmt(data.total_tokens)} tokens`;
      await refresh();
    } catch (err) {
      uploadText.textContent = `Could not read that file: ${err.message}`;
    }
    setTimeout(() => {
      uploadText.textContent = 'Upload a document — PDF, DOCX, MD, TXT';
    }, 6000);
  }

  // --- lifecycle ---------------------------------------------------------

  async function refresh() {
    docs = await api('/api/documents');
    for (const k of Object.keys(sectionCache)) {
      if (!docs.some((d) => d.id === k)) delete sectionCache[k];
    }
    await renderList();
  }

  async function open() {
    overlay.hidden = false;
    try {
      const b = await api('/api/budget');
      budget = b.budget;
    } catch { /* keep default */ }
    await refresh();
    renderBudget();
  }

  function close() { overlay.hidden = true; }

  /* Consumed by chat.js on send, then cleared. */
  function takeSelection() {
    const ids = [...selected.keys()];
    selected.clear();
    renderBudget();
    renderList();
    return ids;
  }

  document.getElementById('attach-btn').addEventListener('click', open);
  document.getElementById('docs-close').addEventListener('click', close);
  document.getElementById('docs-done').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !overlay.hidden) close();
  });

  fileInput.addEventListener('change', () => {
    if (fileInput.files[0]) upload(fileInput.files[0]);
    fileInput.value = '';
  });

  const zone = document.getElementById('upload-zone');
  ['dragover', 'dragenter'].forEach((ev) =>
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach((ev) =>
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
  zone.addEventListener('drop', (e) => {
    if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
  });

  return { takeSelection, over, attachedTokens };
})();
