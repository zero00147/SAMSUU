// The five playable ships.
//
// Every model is built from Three.js primitives — no .glb, no textures — so the game
// stays offline and asset-free, exactly like samsu's vendored JS.
//
// Convention: each model points along +Y in local space. The engine sets `rotation.z`
// to the heading angle, so forward is always (-sin a, cos a). Get this wrong and the
// ship flies sideways.

import * as THREE from 'three';

function hull(color) {
  return new THREE.MeshStandardMaterial({
    color, metalness: 0.65, roughness: 0.35, flatShading: true,
  });
}

function trim(color) {
  return new THREE.MeshStandardMaterial({
    color, emissive: color, emissiveIntensity: 0.9, metalness: 0.2, roughness: 0.5,
  });
}

// Engine bells glow and are pulsed by the engine while thrusting, so they are tagged
// rather than looked up by name.
function thruster(radius, length, color) {
  const m = new THREE.Mesh(
    new THREE.CylinderGeometry(radius * 0.65, radius, length, 8),
    new THREE.MeshStandardMaterial({
      color: 0x111417, emissive: color, emissiveIntensity: 0.4,
      metalness: 0.5, roughness: 0.6,
    }),
  );
  m.userData.isThruster = true;
  return m;
}

function collectThrusters(group) {
  const out = [];
  group.traverse((o) => { if (o.userData.isThruster) out.push(o); });
  group.userData.thrusters = out;
  return group;
}

// --- model builders ------------------------------------------------------

function buildFalcon(c) {
  const g = new THREE.Group();

  const body = new THREE.Mesh(new THREE.ConeGeometry(0.62, 2.6, 6), hull(c));
  body.position.y = 0.25;
  g.add(body);

  const spine = new THREE.Mesh(new THREE.BoxGeometry(0.34, 1.5, 0.34), trim(c));
  spine.position.set(0, -0.15, 0.3);
  g.add(spine);

  for (const s of [-1, 1]) {
    const wing = new THREE.Mesh(new THREE.BoxGeometry(1.5, 0.75, 0.16), hull(0x2c3a48));
    wing.position.set(s * 0.9, -0.55, 0);
    wing.rotation.z = s * 0.42;
    g.add(wing);

    const tip = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.5, 0.2), trim(c));
    tip.position.set(s * 1.5, -0.3, 0);
    g.add(tip);
  }

  const eng = thruster(0.42, 0.7, c);
  eng.position.y = -1.25;
  g.add(eng);

  return collectThrusters(g);
}

function buildWasp(c) {
  const g = new THREE.Group();

  // Long and thin — reads as fast before you have flown it.
  const body = new THREE.Mesh(new THREE.ConeGeometry(0.4, 3.4, 5), hull(c));
  body.position.y = 0.4;
  g.add(body);

  const stripe = new THREE.Mesh(new THREE.BoxGeometry(0.14, 2.2, 0.14), trim(0xfff1c2));
  stripe.position.set(0, 0.3, 0.28);
  g.add(stripe);

  for (const s of [-1, 1]) {
    const wing = new THREE.Mesh(new THREE.BoxGeometry(0.9, 1.5, 0.1), hull(0x4a3d20));
    wing.position.set(s * 0.55, -0.85, 0);
    wing.rotation.z = s * 0.95;
    g.add(wing);
  }

  const eng = thruster(0.3, 0.85, c);
  eng.position.y = -1.5;
  g.add(eng);

  return collectThrusters(g);
}

function buildBulwark(c) {
  const g = new THREE.Group();

  // Blocky and wide. Slow, but it soaks up hits.
  const body = new THREE.Mesh(new THREE.BoxGeometry(1.5, 2.3, 0.85), hull(c));
  g.add(body);

  const nose = new THREE.Mesh(new THREE.ConeGeometry(0.75, 1.1, 4), hull(0xc8d2dc));
  nose.position.y = 1.6;
  nose.rotation.y = Math.PI / 4;
  g.add(nose);

  for (const s of [-1, 1]) {
    const plate = new THREE.Mesh(new THREE.BoxGeometry(0.55, 2.0, 1.0), hull(0x4a5560));
    plate.position.set(s * 1.0, -0.1, 0);
    g.add(plate);

    const light = new THREE.Mesh(new THREE.BoxGeometry(0.18, 0.9, 0.18), trim(0x7fe3ff));
    light.position.set(s * 1.0, 0.3, 0.55);
    g.add(light);

    const eng = thruster(0.34, 0.65, 0x7fe3ff);
    eng.position.set(s * 0.55, -1.5, 0);
    g.add(eng);
  }

  return collectThrusters(g);
}

