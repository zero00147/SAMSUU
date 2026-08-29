// Everything that moves except the player: rocks, bullets, drones, debris, stars.
//
// All of them expose the same shape — `mesh`, `radius`, `alive`, `update(dt)` — so the
// engine's collision and cleanup passes stay uniform.

import * as THREE from 'three';
import { ASTEROID, DRONE, WORLD, shortestDelta, wrap } from './config.js';

const _d = new THREE.Vector3();

function rand(a, b) {
  return a + Math.random() * (b - a);
}

// --- asteroids -----------------------------------------------------------

// IcosahedronGeometry is non-indexed, so the same corner appears several times in the
// vertex list. Displacing by Math.random() would push those copies apart and split the
// mesh open. This is a function of position instead, so every copy of a corner moves
// identically and the rock stays watertight.
function lumpify(geometry, seed) {
  const pos = geometry.attributes.position;
  const v = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);
    const n =
      Math.sin(v.x * 1.7 + seed) *
      Math.cos(v.y * 2.3 + seed * 1.7) *
      Math.sin(v.z * 1.9 + seed * 2.9);
    v.multiplyScalar(1 + n * 0.28);
    pos.setXYZ(i, v.x, v.y, v.z);
  }
  pos.needsUpdate = true;
  geometry.computeVertexNormals();
  return geometry;
}

export class Asteroid {
  constructor(size, position, velocity, tint) {
    const spec = ASTEROID[size];
    this.size = size;
    this.radius = spec.radius;
    this.score = spec.score;
    this.alive = true;

    const geo = lumpify(
      new THREE.IcosahedronGeometry(spec.radius, 1),
      Math.random() * 100,
    );
    this.mesh = new THREE.Mesh(
      geo,
      new THREE.MeshStandardMaterial({
        color: tint, roughness: 0.95, metalness: 0.05, flatShading: true,
      }),
    );
    this.mesh.position.copy(position);
    this.mesh.rotation.set(rand(0, 6.28), rand(0, 6.28), rand(0, 6.28));

    this.velocity = velocity.clone();
    this.spin = new THREE.Vector3(rand(-0.9, 0.9), rand(-0.9, 0.9), rand(-0.6, 0.6));
  }

  update(dt) {
    this.mesh.position.addScaledVector(this.velocity, dt);
    wrap(this.mesh.position);
    this.mesh.rotation.x += this.spin.x * dt;
    this.mesh.rotation.y += this.spin.y * dt;
    this.mesh.rotation.z += this.spin.z * dt;
  }

  // A destroyed rock becomes two smaller ones travelling outward from the impact.
  split(tint) {
    const spec = ASTEROID[this.size];
    if (!spec.splits) return [];
    const out = [];
    for (let i = 0; i < spec.splits; i++) {
      const angle = rand(0, Math.PI * 2);
      const speed = this.velocity.length() * rand(1.15, 1.6) + 3;
      out.push(new Asteroid(
        this.size - 1,
        this.mesh.position,
        new THREE.Vector3(Math.cos(angle) * speed, Math.sin(angle) * speed, 0),
        tint,
      ));
    }
    return out;
  }
}

export function spawnAsteroidField(level, tint, avoid) {
  const rocks = [];
  for (let i = 0; i < level.asteroids; i++) {
    // Keep the opening volley away from the player's spawn point.
    let p;
    do {
      p = new THREE.Vector3(
        rand(-WORLD.w / 2, WORLD.w / 2),
        rand(-WORLD.h / 2, WORLD.h / 2),
        0,
      );
    } while (avoid && shortestDelta(p, avoid, _d).length() < 22);

    const angle = rand(0, Math.PI * 2);
    const speed = rand(level.rockSpeed[0], level.rockSpeed[1]);
    rocks.push(new Asteroid(
      3, p,
      new THREE.Vector3(Math.cos(angle) * speed, Math.sin(angle) * speed, 0),
      tint,
    ));
  }
  return rocks;
}

// --- bullets -------------------------------------------------------------

// One geometry for every bullet ever fired. Tagged so the engine's dispose pass skips
// it — freeing it with the first spent bullet would break every shot after that.
const BULLET_GEO = new THREE.SphereGeometry(0.26, 8, 6);
BULLET_GEO.userData.shared = true;

