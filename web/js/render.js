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

/* Wrap each <pre> in a container with a copy button. */
function decorateCodeBlocks(root) {
  root.querySelectorAll('pre').forEach((pre) => {
    if (pre.parentElement.classList.contains('code-block')) return;

    const wrap = document.createElement('div');
    wrap.className = 'code-block';
    pre.parentNode.insertBefore(wrap, pre);
    wrap.appendChild(pre);

    const btn = document.createElement('button');
    btn.className = 'code-copy';
    btn.textContent = 'Copy';
    btn.addEventListener('click', () => {
      navigator.clipboard.writeText(pre.innerText);
      btn.textContent = 'Copied';
      setTimeout(() => { btn.textContent = 'Copy'; }, 1400);
    });
    wrap.appendChild(btn);
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
