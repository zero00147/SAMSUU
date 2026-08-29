// All tuning in one place. The three levels differ only by the numbers below, so
// difficulty can be rebalanced without touching the engine.

// The playfield is a rectangle on the z = 0 plane. Anything that leaves it wraps to the
// opposite edge, classic-Asteroids style, which is why the camera never needs to move.
export const WORLD = { w: 124, h: 72 };

export const BASE = {
  thrust: 46,          // units/s² at full throttle
  turn: 3.4,           // radians/s
  drag: 0.44,          // velocity decay per second (fraction)
  maxSpeed: 42,
  fireInterval: 0.26,  // seconds between shots at fireRate 1.0
  bulletSpeed: 62,
  bulletLife: 1.25,    // seconds
  invulnTime: 2.2,     // seconds of blinking immunity after a hit
  respawnDelay: 1.1,
};

export const ASTEROID = {
  // radius and score by size tier
  3: { radius: 3.5, score: 20, splits: 2 },
  2: { radius: 2.1, score: 50, splits: 2 },
  1: { radius: 1.2, score: 100, splits: 0 },
};

export const LEVELS = [
  {
    n: 1,
    name: 'Belt Patrol',
    brief: 'A quiet stretch of the outer belt. Clear the rocks.',
    asteroids: 7,
    rockSpeed: [4, 8],
    drones: 0,
    droneInterval: 0,
    droneSpeed: 0,
    fog: 0x05070d,
    hue: 0x2b4a7a,
  },
  {
    n: 2,
    name: 'Debris Field',
    brief: 'Denser, faster — and someone is watching. Two hunter drones inbound.',
    asteroids: 10,
    rockSpeed: [6, 12],
    drones: 2,
    droneInterval: 2.6,
    droneSpeed: 13,
    fog: 0x0a0512,
    hue: 0x6a2b8a,
  },
  {
    n: 3,
    name: 'Core Breach',
    brief: 'Full swarm. Four drones, fast rock, no margin. Survive it.',
    asteroids: 13,
    rockSpeed: [9, 17],
    drones: 4,
    droneInterval: 1.6,
    droneSpeed: 17,
    fog: 0x120508,
    hue: 0x9a2b2b,
  },
];

export const DRONE = {
  radius: 1.3,
  hull: 2,
  score: 250,
  bulletSpeed: 34,
  bulletLife: 2.4,
  preferredRange: 26,   // it circles at roughly this distance
};

// Wrap a position vector into the playfield.
export function wrap(v) {
  const hw = WORLD.w / 2;
  const hh = WORLD.h / 2;
  if (v.x > hw) v.x -= WORLD.w;
  else if (v.x < -hw) v.x += WORLD.w;
  if (v.y > hh) v.y -= WORLD.h;
  else if (v.y < -hh) v.y += WORLD.h;
}

// Shortest vector from a to b across the wrapping playfield, so drones chase through
// the edges instead of turning round and flying the long way.
export function shortestDelta(from, to, out) {
  out.set(to.x - from.x, to.y - from.y, 0);
  if (out.x > WORLD.w / 2) out.x -= WORLD.w;
  else if (out.x < -WORLD.w / 2) out.x += WORLD.w;
  if (out.y > WORLD.h / 2) out.y -= WORLD.h;
  else if (out.y < -WORLD.h / 2) out.y += WORLD.h;
  return out;
}