export class Bullet {
  constructor(position, velocity, life, color, hostile = false) {
    this.radius = 0.4;
    this.alive = true;
    this.life = life;
    this.hostile = hostile;

    this.mesh = new THREE.Mesh(
      BULLET_GEO,
      new THREE.MeshBasicMaterial({ color }),
    );
    this.mesh.position.copy(position);
    // Stretched along travel so fast shots read as tracers rather than dots.
    this.mesh.scale.set(1, 2.6, 1);
    this.mesh.rotation.z = Math.atan2(velocity.y, velocity.x) - Math.PI / 2;

    this.velocity = velocity.clone();
  }

  update(dt) {
    this.mesh.position.addScaledVector(this.velocity, dt);
    wrap(this.mesh.position);
    this.life -= dt;
    if (this.life <= 0) this.alive = false;
  }
}

// --- drones --------------------------------------------------------------

export class Drone {
  constructor(position, level) {
    this.radius = DRONE.radius;
    this.hull = DRONE.hull;
    this.score = DRONE.score;
    this.alive = true;
    this.cooldown = rand(0.8, level.droneInterval);
    this.speed = level.droneSpeed;
    this.interval = level.droneInterval;

    const g = new THREE.Group();
    const body = new THREE.Mesh(
      new THREE.OctahedronGeometry(0.95, 0),
      new THREE.MeshStandardMaterial({
        color: 0x2a2f3a, metalness: 0.8, roughness: 0.3, flatShading: true,
      }),
    );
    g.add(body);

    const eye = new THREE.Mesh(
      new THREE.SphereGeometry(0.34, 10, 8),
      new THREE.MeshBasicMaterial({ color: 0xff3a3a }),
    );
    eye.position.z = 0.75;
    g.add(eye);

    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(1.35, 0.11, 6, 20),
      new THREE.MeshStandardMaterial({
        color: 0xff5a4a, emissive: 0xff3a2a, emissiveIntensity: 0.7,
        metalness: 0.4, roughness: 0.5,
      }),
    );
    g.add(ring);
    this.ring = ring;
    this.eye = eye;

    g.position.copy(position);
    this.mesh = g;
    this.velocity = new THREE.Vector3();
  }

  // Returns a Bullet when it decides to fire, otherwise null.
  update(dt, target) {
    this.ring.rotation.z += dt * 2.2;
    this.ring.rotation.x = Math.sin(performance.now() * 0.001) * 0.5;

    if (!target) {
      this.mesh.position.addScaledVector(this.velocity, dt);
      wrap(this.mesh.position);
      return null;
    }

    shortestDelta(this.mesh.position, target, _d);
    const dist = _d.length();
    _d.normalize();

    // Close in from far away, back off when too close: it orbits at preferredRange
    // instead of ramming the player, which reads as deliberate rather than suicidal.
    const closing = dist > DRONE.preferredRange ? 1 : -0.75;
    const strafe = new THREE.Vector3(-_d.y, _d.x, 0).multiplyScalar(0.55);

    const desired = _d.clone().multiplyScalar(closing).add(strafe)
      .normalize().multiplyScalar(this.speed);
    this.velocity.lerp(desired, 1 - Math.exp(-2.5 * dt));

    this.mesh.position.addScaledVector(this.velocity, dt);
    wrap(this.mesh.position);

    this.eye.position.set(_d.x * 0.75, _d.y * 0.75, 0.4);

    this.cooldown -= dt;
    if (this.cooldown <= 0 && dist < 46) {
      this.cooldown = this.interval;
      const vel = _d.clone().multiplyScalar(DRONE.bulletSpeed);
      const muzzle = this.mesh.position.clone().addScaledVector(_d, 1.7);
      return new Bullet(muzzle, vel, DRONE.bulletLife, 0xff4a3a, true);
    }
    return null;
  }
}

// --- particle debris -----------------------------------------------------

