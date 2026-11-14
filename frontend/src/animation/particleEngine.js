// Particle logo animation drawn on a <canvas>. No libraries.
//
// About 500 small dashes move through a loop:
//   scatter -> swirl into a ring -> ring spins -> fold into the logo -> hold -> burst back to scatter
//
// Usage:
//   const stop = startParticleLogo(canvas, { size: 360 });
//   ...later: stop();

const TAU = Math.PI * 2;

// Seconds spent in each step of the loop.
const TIMELINE = [
  { from: "scatter", to: "scatter", dur: 1.4 },
  { from: "scatter", to: "ring", dur: 1.6 },
  { from: "ring", to: "ring", dur: 2.0 },
  { from: "ring", to: "logo", dur: 1.3 },
  { from: "logo", to: "logo", dur: 2.4 },
  { from: "logo", to: "scatter", dur: 1.3 },
];
const LOOP_LENGTH = TIMELINE.reduce((sum, step) => sum + step.dur, 0);
// When loop is off, the animation stops once the logo is fully formed.
const LOGO_DONE_AT = TIMELINE.slice(0, 4).reduce((sum, step) => sum + step.dur, 0);

// Default logo: an open diamond (a "block") with a dot in the middle.
// Coordinates are in a box from -1 to 1, y pointing down. A segment with the
// same start and end point is drawn as a dot.
const corner = (x, y) => [x, y];
const shorten = (a, b, k) => [
  a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k,
  b[0] + (a[0] - b[0]) * k, b[1] + (a[1] - b[1]) * k,
];
const TOP = corner(0, -0.78), RIGHT = corner(0.78, 0), BOTTOM = corner(0, 0.78), LEFT = corner(-0.78, 0);
export const DEFAULT_LOGO = {
  thickness: 0.16,
  segments: [
    shorten(TOP, RIGHT, 0.14),
    shorten(RIGHT, BOTTOM, 0.14),
    shorten(BOTTOM, LEFT, 0.14),
    shorten(LEFT, TOP, 0.14),
    [-0.3, 0, 0.3, 0],
  ],
};

export const DEFAULT_COLORS = ["#c7d2fe", "#7aa2ff", "#4b6bd6", "#94a3b8", "#f5a524"];
export const DEFAULT_LOGO_COLOR = "#eef2ff";