function buildLancer(c) {
  const g = new THREE.Group();

  const body = new THREE.Mesh(new THREE.ConeGeometry(0.55, 2.8, 3), hull(c));
  body.position.y = 0.3;
  g.add(body);

  // The twin barrels are where its two bullets visually come from.
  for (const s of [-1, 1]) {
    const barrel = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.16, 2.4, 6), hull(0x59636d));
    barrel.position.set(s * 0.78, 0.55, 0);
    g.add(barrel);

    const muzzle = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.22, 0.3, 6), trim(c));
    muzzle.position.set(s * 0.78, 1.7, 0);
    g.add(muzzle);

    const strut = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.22, 0.22), hull(0x59636d));
    strut.position.set(s * 0.42, -0.2, 0);
    g.add(strut);
  }

  const eng = thruster(0.45, 0.7, c);
  eng.position.y = -1.3;
  g.add(eng);

  return collectThrusters(g);
}

function buildNova(c) {
  const g = new THREE.Group();

  // Radial body, three prongs — matches the three-way spread it fires.
  const body = new THREE.Mesh(new THREE.CylinderGeometry(0.95, 0.7, 0.6, 6), hull(c));
  body.rotation.x = Math.PI / 2;
  g.add(body);

  const core = new THREE.Mesh(new THREE.IcosahedronGeometry(0.42, 0), trim(0xffd9ff));
  core.position.z = 0.28;
  g.add(core);

  for (const a of [0, (2 * Math.PI) / 3, (4 * Math.PI) / 3]) {
    const prong = new THREE.Mesh(new THREE.ConeGeometry(0.24, 1.5, 4), hull(0x6e4a8c));
    // Prongs radiate in the XY plane; +Y (a = 0) is the nose.
    prong.position.set(-Math.sin(a) * 1.15, Math.cos(a) * 1.15, 0);
    prong.rotation.z = a;
    g.add(prong);
  }

  const eng = thruster(0.4, 0.5, c);
  eng.position.y = -1.1;
  g.add(eng);

  return collectThrusters(g);
}

// --- roster --------------------------------------------------------------

// `speed`, `turn` and `fireRate` are multipliers on the engine's base values, so the
// whole game can be retuned from one place without touching the ships.
export const SHIPS = [
  {
    id: 'falcon',
    name: 'Falcon',
    tagline: 'Balanced patrol interceptor',
    color: 0x5ec8ff,
    hull: 3, speed: 1.00, turn: 1.00, fireRate: 1.00,
    weapon: 'single',
    build: buildFalcon,
    notes: 'No weakness, no speciality. The one to learn the game on.',
  },
  {
    id: 'wasp',
    name: 'Wasp',
    tagline: 'Fast, fragile, relentless',
    color: 0xffd45e,
    hull: 2, speed: 1.45, turn: 1.35, fireRate: 1.35,
    weapon: 'single',
    build: buildWasp,
    notes: 'Fastest hull and fastest gun. Two hits and you are gone.',
  },
  {
    id: 'bulwark',
    name: 'Bulwark',
    tagline: 'Armoured siege frame',
    color: 0x9aa8b8,
    hull: 6, speed: 0.72, turn: 0.75, fireRate: 0.75,
    weapon: 'single',
    build: buildBulwark,
    notes: 'Six hull points. Turns like a freighter — plan your shots.',
  },
  {
    id: 'lancer',
    name: 'Lancer',
    tagline: 'Twin-cannon gunship',
    color: 0xff7a5e,
    hull: 3, speed: 1.10, turn: 1.05, fireRate: 0.85,
    weapon: 'twin',
    build: buildLancer,
    notes: 'Fires two parallel bolts. Double damage on a lined-up rock.',
  },
  {
    id: 'nova',
    name: 'Nova',
    tagline: 'Three-way scatter platform',
    color: 0xc78bff,
    hull: 3, speed: 0.92, turn: 0.95, fireRate: 0.65,
    weapon: 'spread',
    build: buildNova,
    notes: 'Slow gun, but a 3-shot fan that clears crowds at close range.',
  },
];

export function shipById(id) {
  return SHIPS.find((s) => s.id === id) || SHIPS[0];
}

export function buildShipMesh(spec) {
  const g = spec.build(spec.color);
  g.userData.spec = spec;
  return g;
}
