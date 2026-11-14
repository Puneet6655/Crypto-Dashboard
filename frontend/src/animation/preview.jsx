// Stand-alone preview page for the particle logo. It has its own entry
// (animation.html), so the dashboard's main.jsx and pages are not touched.
import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import ParticleLogo from "./ParticleLogo.jsx";
import "./particleLogo.css";

function Preview() {
  const [loop, setLoop] = useState(true);
  const [replay, setReplay] = useState(0);
  const size = Math.min(440, window.innerWidth - 32);

  return (
    <main className="pl-page">
      <div className="pl-stage">
        <ParticleLogo size={size} loop={loop} replayKey={replay} />
        <h1 className="pl-title">Blockchain Intelligence</h1>
        <p className="pl-sub">Particle logo preview</p>
        <div className="pl-controls">
          <button className="pl-btn" onClick={() => setReplay((n) => n + 1)}>Replay</button>
          <button className="pl-btn" aria-pressed={loop} onClick={() => setLoop((v) => !v)}>
            {loop ? "Looping" : "Play once"}
          </button>
        </div>
      </div>
    </main>
  );
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <Preview />
  </StrictMode>
);
