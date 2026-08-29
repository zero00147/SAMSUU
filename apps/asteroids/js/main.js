// UI layer: hangar menu, HUD, overlays. The engine never touches the DOM — it exposes
// getState() and a few callbacks, and everything below reads from those.

import { audio } from './audio.js';
import { Game } from './game.js';
import { SHIPS } from './ships.js';

const $ = (id) => document.getElementById(id);

const el = {
  menu: $('menu'),
  ships: $('ships'),
  shipName: $('ship-name'),
  shipTagline: $('ship-tagline'),
  shipStats: $('ship-stats'),
  shipNotes: $('ship-notes'),
  launch: $('btn-launch'),
  hud: $('hud'),
  score: $('hud-score'),
  hull: $('hud-hull'),
  level: $('hud-level'),
  contacts: $('hud-contacts'),
  mute: $('btn-mute'),
  overlay: $('overlay'),
  ovTitle: $('ov-title'),
  ovBody: $('ov-body'),
  ovActions: $('ov-actions'),
};

const game = new Game($('scene'));
let selected = SHIPS[0];

// Debug hook: lets the ?debug harness in devlog.js read live engine state. Not attached
// during normal play.
if (location.search.includes('debug')) window.__game = game;

// --- hangar --------------------------------------------------------------

// Stat bars are normalised against the best ship in each column, so the comparison is
// between the five that exist rather than against an arbitrary ceiling.
const STAT_ROWS = [
  { key: 'speed', label: 'Speed', format: (v) => `${v.toFixed(2)}×` },
  { key: 'turn', label: 'Handling', format: (v) => `${v.toFixed(2)}×` },
  { key: 'fireRate', label: 'Fire rate', format: (v) => `${v.toFixed(2)}×` },
  { key: 'hull', label: 'Hull', format: (v) => `${v}` },
];

const MAXES = Object.fromEntries(
  STAT_ROWS.map((r) => [r.key, Math.max(...SHIPS.map((s) => s[r.key]))]),
);

const WEAPON_LABEL = { single: 'Single cannon', twin: 'Twin cannon', spread: '3-way spread' };

function hex(n) {
  return `#${n.toString(16).padStart(6, '0')}`;
}

function buildShipCards() {
  el.ships.innerHTML = '';
  for (const spec of SHIPS) {
    const card = document.createElement('button');
    card.className = 'ship-card';
    card.style.setProperty('--card-accent', hex(spec.color));
    card.innerHTML = `
      <div class="swatch"></div>
      <div class="name">${spec.name}</div>
      <div class="role">${WEAPON_LABEL[spec.weapon]}</div>
    `;
    card.addEventListener('click', () => {
      audio.unlock();
      audio.select();
      select(spec);
    });
    card.dataset.id = spec.id;
    el.ships.appendChild(card);
  }
}

function select(spec) {
  selected = spec;

  for (const card of el.ships.children) {
    card.classList.toggle('selected', card.dataset.id === spec.id);
  }

  el.shipName.textContent = spec.name;
  el.shipTagline.textContent = spec.tagline;
  el.shipNotes.textContent = spec.notes;

  el.shipStats.innerHTML = STAT_ROWS.map((r) => {
    const pct = Math.round((spec[r.key] / MAXES[r.key]) * 100);
    return `
      <div class="stat">
        <span class="k">${r.label}</span>
        <span class="bar"><i style="width:${pct}%"></i></span>
        <span class="v">${r.format(spec[r.key])}</span>
      </div>`;
  }).join('');

  const accent = hex(spec.color);
  el.shipStats.style.setProperty('--card-accent', accent);
  el.launch.style.background = accent;
  el.launch.style.borderColor = accent;
  document.documentElement.style.setProperty('--accent', accent);

  game.showPreview(spec);
}

// --- HUD -----------------------------------------------------------------

// Cached so the loop is not writing identical strings into the DOM 60 times a second.
const shown = { score: -1, hull: -1, level: -1, contacts: -1 };

