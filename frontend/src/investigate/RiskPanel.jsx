import { useEffect, useRef, useState } from "react";
import { MODEL_FAMILIES, bitcoinAddressOf } from "../services/modelScore.js";
import W3RiskGauge from "../w3/W3RiskGauge.jsx";

// Investigate, right column: the live model risk score. It comes automatically with every Bitcoin address lookup
// (no need to pick a model method), can be switched between XGBoost and Random Forest, and any other Bitcoin address
// selected in the graph can be scored with one click. Results that are not about a Bitcoin address keep the
// findings summary ring, because the models only score Bitcoin addresses.
export default function RiskPanel({ inv, result, selectedId, onShowWhy }) {
  const subject = inv.subjectAddress;
  const selected = bitcoinAddressOf(selectedId);
  const target = selected ?? subject;

  if (!result) return null;
  if (!target) {
    const bitcoinTx = result.input?.type === "bitcoin_txid" && result.data_mode === "live";
    if (!bitcoinTx) return <W3RiskGauge result={result} />;
    return (
      <section className="card inv-risk" aria-label="Model risk score">
        <RiskHead inv={inv} />
        <div className="inv-risk-empty">
          <Ring value={null} />
          <p className="small muted">The models score addresses, not transactions. Click an address in the graph to score it.</p>
        </div>
      </section>
    );
  }

  const entry = inv.scoreOf(target);
  const isSubject = target === subject;
  const ex = entry?.status === "done" ? entry.data : null;
  const tone = !ex ? "idle" : ex.flagged ? "high" : ex.score >= ex.threshold * 0.6 ? "mid" : "low";

  return (
    <section className={`card inv-risk inv-tone-${tone}`} aria-label="Model risk score" aria-busy={entry?.status === "loading"}>
      <RiskHead inv={inv} />
      <div className="inv-risk-target small">
        <span className="inv-risk-who">{isSubject ? "Looked-up address" : "Selected in the graph"}</span>
        <span className="mono" title={target}>{shorten(target)}</span>
      </div>

      {!entry && (
        <div className="inv-risk-empty">
          <Ring value={null} />
          <div>
            <button type="button" className="primary compact" onClick={() => inv.scoreAddress(target)}>Score this address</button>
            <p className="small muted">Reads its history from mempool.space, usually 5 to 30 seconds.</p>
          </div>
        </div>
      )}

      {entry?.status === "loading" && <Scoring startedAt={entry.startedAt} />}

      {entry?.status === "error" && (
        <div className="inv-risk-empty">
          <Ring value={null} />
          <div>
            <p className="small error-text">{entry.error}</p>
            <button type="button" className="secondary compact" onClick={() => inv.scoreAddress(target, inv.modelFamily, { force: true })}>Try again</button>
          </div>
        </div>
      )}

      {ex && (
        <>
          <div className="inv-risk-main">
            <Ring value={ex.score} marker={ex.threshold} />
            <div className="inv-risk-verdict">
              <strong>{ex.flagged ? "Flagged by the model" : ex.score >= ex.threshold * 0.6 ? "Close to the alert line" : "Below the alert line"}</strong>
              <span className="small muted">
                Alert line {ex.threshold.toFixed(2)}
                {ex.recent_2026_threshold ? ` · 2026 line ${ex.recent_2026_threshold.toFixed(3)}` : ""}
              </span>
              <span className="small muted">{ex.model?.name}</span>
            </div>
          </div>
          <Drivers explanation={ex.explanation} />
          <div className="inv-risk-actions">
            {isSubject && <button type="button" className="secondary compact" onClick={onShowWhy}>Full explanation</button>}
            <span className="small muted">Live score, trained on 2016-17 labels</span>
          </div>
        </>
      )}
    </section>
  );
}

