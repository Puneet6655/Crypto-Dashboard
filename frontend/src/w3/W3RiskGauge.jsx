import { useEffect, useRef, useState } from "react";

// Investigate (Web3 mode): a ring that sums up the result above the details panel.
// With a model score (Random Forest / XGBoost methods) the ring shows the score against the model's threshold,
// with an info button for which model it was. Other methods have no score, so the ring counts findings instead
// and says so: it never makes up a risk number.
export default function W3RiskGauge({ result }) {
  const [infoOpen, setInfoOpen] = useState(false);
  if (!result) return null;
  const ex = result.explanation;
  const matched = (result.indicators ?? []).filter((i) => i.matched === true).length;
  const findings = result.findings?.length ?? 0;

  if (ex && typeof ex.score === "number") {
    const tone = ex.flagged ? "high" : ex.score >= ex.threshold * 0.6 ? "mid" : "low";
    return (
      <section className={`card w3-gauge w3-gauge-${tone}`} aria-label="Model score">
        <Ring value={ex.score} max={1} marker={ex.threshold} decimals={3} />
        <div className="w3-gauge-text">
          <div className="w3-gauge-title">{ex.flagged ? "Flagged by the model" : "Below the alert line"}</div>
          <div className="small muted">Threshold {ex.threshold.toFixed(2)} · {findings} finding{findings === 1 ? "" : "s"}</div>
          <button type="button" className="w3-gauge-info" aria-expanded={infoOpen} onClick={() => setInfoOpen((o) => !o)}>
            {infoOpen ? "Hide model info" : "Which model?"}
          </button>
        </div>
        {infoOpen && (
          <dl className="w3-gauge-pop kv small">
            <dt>Model</dt><dd>{ex.model?.name ?? "unknown"}</dd>
            {ex.model?.family && <><dt>Family</dt><dd>{ex.model.family}</dd></>}
            {ex.model?.feature_set && <><dt>Features</dt><dd>{ex.model.feature_set}</dd></>}
            {ex.model?.version && <><dt>Version</dt><dd>{ex.model.version}</dd></>}
            <dt>Scored</dt><dd>{ex.benchmark === "live" ? "live, trained on 2016-17 labels" : "offline benchmark"}</dd>
            <dt>Why</dt><dd>See the "Why Flagged?" tab below</dd>
          </dl>
        )}
      </section>
    );
  }

  const tone = matched > 0 ? "high" : findings > 0 ? "mid" : "low";
  return (
    <section className={`card w3-gauge w3-gauge-${tone}`} aria-label="Result summary">
      <Ring value={findings} max={Math.max(findings, 5)} decimals={0} label="findings" />
      <div className="w3-gauge-text">
        <div className="w3-gauge-title">{matched > 0 ? `${matched} indicator${matched === 1 ? "" : "s"} matched` : findings ? "Findings to review" : "Nothing found"}</div>
        <div className="small muted">This method gives no model score. Random Forest or XGBoost do.</div>
      </div>
    </section>
  );
}

// SVG ring that fills and counts up to value. marker draws a tick at the threshold.
function Ring({ value, max, marker, decimals, label }) {
  const shown = useCountUp(value, 900);
  const r = 40;
  const c = 2 * Math.PI * r;
  const frac = Math.max(0, Math.min(1, shown / max));
  const markerAngle = marker != null ? (marker / max) * 360 - 90 : null;
  return (
    <div className="w3-ring">
      <svg viewBox="0 0 100 100" aria-hidden="true">
        <defs>
          <linearGradient id="w3-ring-grad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" style={{ stopColor: "var(--ring-a)" }} />
            <stop offset="1" style={{ stopColor: "var(--ring-b)" }} />
          </linearGradient>
        </defs>
        <circle cx="50" cy="50" r={r} className="w3-ring-track" />
        <circle cx="50" cy="50" r={r} className="w3-ring-fill" strokeDasharray={`${frac * c} ${c}`} transform="rotate(-90 50 50)" />
        {markerAngle != null && (
          <line x1="50" y1="4" x2="50" y2="16" className="w3-ring-marker" transform={`rotate(${markerAngle + 90} 50 50)`} />
        )}
      </svg>
      <div className="w3-ring-value">
        <strong>{shown.toFixed(decimals)}</strong>
        {label && <span>{label}</span>}
      </div>
    </div>
  );
}

function useCountUp(target, duration) {
  const [value, setValue] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { setValue(target); return undefined; }
    const start = performance.now();
    const begin = from.current;
    let frame;
    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - (1 - t) ** 3;
      const v = begin + (target - begin) * eased;
      setValue(v);
      from.current = v;
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, duration]);
  return value;
}