function refreshHud() {
  const s = game.getState();
  const playing = s.mode !== 'menu';
  el.hud.hidden = !playing;
  if (!playing) return;

  if (s.score !== shown.score) {
    el.score.textContent = s.score.toLocaleString();
    shown.score = s.score;
  }

  if (s.hull !== shown.hull) {
    el.hull.innerHTML = Array.from({ length: s.maxHull }, (_, i) =>
      `<span class="pip${i < s.hull ? '' : ' spent'}"></span>`).join('');
    shown.hull = s.hull;
  }

  if (s.level && s.level.n !== shown.level) {
    el.level.textContent = `${s.level.n} · ${s.level.name}`;
    shown.level = s.level.n;
  }

  const contacts = s.rocks + s.drones;
  if (contacts !== shown.contacts) {
    el.contacts.textContent = contacts;
    shown.contacts = contacts;
  }
}

setInterval(refreshHud, 100);

// --- overlays ------------------------------------------------------------

function showOverlay(title, bodyHtml, actions) {
  el.ovTitle.textContent = title;
  el.ovBody.innerHTML = bodyHtml || '';
  el.ovActions.innerHTML = '';
  for (const a of actions || []) {
    const b = document.createElement('button');
    b.textContent = a.label;
    if (a.primary) b.className = 'primary';
    b.addEventListener('click', a.onClick);
    el.ovActions.appendChild(b);
  }
  el.overlay.hidden = false;
}

function hideOverlay() {
  el.overlay.hidden = true;
}

game.onMessage = (msg) => {
  if (!msg) { hideOverlay(); return; }

  if (msg.kind === 'paused') {
    showOverlay('Paused', 'Press <kbd>P</kbd> to resume.', [
      { label: 'Resume', primary: true, onClick: () => game.setPaused(false) },
      { label: 'Abandon run', onClick: backToMenu },
    ]);
    return;
  }

  if (msg.kind === 'levelclear') {
    showOverlay(
      'Sector cleared',
      `<span class="score">${msg.score.toLocaleString()}</span>`
      + `Next: <strong>${msg.next.name}</strong><br>${msg.next.brief}`,
      [],
    );
    return;
  }

  if (msg.kind === 'won') {
    showOverlay(
      'All sectors clear',
      `<span class="score">${msg.score.toLocaleString()}</span>`
      + `You flew the ${selected.name} through all three sectors and came out the other side.`,
      [
        { label: 'Fly again', primary: true, onClick: () => launch() },
        { label: 'Hangar', onClick: backToMenu },
      ],
    );
  }
};

game.onGameOver = ({ score, level }) => {
  showOverlay(
    'Hull breached',
    `<span class="score">${score.toLocaleString()}</span>`
    + `Lost in sector ${level.n} — ${level.name}.`,
    [
      { label: 'Retry', primary: true, onClick: () => launch() },
      { label: 'Hangar', onClick: backToMenu },
    ],
  );
};

game.onLevelStart = () => {
  // Force a redraw of every cached HUD field on the next tick.
  shown.score = shown.hull = shown.level = shown.contacts = -1;
};

// --- flow ----------------------------------------------------------------

function launch() {
  audio.unlock();
  hideOverlay();
  el.menu.hidden = true;
  game.start(selected);
  refreshHud();
}

function backToMenu() {
  hideOverlay();
  game.toMenu();
  el.menu.hidden = false;
  el.hud.hidden = true;
  game.showPreview(selected);
}

el.launch.addEventListener('click', launch);

el.mute.addEventListener('click', () => {
  audio.unlock();
  const muted = !el.mute.classList.contains('off');
  el.mute.classList.toggle('off', muted);
  el.mute.textContent = muted ? '♪̸' : '♪';
  audio.setMuted(muted);
});

window.addEventListener('keydown', (e) => {
  if (e.code === 'KeyM') el.mute.click();
  // Enter launches straight from the hangar without reaching for the mouse.
  if (e.code === 'Enter' && !el.menu.hidden) launch();
});

// The AudioContext cannot start until the user has interacted with the page, so the
// first click or keypress anywhere is used to open it.
for (const evt of ['pointerdown', 'keydown']) {
  window.addEventListener(evt, () => audio.unlock(), { once: true });
}

buildShipCards();
select(SHIPS[0]);
