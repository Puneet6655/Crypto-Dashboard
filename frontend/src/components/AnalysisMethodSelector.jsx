import { compatibleMethods } from "../config/modes.js";

// Dropdown of methods that fit the chosen mode and input type.
// Planned methods appear greyed out with the reason they cannot run yet.
export default function AnalysisMethodSelector({ mode, inputType, value, onChange }) {
  const methods = compatibleMethods(mode, inputType);
  return (
    <label className="field">
      <span className="field-label">Analysis method</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {methods.map((m) => (
          <option key={m.id} value={m.id} disabled={m.status !== "ready"}>
            {m.status === "ready" ? m.label : `${m.label}: not yet, ${m.reason}`}
          </option>
        ))}
      </select>
    </label>
  );
}
