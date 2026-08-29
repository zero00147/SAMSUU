// Every sound is synthesised at runtime with the Web Audio API.
//
// No .wav or .mp3 files: the game ships as text and works with no network, which is the
// same constraint the rest of samsu runs under. It also means the whole soundscape is
// tunable by editing numbers here.
//
// Browsers refuse to start an AudioContext until the user has interacted with the page,
// so `unlock()` must be called from a real click or keypress handler.

const NOISE_SECONDS = 1.6;

class Audio {
  constructor() {
    this.ctx = null;
    this.master = null;
    this.noiseBuffer = null;
    this.muted = false;
    this.thrustNode = null;
  }

  unlock() {
    if (this.ctx) {
      // Chrome suspends the context when the tab is backgrounded.
      if (this.ctx.state === 'suspended') this.ctx.resume();
      return;
    }
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return;

    this.ctx = new Ctor();
    this.master = this.ctx.createGain();
    this.master.gain.value = this.muted ? 0 : 0.5;
    this.master.connect(this.ctx.destination);

    // One reusable noise buffer — regenerating it per explosion is pure waste.
    const len = Math.floor(this.ctx.sampleRate * NOISE_SECONDS);
    this.noiseBuffer = this.ctx.createBuffer(1, len, this.ctx.sampleRate);
    const data = this.noiseBuffer.getChannelData(0);
    for (let i = 0; i < len; i++) data[i] = Math.random() * 2 - 1;
  }

  setMuted(muted) {
    this.muted = muted;
    if (this.master) {
      this.master.gain.setTargetAtTime(muted ? 0 : 0.5, this.ctx.currentTime, 0.02);
    }
  }

  get ready() {
    return this.ctx !== null && this.ctx.state === 'running';
  }

  // --- building blocks ---------------------------------------------------

  tone({ freq, endFreq, type = 'sine', gain = 0.3, attack = 0.005, duration = 0.2, delay = 0 }) {
    if (!this.ready) return;
    const t0 = this.ctx.currentTime + delay;
    const osc = this.ctx.createOscillator();
    const g = this.ctx.createGain();

    osc.type = type;
    osc.frequency.setValueAtTime(freq, t0);
    if (endFreq !== undefined) {
      // exponentialRamp cannot reach or cross zero.
      osc.frequency.exponentialRampToValueAtTime(Math.max(1, endFreq), t0 + duration);
    }

    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(gain, t0 + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + duration);

    osc.connect(g).connect(this.master);
    osc.start(t0);
    osc.stop(t0 + duration + 0.02);
  }

  noise({ gain = 0.4, duration = 0.4, cutoff = 1400, endCutoff = 120, q = 1, delay = 0 }) {
    if (!this.ready) return;
    const t0 = this.ctx.currentTime + delay;
    const src = this.ctx.createBufferSource();
    src.buffer = this.noiseBuffer;

    const filter = this.ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.Q.value = q;
    filter.frequency.setValueAtTime(cutoff, t0);
    filter.frequency.exponentialRampToValueAtTime(Math.max(20, endCutoff), t0 + duration);

    const g = this.ctx.createGain();
    g.gain.setValueAtTime(gain, t0);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + duration);

    src.connect(filter).connect(g).connect(this.master);
    src.start(t0);
    src.stop(t0 + duration + 0.02);
  }

  // --- the actual effects ------------------------------------------------

  laser(pitch = 1) {
    this.tone({
      freq: 900 * pitch, endFreq: 180 * pitch, type: 'square',
      gain: 0.16, duration: 0.14,
    });
    this.tone({
      freq: 1800 * pitch, endFreq: 400 * pitch, type: 'sawtooth',
      gain: 0.05, duration: 0.09,
    });
  }

  // size 3 = large rock, 1 = fragment. Bigger rocks get longer, darker booms.
  explosion(size = 2) {
    const d = 0.24 + size * 0.16;
    this.noise({ gain: 0.32 + size * 0.06, duration: d, cutoff: 700 + size * 500, endCutoff: 60 });
    this.tone({ freq: 120 - size * 18, endFreq: 34, type: 'sine', gain: 0.28, duration: d * 0.9 });
  }

  hit() {
    this.noise({ gain: 0.3, duration: 0.18, cutoff: 2600, endCutoff: 300, q: 3 });
    this.tone({ freq: 320, endFreq: 90, type: 'square', gain: 0.2, duration: 0.16 });
  }

  droneShot() {
    this.tone({ freq: 420, endFreq: 140, type: 'triangle', gain: 0.1, duration: 0.16 });
  }

  levelUp() {
    [523.25, 659.25, 783.99, 1046.5].forEach((f, i) => {
      this.tone({ freq: f, type: 'triangle', gain: 0.18, duration: 0.34, delay: i * 0.1 });
    });
  }

  gameOver() {
    [392, 311, 261.6, 196].forEach((f, i) => {
      this.tone({ freq: f, endFreq: f * 0.94, type: 'sawtooth', gain: 0.16, duration: 0.5, delay: i * 0.17 });
    });
    this.noise({ gain: 0.2, duration: 1.2, cutoff: 500, endCutoff: 40, delay: 0.5 });
  }

  victory() {
    [523.25, 659.25, 783.99, 1046.5, 1318.5].forEach((f, i) => {
      this.tone({ freq: f, type: 'triangle', gain: 0.2, duration: 0.5, delay: i * 0.13 });
    });
  }

  select() {
    this.tone({ freq: 660, type: 'triangle', gain: 0.12, duration: 0.08 });
  }

  // --- continuous thrust rumble ------------------------------------------

  // A looping filtered-noise bed whose gain follows the throttle, rather than a sound
  // retriggered every frame.
  setThrust(on) {
    if (!this.ready) return;
    if (on && !this.thrustNode) {
      const src = this.ctx.createBufferSource();
      src.buffer = this.noiseBuffer;
      src.loop = true;

      const filter = this.ctx.createBiquadFilter();
      filter.type = 'lowpass';
      filter.frequency.value = 320;
      filter.Q.value = 6;

      const g = this.ctx.createGain();
      g.gain.setValueAtTime(0.0001, this.ctx.currentTime);
      g.gain.setTargetAtTime(0.13, this.ctx.currentTime, 0.06);

      src.connect(filter).connect(g).connect(this.master);
      src.start();
      this.thrustNode = { src, gain: g };
    } else if (!on && this.thrustNode) {
      const { src, gain } = this.thrustNode;
      this.thrustNode = null;
      gain.gain.setTargetAtTime(0.0001, this.ctx.currentTime, 0.05);
      // Let the fade finish before tearing the node down.
      setTimeout(() => { try { src.stop(); } catch (e) { /* already stopped */ } }, 300);
    }
  }

  stopAll() {
    this.setThrust(false);
  }
}

export const audio = new Audio();
