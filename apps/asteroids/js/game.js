// The engine: scene setup, the player ship, the fixed update loop, collisions and the
// level state machine. UI lives in main.js and talks to this only through `getState()`
// and the `on*` callbacks.

import * as THREE from 'three';
import { audio } from './audio.js';
import { BASE, LEVELS, WORLD, shortestDelta, wrap } from './config.js';
import { Bullet, Debris, Drone, makeStarfield, spawnAsteroidField } from './entities.js';
import { buildShipMesh } from './ships.js';

const _a = new THREE.Vector3();
const _b = new THREE.Vector3();

// Collision is a circle test on the z = 0 plane, done through the wrap so a rock
// straddling the edge still hits you.
function collides(objA, objB) {
  shortestDelta(objA.mesh.position, objB.mesh.position, _a);
  const r = objA.radius + objB.radius;
  return _a.lengthSq() < r * r;
}

class Player {
  constructor(spec) {
    this.spec = spec;
    this.mesh = buildShipMesh(spec);
    this.radius = 1.0;
    this.angle = Math.PI / 2;         // facing "up" the screen
    this.velocity = new THREE.Vector3();
    this.hull = spec.hull;
    this.maxHull = spec.hull;
    this.cooldown = 0;
    this.invuln = BASE.invulnTime;
    this.thrusting = false;
    this.alive = true;
    this.blinkClock = 0;
  }

  reset() {
    this.mesh.position.set(0, 0, 0);
    this.mesh.visible = true;
    this.angle = Math.PI / 2;
    this.velocity.set(0, 0, 0);
    this.invuln = BASE.invulnTime;
    this.cooldown = 0;
    this.alive = true;
  }

  get forward() {
    // Models point along +Y, so heading `a` maps to (-sin a, cos a).
    return _b.set(-Math.sin(this.angle), Math.cos(this.angle), 0);
  }

  update(dt, input) {
    if (!this.alive) return;

    if (input.left) this.angle += BASE.turn * this.spec.turn * dt;
    if (input.right) this.angle -= BASE.turn * this.spec.turn * dt;

    this.thrusting = !!input.thrust;
    if (this.thrusting) {
      this.velocity.addScaledVector(this.forward, BASE.thrust * this.spec.speed * dt);
      const max = BASE.maxSpeed * this.spec.speed;
      if (this.velocity.lengthSq() > max * max) this.velocity.setLength(max);
    }

    // Frame-rate independent drag: a per-second fraction, not a per-frame one.
    this.velocity.multiplyScalar(Math.exp(-BASE.drag * dt));

    this.mesh.position.addScaledVector(this.velocity, dt);
    wrap(this.mesh.position);
    this.mesh.rotation.z = this.angle;
    // A little roll into the turn. Cosmetic, but it stops the ship reading as a sprite.
    const targetRoll = (input.left ? 0.4 : 0) + (input.right ? -0.4 : 0);
    this.mesh.rotation.y += (targetRoll - this.mesh.rotation.y) * Math.min(1, 8 * dt);

    for (const t of this.mesh.userData.thrusters) {
      const target = this.thrusting ? 2.4 : 0.35;
      const m = t.material;
      m.emissiveIntensity += (target - m.emissiveIntensity) * Math.min(1, 12 * dt);
      t.scale.y = this.thrusting ? 1 + Math.random() * 0.6 : 1;
    }

    this.cooldown -= dt;

    if (this.invuln > 0) {
      this.invuln -= dt;
      this.blinkClock += dt;
      this.mesh.visible = Math.sin(this.blinkClock * 28) > -0.35;
      if (this.invuln <= 0) this.mesh.visible = true;
    }
  }

  // Each weapon returns the bullets for one trigger pull.
  fire() {
    if (this.cooldown > 0 || !this.alive) return [];
    this.cooldown = BASE.fireInterval / this.spec.fireRate;

    const fwd = this.forward.clone();
    const vel = fwd.clone().multiplyScalar(BASE.bulletSpeed).add(this.velocity);
    const nose = this.mesh.position.clone().addScaledVector(fwd, 1.9);
    const color = this.spec.color;
    const out = [];

    if (this.spec.weapon === 'twin') {
      const side = new THREE.Vector3(-fwd.y, fwd.x, 0).multiplyScalar(0.78);
      out.push(new Bullet(nose.clone().add(side), vel, BASE.bulletLife, color));
      out.push(new Bullet(nose.clone().sub(side), vel, BASE.bulletLife, color));
    } else if (this.spec.weapon === 'spread') {
      for (const off of [-0.26, 0, 0.26]) {
        const a = this.angle + off;
        const dir = new THREE.Vector3(-Math.sin(a), Math.cos(a), 0);
        out.push(new Bullet(
          nose, dir.multiplyScalar(BASE.bulletSpeed).add(this.velocity),
          BASE.bulletLife, color,
        ));
      }
    } else {
      out.push(new Bullet(nose, vel, BASE.bulletLife, color));
    }

    audio.laser(this.spec.weapon === 'spread' ? 0.8 : 1);
    return out;
  }
}

