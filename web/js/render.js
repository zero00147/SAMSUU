/* Markdown rendering + sanitising. Model output is untrusted input even locally,
   so everything passes through DOMPurify before it touches innerHTML. */

marked.setOptions({ breaks: true, gfm: true });

/* Qwen3 reaches for LaTeX on anything numeric. There is no math renderer here (KaTeX
   would mean megabytes of vendored fonts, against the lean/offline goal), so the system
   prompt asks for plain text and this normalises whatever still slips through into
   Unicode. Code blocks are protected so real LaTeX source is never mangled. */
const LATEX_SYMBOLS = [
  [/\\times/g, '×'], [/\\cdot/g, '·'], [/\\div/g, '÷'],
  [/\\pm/g, '±'], [/\\leq/g, '≤'], [/\\geq/g, '≥'], [/\\neq/g, '≠'],
  [/\\approx/g, '≈'], [/\\rightarrow/g, '→'], [/\\to/g, '→'],
  [/\\alpha/g, 'α'], [/\\beta/g, 'β'], [/\\pi/g, 'π'], [/\\theta/g, 'θ'],
  [/\\sqrt\s*\{([^{}]*)\}/g, '√($1)'],
  [/\\d?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, '($1)/($2)'],
  [/\\(?:text|mathrm|mathbf|textbf|operatorname)\s*\{([^{}]*)\}/g, '$1'],
  [/\\left|\\right/g, ''],
  [/\\[,;:!]/g, ' '],
];

function stripLatex(text) {
  // \( \) and \[ \] delimiters
  let out = text.replace(/\\\(|\\\)|\\\[|\\\]/g, '');
  // $$…$$ and $…$ — only when the span is actually math. A digit alone is not
  // enough: "costing $5 and $10" would otherwise be eaten as a math span.
  out = out.replace(/\$\$([\s\S]+?)\$\$/g, '$1');
  out = out.replace(/\$([^$\n]{1,200}?)\$/g, (m, inner) => {
    const hasLatex = /[\\^_{}]/.test(inner);
    const pureMath = /^[\d\s+\-*/=().,×·÷<>]+$/.test(inner);
    return hasLatex || pureMath ? inner : m;
  });
  for (const [re, to] of LATEX_SYMBOLS) out = out.replace(re, to);
  // Removing \text{…} leaves doubled spaces; collapse them without touching
  // leading indentation (which markdown uses for nesting).
  return out.replace(/(\S) {2,}/g, '$1 ');
}

/* Apply fn to the text between fenced/inline code spans, leaving code untouched. */
function outsideCode(text, fn) {
  const parts = text.split(/(```[\s\S]*?```|`[^`\n]*`)/g);
  return parts.map((p, i) => (i % 2 ? p : fn(p))).join('');
}

function renderMarkdown(text) {
  const cleaned = outsideCode(text || '', stripLatex);
  const raw = marked.parse(cleaned);
  return DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
}

/* ---- code block download ------------------------------------------------
   The web UI has no filesystem access (file tools are CLI-only), so a generated
   file arrives as a fenced block. These helpers turn that block back into a real
   file the browser can save, guessing a sensible name rather than "download.txt". */

const CODE_EXT = {
  sql: 'sql', python: 'py', py: 'py', javascript: 'js', js: 'js', jsx: 'jsx',
  typescript: 'ts', ts: 'ts', tsx: 'tsx', json: 'json', yaml: 'yml', yml: 'yml',
  html: 'html', css: 'css', scss: 'scss', bash: 'sh', sh: 'sh', shell: 'sh',
  zsh: 'sh', markdown: 'md', md: 'md', c: 'c', h: 'h', cpp: 'cpp', java: 'java',
  go: 'go', rust: 'rs', rs: 'rs', ruby: 'rb', php: 'php', swift: 'swift',
  kotlin: 'kt', toml: 'toml', ini: 'ini', xml: 'xml', csv: 'csv', diff: 'diff',
  text: 'txt', plaintext: 'txt',
};

// A filename must contain a letter and end in a short extension, so "1.2s" and
// "e.g" in surrounding prose are not mistaken for one.
const FILENAME_RE = /\b([A-Za-z0-9_-]*[A-Za-z][A-Za-z0-9_-]*\.[A-Za-z0-9]{1,8})\b/;

/* Language tag marked put on the <code> element, e.g. "language-sql". */
function blockLang(pre) {
  const code = pre.querySelector('code');
  const cls = (code && code.className) || '';
  const m = cls.match(/language-([\w+-]+)/);
  return m ? m[1].toLowerCase() : '';
}

/* Models label a generated file either in a comment on the first lines
   ("-- schema.sql") or in the sentence just above the fence. Try both, and only
   accept a name whose extension agrees with the fence language — otherwise a
   passing mention of README.md would rename an SQL file. */
function guessFilename(pre, wrap) {
  const lang = blockLang(pre);
  const ext = CODE_EXT[lang] || 'txt';

  const heads = pre.innerText.split('\n').slice(0, 3)
    .filter((l) => /^\s*(--|#|\/\/|\/\*|<!--|;)/.test(l));
  const prev = wrap.previousElementSibling;
  const sources = [...heads, prev ? prev.textContent.slice(-160) : ''];

  for (const text of sources) {
    const m = text.match(FILENAME_RE);
    if (!m) continue;
    const name = m[1];
    const found = name.split('.').pop().toLowerCase();
    if (!CODE_EXT[lang] || found === ext) return name;
  }
  return `snippet.${ext}`;
}

function downloadText(filename, text) {
  const url = URL.createObjectURL(
    new Blob([text], { type: 'text/plain;charset=utf-8' })
  );
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoked on the next tick — revoking synchronously can cancel the download.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* Wrap each <pre> in a container with copy and download buttons. */
function decorateCodeBlocks(root) {
  root.querySelectorAll('pre').forEach((pre) => {
    if (pre.parentElement.classList.contains('code-block')) return;

    const wrap = document.createElement('div');
    wrap.className = 'code-block';
    pre.parentNode.insertBefore(wrap, pre);
    wrap.appendChild(pre);

    const bar = document.createElement('div');
    bar.className = 'code-tools';

    const name = guessFilename(pre, wrap);

    const save = document.createElement('button');
    save.className = 'code-btn';
    save.textContent = `Download ${name}`;
    save.title = `Save this block as ${name}`;
    save.addEventListener('click', () => {
      downloadText(name, pre.innerText);
      save.textContent = 'Saved';
      setTimeout(() => { save.textContent = `Download ${name}`; }, 1400);
    });

    const btn = document.createElement('button');
    btn.className = 'code-btn';
    btn.textContent = 'Copy';
    btn.addEventListener('click', () => {
      navigator.clipboard.writeText(pre.innerText);
      btn.textContent = 'Copied';
      setTimeout(() => { btn.textContent = 'Copy'; }, 1400);
    });

    bar.append(save, btn);
    wrap.appendChild(bar);
  });
}

function setMarkdown(el, text) {
  el.innerHTML = renderMarkdown(text);
  decorateCodeBlocks(el);
}

function icon(paths) {
  return `<svg viewBox="0 0 24 24" width="13" height="13">${paths}</svg>`;
}

const ICONS = {
  copy: icon('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>'),
  refresh: icon('<path d="M21 12a9 9 0 1 1-2.6-6.4M21 3v6h-6"/>'),
  edit: icon('<path d="M4 20h4L20 8l-4-4L4 16v4z"/>'),
  trash: icon('<path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13h10l1-13"/>'),
  pencil: icon('<path d="M4 20h4L20 8l-4-4L4 16v4z"/>'),
};
