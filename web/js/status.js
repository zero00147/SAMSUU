/* Top-right activity indicator.
   Answers "is it working, and how long has it been?" — the model can take several
   seconds to process a prompt before the first token appears, and without this the
   UI looks frozen during that window. */

const Status = (() => {
  const pill = document.getElementById('status-pill');
  const label = document.getElementById('status-label');
  const elapsed = document.getElementById('status-elapsed');

  let t0 = 0;
  let ticker = null;
  let hideTimer = null;
  let phase = null;

  const secs = (ms) => (ms / 1000).toFixed(1) + 's';

  function show(state) {
    pill.hidden = false;
    pill.dataset.state = state;
  }

  function tick() {
    elapsed.textContent = secs(performance.now() - t0);
  }

  /* Called the moment the user hits send — before any network response. */
  function start() {
    clearTimeout(hideTimer);
    clearInterval(ticker);
    t0 = performance.now();
    phase = 'thinking';
    label.textContent = 'Thinking';
    elapsed.textContent = '0.0s';
    show('working');
    ticker = setInterval(tick, 100);
  }

  /* 'reasoning' = emitting <think> content; 'writing' = emitting the answer. */
  function setPhase(next) {
    if (phase === next) return;
    phase = next;
    label.textContent = next === 'reasoning' ? 'Reasoning' : 'Writing';
  }

  function settle(state, text, holdMs) {
    clearInterval(ticker);
    ticker = null;
    label.textContent = text;
    elapsed.textContent = secs(performance.now() - t0);
    show(state);
    hideTimer = setTimeout(() => { pill.hidden = true; }, holdMs);
  }

  function finish(stats) {
    const tps = stats && stats.tokens_per_sec;
    clearInterval(ticker);
    ticker = null;
    label.textContent = 'Done';
    elapsed.textContent =
      secs(performance.now() - t0) + (tps ? ` · ${tps} tok/s` : '');
    show('done');
    hideTimer = setTimeout(() => { pill.hidden = true; }, 5000);
  }

  function stopped() { settle('stopped', 'Stopped', 4000); }
  function failed()  { settle('failed', 'Failed', 6000); }

  return { start, setPhase, finish, stopped, failed };
})();
