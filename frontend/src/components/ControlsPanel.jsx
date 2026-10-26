import { INPUT_TYPES, MODES } from "../config/modes.js";
import { FIXTURE_CASES } from "../data/fixtures.js";
import AnalysisMethodSelector from "./AnalysisMethodSelector.jsx";

// Left column: everything the analyst chooses before pressing Start.
// It holds no state of its own; App passes values down and gets changes back.
export default function ControlsPanel({ form, onFormChange, onCaseChange, onStart, isLoading }) {
  const mode = MODES.find((m) => m.id === form.mode);
  const cases = FIXTURE_CASES.filter((c) => c.mode === form.mode);

  return (
    <section className="card controls" aria-label="Investigation controls">
      <label className="field">
        <span className="field-label">Investigation mode</span>
        <select value={form.mode} onChange={(event) => onFormChange({ mode: event.target.value })}>
          {MODES.map((m) => (
            <option key={m.id} value={m.id}>{m.label}</option>
          ))}
        </select>
      </label>

      <label className="field">
        <span className="field-label">Input type</span>
        <select value={form.inputType} onChange={(event) => onFormChange({ inputType: event.target.value })}>
          {mode.inputTypes.map((t) => (
            <option key={t} value={t}>{INPUT_TYPES[t].label}</option>
          ))}
        </select>
      </label>

      <label className="field">
        <span className="field-label">{INPUT_TYPES[form.inputType].label}</span>
        <input
          type="text"
          value={form.value}
          placeholder={INPUT_TYPES[form.inputType].placeholder}
          onChange={(event) => onFormChange({ value: event.target.value, caseId: "" })}
        />
      </label>

      <label className="field">
        <span className="field-label">Preloaded case</span>
        <select value={form.caseId} onChange={(event) => onCaseChange(event.target.value)}>
          <option value="">None (type your own)</option>
          {cases.map((c) => (
            <option key={c.caseId} value={c.caseId}>{c.label}</option>
          ))}
        </select>
      </label>

      <AnalysisMethodSelector
        mode={form.mode}
        inputType={form.inputType}
        value={form.method}
        onChange={(method) => onFormChange({ method })}
      />

      <button className="primary" type="button" onClick={onStart} disabled={isLoading}>
        {isLoading ? "Investigating..." : "Start Investigation"}
      </button>
    </section>
  );
}