const easeInOut = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
const lerp = (a, b, p) => a + (b - a) * p;
// Dashes look the same turned by half a circle, so blend angles modulo PI.
function lerpAngle(a, b, p) {
  let d = (b - a) % Math.PI;
  if (d > Math.PI / 2) d -= Math.PI;
  if (d < -Math.PI / 2) d += Math.PI;
  return a + d * p;
}
function hexToRgb(hex) {
  const n = parseInt(hex.replace("#", ""), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

// Spread the particles along the logo strokes, more on longer strokes.
function logoSlots(logo, count) {
  const strokes = logo.segments.map(([x1, y1, x2, y2]) => ({ x1, y1, x2, y2, len: Math.hypot(x2 - x1, y2 - y1) }));
  const dots = strokes.filter((s) => s.len === 0).length;
  const total = strokes.reduce((sum, s) => sum + s.len, 0) || 1;
  const forDots = dots * 4;
  const slots = [];
  strokes.forEach((s) => {
    const k = s.len === 0 ? 4 : Math.max(1, Math.round(((count - forDots) * s.len) / total));
    const angle = Math.atan2(s.y2 - s.y1, s.x2 - s.x1);
    for (let i = 0; i < k; i++) {
      // Each particle covers its piece of the stroke plus some overlap, so the
      // pieces join into one solid bar.
      const a = Math.max(0, (i - 0.6) / k), b = Math.min(1, (i + 1.6) / k);
      const mid = (a + b) / 2;
      slots.push({
        x: lerp(s.x1, s.x2, mid), y: lerp(s.y1, s.y2, mid),
        angle, half: ((b - a) * s.len) / 2,
      });
    }
  });
  return slots;
}

function makeParticles(count, colors, logo) {
  const slots = logoSlots(logo, count);
  const rgb = colors.map(hexToRgb);
  return Array.from({ length: count }, (_, i) => {
    const r = Math.sqrt(Math.random()) * 0.95;
    const t = Math.random() * TAU;
    const band = Math.floor(Math.random() * 7);
    return {
      // scatter
      sx: Math.cos(t) * r, sy: Math.sin(t) * r,
      sa: Math.random() * TAU, spin: (Math.random() - 0.5) * 2,
      phase: Math.random() * TAU, wobble: 0.6 + Math.random(),
      // ring: 7 bands between 60% and 100% of the radius
      ra: Math.random() * TAU, rr: 0.6 + band * 0.065 + Math.random() * 0.02,
      // logo (particles past the last slot double up on random slots)
      slot: slots[i < slots.length ? i : Math.floor(Math.random() * slots.length)],
      color: rgb[Math.floor(Math.random() ** 1.6 * rgb.length)],
    };
  });
}

// Where one particle sits in a given state at time t. Units: radius = 1.
function pose(pt, state, t, logo) {
  if (state === "scatter") {
    return {
      x: pt.sx + Math.sin(t * pt.wobble + pt.phase) * 0.03,
      y: pt.sy + Math.cos(t * pt.wobble * 0.8 + pt.phase) * 0.03,
      a: pt.sa + t * pt.spin, half: 0.018, w: 0.009, logo: 0,
    };
  }
  if (state === "ring") {
    const a = pt.ra + t * 0.55; // the ring keeps turning
    return {
      x: Math.cos(a) * pt.rr, y: Math.sin(a) * pt.rr,
      a: a + Math.PI / 2 + 0.35, half: 0.022, w: 0.011, logo: 0,
    };
  }
  const s = pt.slot;
  return { x: s.x * 0.85, y: s.y * 0.85, a: s.angle, half: s.half * 0.85, w: logo.thickness * 0.85, logo: 1 };
}

function stepAt(time) {
  let t = time % LOOP_LENGTH;
  for (const step of TIMELINE) {
    if (t < step.dur) return { ...step, p: t / step.dur };
    t -= step.dur;
  }
  return { ...TIMELINE[0], p: 0 };
}

export function startParticleLogo(canvas, options = {}) {
  const {
    size = 360,
    count = 500,
    colors = DEFAULT_COLORS,
    logoColor = DEFAULT_LOGO_COLOR,
    logo = DEFAULT_LOGO,
    loop = true,
  } = options;

  const ctx = canvas.getContext("2d");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = size * dpr;
  canvas.height = size * dpr;
  canvas.style.width = `${size}px`;
  canvas.style.height = `${size}px`;

  const particles = makeParticles(count, colors, logo);
  const logoRgb = hexToRgb(logoColor);
  const scale = (size / 2) * 0.9;

  function draw(time, step) {
    const k = easeInOut(step.p);
    // Spin the cloud around the centre while it changes shape.
    const swirl = step.from === step.to ? 0 : Math.sin(Math.PI * k) * (step.to === "scatter" ? -1.4 : 1.1);
    const cos = Math.cos(swirl), sin = Math.sin(swirl);

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size, size);
    ctx.lineCap = "round";

    // Dashes only thicken into bars at the very end of forming the logo, and thin
    // out quickly when it breaks apart, so the shapes never look like blobs.
    let kw = k;
    if (step.to === "logo" && step.from !== "logo") kw = Math.pow(k, 4);
    if (step.from === "logo" && step.to !== "logo") kw = Math.pow(step.p, 0.35);

    for (const pt of particles) {
      const a = pose(pt, step.from, time, logo);
      const b = step.from === step.to ? a : pose(pt, step.to, time, logo);
      const x0 = lerp(a.x, b.x, k), y0 = lerp(a.y, b.y, k);
      const x = x0 * cos - y0 * sin, y = x0 * sin + y0 * cos;
      const angle = lerpAngle(a.a, b.a, k);
      const half = lerp(a.half, b.half, kw) * scale;
      const m = lerp(a.logo, b.logo, k);
      const c = pt.color;
      const dx = Math.cos(angle) * half, dy = Math.sin(angle) * half;
      const cx = size / 2 + x * scale, cy = size / 2 + y * scale;

      ctx.strokeStyle = `rgb(${lerp(c[0], logoRgb[0], m) | 0},${lerp(c[1], logoRgb[1], m) | 0},${lerp(c[2], logoRgb[2], m) | 0})`;
      ctx.lineWidth = Math.max(1, lerp(a.w, b.w, kw) * scale);
      ctx.beginPath();
      ctx.moveTo(cx - dx, cy - dy);
      ctx.lineTo(cx + dx + 0.01, cy + dy); // +0.01 so a zero-length dot still draws
      ctx.stroke();
    }
  }

  // People who ask their system for less motion just see the finished logo.
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  if (reduce) {
    draw(0, { from: "logo", to: "logo", p: 1 });
    return () => {};
  }

  let frame = 0;
  let start = null;
  function tick(now) {
    if (start === null) start = now;
    const time = (now - start) / 1000;
    if (!loop && time >= LOGO_DONE_AT) {
      draw(time, { from: "logo", to: "logo", p: 1 });
      return;
    }
    draw(time, stepAt(time));
    frame = requestAnimationFrame(tick);
  }
  frame = requestAnimationFrame(tick);
  return () => cancelAnimationFrame(frame);
}