function RiskHead({ inv }) {
  return (
    <div className="inv-risk-head">
      <h2>Model risk score</h2>
      <div className="segmented small inv-family" role="radiogroup" aria-label="Model">
        {MODEL_FAMILIES.map((f) => (
          <button key={f.id} type="button" role="radio" aria-checked={inv.modelFamily === f.id}
            className={inv.modelFamily === f.id ? "active" : ""} onClick={() => inv.setModelFamily(f.id)}>
            {f.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// The features that moved the score most, as small bars: red pushes towards illicit, blue towards licit.
function Drivers({ explanation }) {
  const [open, setOpen] = useState(null);
  if (!explanation?.features?.length) return null;
  const top = explanation.features.slice(0, 4);
  const largest = Math.max(...top.map((f) => Math.abs(f.contribution)), 1e-9);
  return (
    <div className="inv-drivers">
      <div className="small muted">What moved the score most</div>
      <ul>
        {top.map((f) => {
          const up = f.contribution > 0;
          return (
            <li key={f.feature}>
              <button type="button" className={`inv-driver ${open === f.feature ? "on" : ""}`} aria-expanded={open === f.feature}
                onClick={() => setOpen((o) => (o === f.feature ? null : f.feature))}>
                <span className="inv-driver-name">{f.feature.replaceAll("_", " ")}</span>
                <span className="inv-driver-track">
                  <span className={`inv-driver-bar ${up ? "up" : "down"}`} style={{ width: `${(Math.abs(f.contribution) / largest) * 100}%` }} />
                </span>
              </button>
              {open === f.feature && (
                <div className="small muted inv-driver-more">
                  Value {f.value == null ? "missing" : Number(f.value.toPrecision(4)).toLocaleString()} ·{" "}
                  {up ? "+" : ""}{f.contribution.toFixed(3)} {explanation.units}, {f.pushes}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Scoring({ startedAt }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(timer);
  }, []);
  const seconds = Math.max(0, (now - (startedAt ?? now)) / 1000);
  return (
    <div className="inv-risk-empty" role="status">
      <Ring value={null} spinning />
      <div>
        <strong>Scoring... {seconds.toFixed(0)} s</strong>
        <p className="small muted">
          {seconds < 12 ? "Reading the address history and building its features." : "Busy addresses take longer: up to about 30 seconds."}
        </p>
      </div>
    </div>
  );
}

// Score ring: fills and counts up to the score; the tick marks the model's alert line.
function Ring({ value, marker, spinning = false }) {
  const shown = useCountUp(value ?? 0, 900);
  const r = 40;
  const c = 2 * Math.PI * r;
  const frac = value == null ? 0 : Math.max(0, Math.min(1, shown));
  return (
    <div className={`inv-ring ${spinning ? "spinning" : ""}`}>
      <svg viewBox="0 0 100 100" aria-hidden="true">
        <circle cx="50" cy="50" r={r} className="inv-ring-track" />
        {spinning && <circle cx="50" cy="50" r={r} className="inv-ring-spin" strokeDasharray={`${c * 0.22} ${c}`} />}
        {value != null && (
          <circle cx="50" cy="50" r={r} className="inv-ring-fill" strokeDasharray={`${frac * c} ${c}`} transform="rotate(-90 50 50)" />
        )}
        {marker != null && (
          <line x1="50" y1="3" x2="50" y2="17" className="inv-ring-marker" transform={`rotate(${marker * 360} 50 50)`} />
        )}
      </svg>
      <div className="inv-ring-value">
        {value == null ? <span className="muted">{spinning ? "" : "?"}</span> : <strong>{shown.toFixed(3)}</strong>}
      </div>
    </div>
  );
}

function useCountUp(target, duration) {
  const [value, setValue] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { setValue(target); from.current = target; return undefined; }
    const start = performance.now();
    const begin = from.current;
    let frame;
    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration);
      const v = begin + (target - begin) * (1 - (1 - t) ** 3);
      setValue(v);
      from.current = v;
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, duration]);
  return value;
}

function shorten(text) {
  return text.length > 22 ? `${text.slice(0, 10)}…${text.slice(-8)}` : text;
}
