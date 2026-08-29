// Opt-in debug beacon, active only with ?debug in the URL.
//
// There is no browser automation on this machine — no Node, no headless driver — so the
// only channel back out of the page is the static server's access log. This reports
// boot, uncaught errors and a liveness heartbeat by requesting URLs that do not exist:
// the server logs the path (and 404s), which is enough to tell whether the game
// actually ran. Off by default, so normal play makes no requests at all.

if (location.search.includes('debug')) {
  const beacon = (kind, detail = '') => {
    const msg = String(detail).replace(/\s+/g, ' ').slice(0, 300);
    // A GET the server will 404 — the point is the log line, not the response.
    fetch(`/__dev/${kind}/${encodeURIComponent(msg)}`).catch(() => {});
  };

  window.addEventListener('error', (e) => {
    beacon('error', `${e.message} @ ${e.filename || '?'}:${e.lineno || 0}`);
  });

  window.addEventListener('unhandledrejection', (e) => {
    beacon('reject', e.reason && e.reason.message ? e.reason.message : e.reason);
  });

  beacon('boot', 'scripts parsed');

  // Liveness: if the render loop is running, frame count climbs. A flat or absent
  // reading means the loop died even though nothing threw.
  let frames = 0;
  const count = () => { frames++; requestAnimationFrame(count); };
  requestAnimationFrame(count);

  let ticks = 0;
  const timer = setInterval(() => {
    const canvas = document.getElementById('scene');
    const g = window.__game;
    const s = g ? g.getState() : null;

    beacon('alive', `t=${++ticks} frames=${frames} canvas=${canvas.width}x${canvas.height}`
      + ` menu=${!document.getElementById('menu').hidden}`
      + ` cards=${document.querySelectorAll('.ship-card').length}`
      + (s ? ` mode=${s.mode} score=${s.score} hull=${s.hull}/${s.maxHull}`
           + ` rocks=${s.rocks} drones=${s.drones}`
           + ` bullets=${g.bullets.length} lvl=${s.level.n}` : ''));

    frames = 0;
    if (ticks >= 12) clearInterval(timer);
  }, 1000);

  // ?debug&autoplay — launches a run and holds thrust + fire, so the play loop,
  // spawning, shooting and collisions can be exercised without a human at the keyboard.
  if (location.search.includes('autoplay')) {
    const key = (type, code) => window.dispatchEvent(
      new KeyboardEvent(type, { code, bubbles: true }));

    setTimeout(() => {
      const btn = document.getElementById('btn-launch');
      if (!btn) return beacon('error', 'launch button missing');
      btn.click();
      beacon('autoplay', 'launched');

      // ?level=N jumps straight to a sector, so the drone code can be exercised without
      // first clearing every rock in the levels before it.
      const m = location.search.match(/level=(\d)/);
      if (m && window.__game) {
        window.__game.loadLevel(Number(m[1]) - 1);
        beacon('autoplay', `forced level ${m[1]}`);
      }

      key('keydown', 'Space');
      key('keydown', 'ArrowUp');
      // Turning keeps the ship sweeping the field instead of flying one straight line.
      setInterval(() => {
        key('keydown', 'ArrowLeft');
        setTimeout(() => key('keyup', 'ArrowLeft'), 420);
      }, 1400);
    }, 800);
  }
}