// One pooled Points cloud for every explosion in the game. Allocating a geometry per
// burst would churn GPU buffers constantly; this reuses a fixed set of slots.
export class Debris {
  constructor(capacity = 900) {
    this.capacity = capacity;
    this.positions = new Float32Array(capacity * 3);
    this.colors = new Float32Array(capacity * 3);
    this.velocities = new Float32Array(capacity * 3);
    this.life = new Float32Array(capacity);
    this.maxLife = new Float32Array(capacity);
    this.cursor = 0;

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(this.positions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(this.colors, 3));

    this.mesh = new THREE.Points(geo, new THREE.PointsMaterial({
      size: 0.7,
      vertexColors: true,
      transparent: true,
      opacity: 0.95,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      sizeAttenuation: true,
    }));
    this.mesh.frustumCulled = false;

    // Dead particles are parked far off-screen rather than hidden, which would need a
    // second attribute and a custom shader.
    for (let i = 0; i < capacity; i++) this.positions[i * 3 + 2] = 9999;
  }

  burst(position, count, color, speed = 18) {
    const c = new THREE.Color(color);
    for (let i = 0; i < count; i++) {
      const idx = this.cursor;
      this.cursor = (this.cursor + 1) % this.capacity;

      const a = Math.random() * Math.PI * 2;
      const z = Math.random() * 2 - 1;
      const r = Math.sqrt(Math.max(0, 1 - z * z));
      const s = speed * (0.35 + Math.random() * 0.9);

      this.positions[idx * 3] = position.x;
      this.positions[idx * 3 + 1] = position.y;
      this.positions[idx * 3 + 2] = position.z;

      this.velocities[idx * 3] = Math.cos(a) * r * s;
      this.velocities[idx * 3 + 1] = Math.sin(a) * r * s;
      this.velocities[idx * 3 + 2] = z * s * 0.5;

      const shade = 0.65 + Math.random() * 0.35;
      this.colors[idx * 3] = c.r * shade;
      this.colors[idx * 3 + 1] = c.g * shade;
      this.colors[idx * 3 + 2] = c.b * shade;

      this.maxLife[idx] = 0.5 + Math.random() * 0.7;
      this.life[idx] = this.maxLife[idx];
    }
  }

  update(dt) {
    for (let i = 0; i < this.capacity; i++) {
      if (this.life[i] <= 0) continue;
      this.life[i] -= dt;

      if (this.life[i] <= 0) {
        this.positions[i * 3 + 2] = 9999;
        continue;
      }

      const decay = Math.exp(-2.2 * dt);
      this.velocities[i * 3] *= decay;
      this.velocities[i * 3 + 1] *= decay;
      this.velocities[i * 3 + 2] *= decay;

      this.positions[i * 3] += this.velocities[i * 3] * dt;
      this.positions[i * 3 + 1] += this.velocities[i * 3 + 1] * dt;
      this.positions[i * 3 + 2] += this.velocities[i * 3 + 2] * dt;

      // Fade by dimming the colour, since PointsMaterial has no per-point alpha.
      const f = this.life[i] / this.maxLife[i];
      const k = Math.min(1, f * 1.4);
      this.colors[i * 3] *= 0.985 + 0.015 * k;
      this.colors[i * 3 + 1] *= 0.985 + 0.015 * k;
      this.colors[i * 3 + 2] *= 0.985 + 0.015 * k;
    }
    this.mesh.geometry.attributes.position.needsUpdate = true;
    this.mesh.geometry.attributes.color.needsUpdate = true;
  }

  clear() {
    for (let i = 0; i < this.capacity; i++) {
      this.life[i] = 0;
      this.positions[i * 3 + 2] = 9999;
    }
    this.mesh.geometry.attributes.position.needsUpdate = true;
  }
}

// --- starfield -----------------------------------------------------------

export function makeStarfield(count = 1500) {
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  const c = new THREE.Color();

  for (let i = 0; i < count; i++) {
    positions[i * 3] = rand(-WORLD.w * 1.6, WORLD.w * 1.6);
    positions[i * 3 + 1] = rand(-WORLD.h * 1.8, WORLD.h * 1.8);
    // Well behind the play plane so stars never occlude gameplay.
    positions[i * 3 + 2] = rand(-260, -40);

    c.setHSL(rand(0.52, 0.72), rand(0.1, 0.6), rand(0.5, 0.95));
    colors[i * 3] = c.r;
    colors[i * 3 + 1] = c.g;
    colors[i * 3 + 2] = c.b;
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));

  return new THREE.Points(geo, new THREE.PointsMaterial({
    size: 1.1, vertexColors: true, sizeAttenuation: true,
    transparent: true, opacity: 0.85, depthWrite: false,
  }));
}
