import { useEffect, useRef } from "react";

// A cloud of short glowing dashes that loops: loose cloud -> spinning ring -> shield logo.
// Drawn on one <canvas> with no library. Shows a still ring when the user asks for less motion.

const COUNT = 380;
const COLOURS = ["#c4b5fd", "#a78bfa", "#8b5cf6", "#ddd6fe", "#f0abfc", "#67e8f9"];
const WEIGHTS = [0.24, 0.22, 0.18, 0.16, 0.12, 0.08];
const SHIELD = "M12 2 3 6v6c0 5 3.8 9.4 9 10 5.2-.6 9-5 9-10V6l-9-4Zm-3 9.5 2 2 4-4.5";
const PHASES = [
  { name: "cloud", ms: 2600 },
  { name: "ring", ms: 4200 },
  { name: "shield", ms: 3400 },
];

function pickColour() {
  let r = Math.random();
  for (let i = 0; i < COLOURS.length; i += 1) {
    r -= WEIGHTS[i];
    if (r <= 0) return COLOURS[i];
  }
  return COLOURS[0];
}

// Points along the shield outline, in a -1..1 box, sampled from an off-screen drawing.
function shieldPoints(count) {
  const size = 160;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  const scale = size / 24;
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  ctx.lineWidth = 1.3;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.stroke(new Path2D(SHIELD));
  const data = ctx.getImageData(0, 0, size, size).data;
  const found = [];
  for (let y = 0; y < size; y += 2) {
    for (let x = 0; x < size; x += 2) {
      if (data[(y * size + x) * 4 + 3] > 120) found.push([(x / size) * 2 - 1, (y / size) * 2 - 1]);
    }
  }
  for (let i = found.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [found[i], found[j]] = [found[j], found[i]];
  }
  return Array.from({ length: count }, (_, i) => found[i % found.length] ?? [0, 0]);
}

export default function ParticleSwirl({ className = "" }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const shield = shieldPoints(COUNT);
    const parts = Array.from({ length: COUNT }, (_, i) => {
      const a = Math.random() * Math.PI * 2;
      const d = Math.sqrt(Math.random());
      return {
        x: 0, y: 0, vx: 0, vy: 0,
        cloud: [Math.cos(a) * d * 0.9, Math.sin(a) * d * 0.9],
        ringAngle: Math.random() * Math.PI * 2,
        ringRadius: 0.55 + Math.random() * 0.4,
        shield: shield[i],
        spin: Math.random() * Math.PI * 2,
        len: 3 + Math.random() * 5,
        speed: 0.04 + Math.random() * 0.05,
        colour: pickColour(),
      };
    });

    let width = 0, height = 0, radius = 0;
    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const box = canvas.getBoundingClientRect();
      width = box.width;
      height = box.height;
      radius = Math.min(width, height) * 0.46;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    const observer = new ResizeObserver(() => { resize(); if (still) draw(1, 0); });
    observer.observe(canvas);
    parts.forEach((p) => { p.x = p.cloud[0]; p.y = p.cloud[1]; });

    function target(p, phase, t) {
      if (phase === "ring") {
        const a = p.ringAngle + t * 0.0007;
        return [Math.cos(a) * p.ringRadius, Math.sin(a) * p.ringRadius];
      }
      if (phase === "shield") return p.shield;
      const wobble = Math.sin(t * 0.0012 + p.spin) * 0.04;
      return [p.cloud[0] + wobble, p.cloud[1] - wobble];
    }

    function draw(t, phaseIndex) {
      const phase = PHASES[phaseIndex].name;
      ctx.clearRect(0, 0, width, height);
      ctx.lineCap = "round";
      ctx.lineWidth = 1.6;
      for (const p of parts) {
        const [tx, ty] = target(p, phase, t);
        if (still) {
          p.x = tx; p.y = ty;
        } else {
          p.vx = p.vx * 0.82 + (tx - p.x) * p.speed;
          p.vy = p.vy * 0.82 + (ty - p.y) * p.speed;
          p.x += p.vx;
          p.y += p.vy;
        }
        const moving = Math.hypot(p.vx, p.vy) > 0.002;
        let angle;
        if (moving) angle = Math.atan2(p.vy, p.vx);
        else if (phase === "ring") angle = Math.atan2(p.y, p.x) + Math.PI / 2;
        else angle = p.spin + t * 0.001;
        const cx = width / 2 + p.x * radius;
        const cy = height / 2 + p.y * radius;
        const dx = Math.cos(angle) * p.len * 0.5;
        const dy = Math.sin(angle) * p.len * 0.5;
        ctx.strokeStyle = p.colour;
        ctx.beginPath();
        ctx.moveTo(cx - dx, cy - dy);
        ctx.lineTo(cx + dx, cy + dy);
        ctx.stroke();
      }
    }

    if (still) {
      draw(1, 1);
      return () => observer.disconnect();
    }

    let frame = 0;
    let phaseIndex = 0;
    let phaseStart = performance.now();
    function tick(now) {
      if (now - phaseStart > PHASES[phaseIndex].ms) {
        phaseIndex = (phaseIndex + 1) % PHASES.length;
        phaseStart = now;
      }
      draw(now, phaseIndex);
      frame = requestAnimationFrame(tick);
    }
    frame = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, []);

  return <canvas ref={canvasRef} className={className} aria-hidden="true" />;
}
