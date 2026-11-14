import "./overview-hero.css";

// Overview hero: purple glass banner with a light beam, floating coins.
// It lives in its own files so the existing pages stay exactly as they were.
// Open it at #/overview.

// Coins around the beam: left/top are % of the hero, delay staggers the float.
const COINS = [
  { id: "btc", left: 13, top: 50, size: 64, delay: 0 },
  { id: "eth", left: 87, top: 46, size: 60, delay: 1.2 },
  { id: "sol", left: 75, top: 62, size: 50, delay: 2.1 },
  { id: "btc", left: 24, top: 20, size: 44, delay: 0.6, faint: true },
  { id: "eth", left: 7, top: 34, size: 40, delay: 1.7, faint: true },
  { id: "sol", left: 92, top: 26, size: 40, delay: 2.6, faint: true },
];

const STREAKS = [8, 19, 31, 44, 57, 66, 78, 90]; // falling light lines, % from the left

export default function OverviewHero() {
  return (
    <section className="oh-hero" aria-labelledby="oh-title">
      <div className="oh-bg" aria-hidden="true">
        <div className="oh-glow" />
        <div className="oh-planet" />
        <div className="oh-beam" />
        <div className="oh-beam-core" />
        {STREAKS.map((left, i) => (
          <span key={left} className="oh-streak" style={{ left: `${left}%`, animationDelay: `${i * 0.7}s` }} />
        ))}
        {COINS.map((c, i) => (
          <span
            key={i}
            className={`oh-coin ${c.faint ? "faint" : ""}`}
            style={{ left: `${c.left}%`, top: `${c.top}%`, width: c.size, height: c.size, animationDelay: `${c.delay}s` }}
          >
            <CoinIcon id={c.id} />
          </span>
        ))}
      </div>

      <div className="oh-content">
        <span className="oh-badge">
          <span className="oh-dot" /> Blockchain intelligence
        </span>
        <h1 id="oh-title" className="oh-title">
          Follow the money across <span className="oh-gradient-text">every chain</span>
        </h1>
        <p className="oh-sub">
          Look up addresses and transactions, score them with trained models and keep the trail in cases.
        </p>
        <div className="oh-actions">
          <a className="oh-btn primary" href="#/investigate">Start an investigation</a>
          <a className="oh-btn ghost" href="#/alerts">Open live alerts</a>
        </div>
      </div>

    </section>
  );
}

function CoinIcon({ id }) {
  if (id === "btc") {
    return (
      <svg viewBox="0 0 24 24" aria-label="Bitcoin" role="img">
        <path fill="currentColor" d="M14.6 11.6c.9-.4 1.5-1.2 1.4-2.4-.2-1.6-1.6-2.1-3.3-2.3V5h-1.3v1.8h-1V5H9.1v1.8H6.8v1.4h.9c.5 0 .7.3.7.6v6.4c0 .3-.2.6-.6.6h-.9l-.3 1.5h2.5V19h1.3v-1.7h1V19h1.3v-1.7c2.3-.1 3.8-.7 4-2.7.2-1.6-.6-2.4-2.1-2.9zM10 8.3h1.8c1 0 2.2.2 2.2 1.4 0 1.2-1.2 1.4-2.2 1.4H10V8.3zm2.1 7.5H10v-3.2h2.1c1.1 0 2.5.3 2.5 1.6 0 1.4-1.4 1.6-2.5 1.6z" />
      </svg>
    );
  }
  if (id === "eth") {
    return (
      <svg viewBox="0 0 24 24" aria-label="Ethereum" role="img">
        <path fill="currentColor" opacity="0.9" d="M12 3 6.5 12.2 12 15.4l5.5-3.2z" />
        <path fill="currentColor" opacity="0.6" d="M12 16.4 6.5 13.2 12 21l5.5-7.8z" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" aria-label="Solana" role="img">
      <path fill="currentColor" d="M7.2 6h11.3l-1.7 2.2H5.5zM5.5 10.9h11.3l1.7 2.2H7.2zM7.2 15.8h11.3L16.8 18H5.5z" />
    </svg>
  );
}