export class Game {
  constructor(canvas) {
    this.canvas = canvas;
    this.mode = 'menu';           // menu | playing | levelclear | gameover | won
    this.score = 0;
    this.levelIndex = 0;
    this.paused = false;

    this.asteroids = [];
    this.bullets = [];
    this.drones = [];
    this.player = null;
    this.previewMesh = null;
    this.messageTimer = 0;

    this.input = { left: false, right: false, thrust: false, fire: false };

    this.onMessage = () => {};
    this.onGameOver = () => {};
    this.onLevelStart = () => {};

    this._initScene();
    this._bindInput();

    this.clock = new THREE.Clock();
    this._tick = this._tick.bind(this);
    requestAnimationFrame(this._tick);
  }

  // --- setup -------------------------------------------------------------

  _initScene() {
    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas, antialias: true, powerPreference: 'high-performance',
    });
    // Capped: an uncapped ratio on a Retina panel quadruples the fill cost for very
    // little visible gain, and this machine has 8 GB of shared memory.
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x05070d);
    this.scene.fog = new THREE.Fog(0x05070d, 130, 340);

    this.camera = new THREE.PerspectiveCamera(56, 1, 0.1, 700);

    this.scene.add(new THREE.AmbientLight(0x5a6a80, 1.1));

    this.keyLight = new THREE.DirectionalLight(0xffffff, 2.0);
    this.keyLight.position.set(8, 14, 22);
    this.scene.add(this.keyLight);

    // Intensity is in candela since three r155 (illuminance falls off as 1/d²), and the
    // playfield sits ~46 units away — hence the large-looking number.
    this.rimLight = new THREE.PointLight(0x2b4a7a, 3800, 320);
    this.rimLight.position.set(0, 0, 46);
    this.scene.add(this.rimLight);

    this.stars = makeStarfield(1500);
    this.scene.add(this.stars);

    this.debris = new Debris(900);
    this.scene.add(this.debris.mesh);

    this._resize();
    window.addEventListener('resize', () => this._resize());
  }

  // Pull the camera back far enough that the whole playfield fits whatever the window
  // aspect happens to be — otherwise rocks wrap in from off-screen on a narrow window.
  _resize() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;

    const half = Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2);
    const needV = (WORLD.h / 2 + 5) / half;
    const needH = (WORLD.w / 2 + 5) / (half * this.camera.aspect);
    const dist = Math.max(needV, needH);

    this.camera.position.set(0, -dist * 0.16, dist);
    this.camera.lookAt(0, 0, 0);
    this.camera.updateProjectionMatrix();
  }

  _bindInput() {
    const set = (code, down) => {
      switch (code) {
        case 'ArrowLeft': case 'KeyA': this.input.left = down; return true;
        case 'ArrowRight': case 'KeyD': this.input.right = down; return true;
        case 'ArrowUp': case 'KeyW': this.input.thrust = down; return true;
        case 'Space': this.input.fire = down; return true;
        default: return false;
      }
    };

    window.addEventListener('keydown', (e) => {
      if (e.repeat) return;
      if (set(e.code, true)) e.preventDefault();
      if (e.code === 'KeyP' || e.code === 'Escape') this.togglePause();
    });

    window.addEventListener('keyup', (e) => {
      if (set(e.code, false)) e.preventDefault();
    });

    // Losing focus mid-thrust would otherwise leave the key stuck down.
    window.addEventListener('blur', () => {
      this.input.left = this.input.right = this.input.thrust = this.input.fire = false;
      audio.setThrust(false);
      if (this.mode === 'playing') this.setPaused(true);
    });
  }

  // --- menu preview ------------------------------------------------------

  showPreview(spec) {
    this.clearPreview();
    this.previewMesh = buildShipMesh(spec);
    this.previewMesh.scale.setScalar(3.4);
    this.previewMesh.position.set(0, 0, 26);
    for (const t of this.previewMesh.userData.thrusters) {
      t.material.emissiveIntensity = 1.6;
    }
    this.scene.add(this.previewMesh);
    this.rimLight.color.setHex(spec.color);
  }

  clearPreview() {
    if (!this.previewMesh) return;
    this.scene.remove(this.previewMesh);
    disposeTree(this.previewMesh);
    this.previewMesh = null;
  }

  // --- lifecycle ---------------------------------------------------------

  start(spec) {
    this.clearPreview();
    this.clearField();
    // Retry goes straight from the game-over overlay back through here, so the previous
    // ship has to be torn down or every restart leaves a dead hull in the scene.
    if (this.player) {
      this.scene.remove(this.player.mesh);
      disposeTree(this.player.mesh);
      this.player = null;
    }
    this.score = 0;
    this.levelIndex = 0;
    this.player = new Player(spec);
    this.scene.add(this.player.mesh);
    this.loadLevel(0);
    this.mode = 'playing';
    this.paused = false;
  }

  loadLevel(index) {
    this.levelIndex = index;
    const level = LEVELS[index];

    this.clearField();
    this.player.reset();

    this.asteroids = spawnAsteroidField(level, level.hue, this.player.mesh.position);
    for (const a of this.asteroids) this.scene.add(a.mesh);

    for (let i = 0; i < level.drones; i++) {
      // Drones enter from the corners, never on top of the player.
      const p = new THREE.Vector3(
        (i % 2 ? 1 : -1) * (WORLD.w / 2 - 8),
        (i < 2 ? 1 : -1) * (WORLD.h / 2 - 8),
        0,
      );
      const d = new Drone(p, level);
      this.drones.push(d);
      this.scene.add(d.mesh);
    }

    this.scene.background.setHex(level.fog);
    this.scene.fog.color.setHex(level.fog);
    this.rimLight.color.setHex(level.hue);
    this.onLevelStart(level);
  }

  clearField() {
    for (const list of [this.asteroids, this.bullets, this.drones]) {
      for (const o of list) {
        this.scene.remove(o.mesh);
        disposeTree(o.mesh);
      }
      list.length = 0;
    }
    this.debris.clear();
  }

  toMenu() {
    this.clearField();
    if (this.player) {
      this.scene.remove(this.player.mesh);
      disposeTree(this.player.mesh);
      this.player = null;
    }
    this.scene.background.setHex(0x05070d);
    this.scene.fog.color.setHex(0x05070d);
    this.mode = 'menu';
    this.paused = false;
    audio.setThrust(false);
  }

  togglePause() {
    if (this.mode !== 'playing') return;
    this.setPaused(!this.paused);
  }

  setPaused(p) {
    if (this.mode !== 'playing') return;
    this.paused = p;
    if (p) audio.setThrust(false);
    this.onMessage(p ? { kind: 'paused' } : null);
  }

  getState() {
    return {
      mode: this.mode,
      score: this.score,
      level: LEVELS[this.levelIndex],
      hull: this.player ? this.player.hull : 0,
      maxHull: this.player ? this.player.maxHull : 0,
      rocks: this.asteroids.length,
      drones: this.drones.length,
      paused: this.paused,
    };
  }

  // --- loop --------------------------------------------------------------

  _tick() {
    requestAnimationFrame(this._tick);
    // Clamp: after a tab switch the delta can be seconds long, which would tunnel
    // everything straight through every collision test.
    const dt = Math.min(this.clock.getDelta(), 0.05);

    this.stars.rotation.z += dt * 0.006;

    if (this.mode === 'menu' && this.previewMesh) {
      this.previewMesh.rotation.y += dt * 0.9;
      this.previewMesh.rotation.x = Math.sin(performance.now() * 0.0006) * 0.25;
    }

    if (this.mode === 'playing' && !this.paused) this._update(dt);
    if (this.mode === 'levelclear' || this.mode === 'gameover' || this.mode === 'won') {
      this._updateCosmetic(dt);
    }

    this.debris.update(dt);
    this.renderer.render(this.scene, this.camera);
  }

  // Keeps rocks drifting during the between-level and game-over overlays.
  _updateCosmetic(dt) {
    for (const a of this.asteroids) a.update(dt);
    if (this.messageTimer > 0) {
      this.messageTimer -= dt;
      if (this.messageTimer <= 0) this._advance();
    }
  }

  _update(dt) {
    const p = this.player;

    p.update(dt, this.input);
    audio.setThrust(p.thrusting && p.alive);

    if (this.input.fire) {
      for (const b of p.fire()) {
        this.bullets.push(b);
        this.scene.add(b.mesh);
      }
    }

    for (const a of this.asteroids) a.update(dt);
    for (const b of this.bullets) b.update(dt);

    for (const d of this.drones) {
      const shot = d.update(dt, p.alive ? p.mesh.position : null);
      if (shot) {
        this.bullets.push(shot);
        this.scene.add(shot.mesh);
        audio.droneShot();
      }
    }

    this._collide();
    this._cull();

    if (this.asteroids.length === 0 && this.drones.length === 0) this._clearLevel();
  }

  _collide() {
    const p = this.player;

    for (const b of this.bullets) {
      if (!b.alive) continue;

      if (b.hostile) {
        if (p.alive && p.invuln <= 0 && collides(b, p)) {
          b.alive = false;
          this._damagePlayer();
        }
        continue;
      }

      for (const a of this.asteroids) {
        if (!a.alive || !collides(b, a)) continue;
        b.alive = false;
        a.alive = false;
        this.score += a.score;
        this.debris.burst(a.mesh.position, 14 + a.size * 8, a.mesh.material.color, 10 + a.size * 5);
        audio.explosion(a.size);
        for (const frag of a.split(LEVELS[this.levelIndex].hue)) {
          this.asteroids.push(frag);
          this.scene.add(frag.mesh);
        }
        break;
      }
      if (!b.alive) continue;

      for (const d of this.drones) {
        if (!d.alive || !collides(b, d)) continue;
        b.alive = false;
        d.hull -= 1;
        if (d.hull <= 0) {
          d.alive = false;
          this.score += d.score;
          this.debris.burst(d.mesh.position, 34, 0xff5a3a, 22);
          audio.explosion(3);
        } else {
          this.debris.burst(d.mesh.position, 8, 0xffaa66, 12);
          audio.hit();
        }
        break;
      }
    }

    if (!p.alive || p.invuln > 0) return;

    for (const a of this.asteroids) {
      if (!collides(p, a)) continue;
      a.alive = false;
      this.debris.burst(a.mesh.position, 14 + a.size * 8, a.mesh.material.color, 14);
      audio.explosion(a.size);
      for (const frag of a.split(LEVELS[this.levelIndex].hue)) {
        this.asteroids.push(frag);
        this.scene.add(frag.mesh);
      }
      this._damagePlayer();
      return;
    }

    for (const d of this.drones) {
      if (!collides(p, d)) continue;
      d.alive = false;
      this.score += d.score;
      this.debris.burst(d.mesh.position, 30, 0xff5a3a, 20);
      audio.explosion(3);
      this._damagePlayer();
      return;
    }
  }

  _damagePlayer() {
    const p = this.player;
    p.hull -= 1;
    this.debris.burst(p.mesh.position, 26, p.spec.color, 18);

    if (p.hull <= 0) {
      p.alive = false;
      p.mesh.visible = false;
      audio.explosion(3);
      audio.setThrust(false);
      setTimeout(() => audio.gameOver(), 250);
      this.mode = 'gameover';
      this.onGameOver({ score: this.score, level: LEVELS[this.levelIndex] });
      return;
    }

    audio.hit();
    p.invuln = BASE.invulnTime;
    p.velocity.multiplyScalar(0.3);
  }

  _cull() {
    const drop = (list) => {
      for (let i = list.length - 1; i >= 0; i--) {
        if (list[i].alive) continue;
        this.scene.remove(list[i].mesh);
        disposeTree(list[i].mesh);
        list.splice(i, 1);
      }
    };
    drop(this.bullets);
    drop(this.asteroids);
    drop(this.drones);
  }

  _clearLevel() {
    audio.setThrust(false);
    const last = this.levelIndex >= LEVELS.length - 1;
    this.mode = last ? 'won' : 'levelclear';
    this.messageTimer = last ? 0.6 : 2.6;
    // Surviving a level is worth more the deeper you are.
    this.score += 500 * (this.levelIndex + 1);

    if (last) {
      audio.victory();
      this.onMessage({ kind: 'won', score: this.score });
    } else {
      audio.levelUp();
      this.onMessage({ kind: 'levelclear', next: LEVELS[this.levelIndex + 1], score: this.score });
    }
  }

  _advance() {
    if (this.mode !== 'levelclear') return;
    this.onMessage(null);
    // One hull point back between levels. Without it, a hit taken on level 1 follows
    // you to the hardest level and the Wasp becomes unplayable.
    this.player.hull = Math.min(this.player.maxHull, this.player.hull + 1);
    this.loadLevel(this.levelIndex + 1);
    this.mode = 'playing';
  }
}

// Three.js does not free GPU buffers on scene.remove — without this the debris of a
// dozen destroyed rocks per level leaks for the whole session.
function disposeTree(root) {
  root.traverse((o) => {
    if (o.geometry && !o.geometry.userData.shared) o.geometry.dispose();
    if (o.material) {
      if (Array.isArray(o.material)) o.material.forEach((m) => m.dispose());
      else o.material.dispose();
    }
  });
}
