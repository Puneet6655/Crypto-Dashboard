// "Why flagged?" for a model score: SHAP contributions as bars that start at the middle line.
// Red bars push the score towards illicit, blue bars towards licit. Bar length is relative to the
// biggest contribution shown, so compare bars within one chart, not across charts.

const fmt = (v) => (v === null || v === undefined ? "missing" : Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : Number(v.toPrecision(4)).toString());

export default function ShapChart({ explanation }) {
  if (!explanation) return null;
  const { features, other_features: other, units, base_value: base, output, probability, method } = explanation;
  const largest = Math.max(...features.map((f) => Math.abs(f.contribution)), 1e-9);
  return (
    <div className="shap">
      <p className="small muted">
        {method}, in {units}. Average training score {base.toFixed(3)} + contributions = this score {output.toFixed(3)}
        {units === "log-odds" ? ` (probability ${probability.toFixed(3)})` : ""}.
      </p>
      <ul className="shap-list" aria-label="Feature contributions">
        {features.map((f) => {
          const width = `${(Math.abs(f.contribution) / largest) * 50}%`;
          const up = f.contribution > 0;
          return (
            <li key={f.feature} className="shap-row" title={`${f.feature} = ${fmt(f.value)}: ${up ? "+" : ""}${f.contribution.toFixed(4)} ${units}, ${f.pushes}`}>
              <span className="shap-name">
                <span className="mono">{f.feature}</span>
                <span className="small muted"> = {fmt(f.value)}</span>
              </span>
              <span className="shap-track">
                <span className={`shap-bar ${up ? "up" : "down"}`} style={up ? { left: "50%", width } : { right: "50%", width }} />
              </span>
              <span className={`shap-value ${up ? "up" : "down"}`}>{up ? "+" : ""}{f.contribution.toFixed(3)}</span>
            </li>
          );
        })}
      </ul>
      {other?.count > 0 && (
        <p className="small muted">
          {other.count} other features together: {other.contribution >= 0 ? "+" : ""}{other.contribution.toFixed(3)} {units}.
        </p>
      )}
      <p className="small"><span className="shap-key up" /> towards illicit <span className="shap-key down" /> towards licit</p>
      <p className="small muted">{explanation.note}</p>
    </div>
  );
}
