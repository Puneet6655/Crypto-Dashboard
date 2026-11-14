import { useEffect, useRef, useState } from "react";

// Full-screen animated background for Web3 mode. It sits behind every page (position: fixed).
//   mode "full": Home. Glow, planet arc, light beam, falling streaks and floating coins.
//   mode "calm": working pages. Same colours, slow glow and a soft light that follows the mouse, nothing flying.
// pulse: a number that goes up when a live alert arrives; each step sends a ripple of light across the screen.
// Every class starts with "w3-bd-" (styles in w3-shell.css).

const COINS = [
  { id: "btc", left: 12, top: 56, size: 72, delay: 0 },
  { id: "eth", left: 88, top: 48, size: 64, delay: 1.2 },
  { id: "sol", left: 76, top: 72, size: 52, delay: 2.1 },
  { id: "btc", left: 26, top: 18, size: 44, delay: 0.6, faint: true },
  { id: "eth", left: 6, top: 30, size: 40, delay: 1.7, faint: true },
  { id: "sol", left: 93, top: 20, size: 40, delay: 2.6, faint: true },
  { id: "eth", left: 38, top: 84, size: 36, delay: 3.1, faint: true },
];
const STREAKS = [6, 15, 27, 39, 52, 61, 73, 84, 95];

export default function W3Backdrop({ mode = "calm", pulse = 0 }) {
  const ref = useRef(null);
  const [ripples, setRipples] = useState([]);
  const lastPulse = useRef(pulse);

  // A ripple per new alert, removed when its animation ends.
  useEffect(() => {
    if (pulse <= lastPulse.current) { lastPulse.current = pulse; return; }
    lastPulse.current = pulse;
    const id = `${pulse}-${Date.now()}`;
    setRipples((r) => [...r.slice(-2), id]);
    setTimeout(() => setRipples((r) => r.filter((x) => x !== id)), 2800);
  }, [pulse]);

  // Soft light that follows the mouse (one CSS variable update per frame at most).
  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return undefined;
    let frame = 0;
    function move(e) {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        ref.current?.style.setProperty("--w3-mx", `${e.clientX}px`);
        ref.current?.style.setProperty("--w3-my", `${e.clientY}px`);
      });
    }
    window.addEventListener("pointermove", move, { passive: true });
    return () => { window.removeEventListener("pointermove", move); cancelAnimationFrame(frame); };
  }, []);

  return (
    <div ref={ref} className={`w3-bd w3-bd-${mode}`} aria-hidden="true">
      <div className="w3-bd-glow" />
      <div className="w3-bd-mouse" />
      <div className="w3-bd-planet" />
      <div className="w3-bd-beam" />
      <div className="w3-bd-beam-core" />
      {mode === "full" && STREAKS.map((left, i) => (
        <span key={left} className="w3-bd-streak" style={{ left: `${left}%`, animationDelay: `${i * 0.7}s` }} />
      ))}
      {mode === "full" && COINS.map((c, i) => (
        <span key={i} className={`w3-bd-coin ${c.faint ? "faint" : ""}`}
          style={{ left: `${c.left}%`, top: `${c.top}%`, width: c.size, height: c.size, animationDelay: `${c.delay}s` }}>
          <CoinIcon id={c.id} />
        </span>
      ))}
      {ripples.map((id) => <span key={id} className="w3-bd-ripple" />)}
      <div className="w3-bd-grain" />
    </div>
  );
}

function CoinIcon({ id }) {
  if (id === "btc") {
    return (
      <svg viewBox="0 0 24 24"><path fill="currentColor" d="M14.6 11.6c.9-.4 1.5-1.2 1.4-2.4-.2-1.6-1.6-2.1-3.3-2.3V5h-1.3v1.8h-1V5H9.1v1.8H6.8v1.4h.9c.5 0 .7.3.7.6v6.4c0 .3-.2.6-.6.6h-.9l-.3 1.5h2.5V19h1.3v-1.7h1V19h1.3v-1.7c2.3-.1 3.8-.7 4-2.7.2-1.6-.6-2.4-2.1-2.9zM10 8.3h1.8c1 0 2.2.2 2.2 1.4 0 1.2-1.2 1.4-2.2 1.4H10V8.3zm2.1 7.5H10v-3.2h2.1c1.1 0 2.5.3 2.5 1.6 0 1.4-1.4 1.6-2.5 1.6z" /></svg>
    );
  }
  if (id === "eth") {
    return (
      <svg viewBox="0 0 24 24">
        <path fill="currentColor" opacity="0.9" d="M12 3 6.5 12.2 12 15.4l5.5-3.2z" />
        <path fill="currentColor" opacity="0.6" d="M12 16.4 6.5 13.2 12 21l5.5-7.8z" />
      </svg>
    );
  }
  return <svg viewBox="0 0 24 24"><path fill="currentColor" d="M7.2 6h11.3l-1.7 2.2H5.5zM5.5 10.9h11.3l1.7 2.2H7.2zM7.2 15.8h11.3L16.8 18H5.5z" /></svg>;
}
