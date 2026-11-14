import { useEffect } from "react";
import { INPUT_TYPES, MODES, compatibleMethods } from "../config/modes.js";
import { validateInput } from "../services/runFixtureInvestigation.js";
import { CHAIN_INFO, detectInput } from "./chains.js";
import { ChainLogo } from "./ChainBadge.jsx";

// Investigate controls in Web3 mode: one search box that works out the chain and type by itself,
// method cards that say where the data comes from, and a start button that says what it will do.
// Demo (synthetic) cases are not offered here: live lookups replace them.

const chainOfType = (t) => t.split("_")[0]; // "ethereum_address" -> "ethereum", "url_domain" -> "url"

// Short name and data source per method (and per chain for the live ones).
const METHOD_INFO = {
  live_lookup: {
    name: "Live lookup",
    source: {
      bitcoin: "mempool.space (blockstream.info as backup) · free",
      ethereum: "Etherscan, then Blockscout and Alchemy as backups · free keys",
      base: "Alchemy · uses compute units",
      solana: "Alchemy · uses compute units",
    },
  },
  rules_v1: { name: "Indicator rules", source: "Scam-website lists, OFAC and Ransomwhere, checked locally · free" },
  graph_measures: { name: "Graph measures", source: "Bridges and value flow (NetworkX) on the live lookup's data" },
  random_forest: { name: "Random Forest score", source: "Features from mempool.space, with a SHAP explanation · free" },
  xgboost: { name: "XGBoost score", source: "Features from mempool.space, with a SHAP explanation · free" },
};
const HIDDEN_METHODS = new Set(["fixture_review"]); // demo cases

const startLabel = (t) => (t === "url_domain" ? "website" : INPUT_TYPES[t].label);

function methodsFor(form) {
  return compatibleMethods(form.mode, form.inputType).filter((m) => m.status === "ready" && !HIDDEN_METHODS.has(m.id));
}

// Which mode fits a detected type: chain lookups go to Crypto Investigation, websites to Scam & Early Warning.
function modeFor(inputType) {
  if (inputType === "url_domain") return "scam_early_warning";
  return MODES.find((m) => m.id === "crypto_investigation" && m.inputTypes.includes(inputType))?.id
    ?? MODES.find((m) => m.inputTypes.includes(inputType))?.id;
}

export default function W3SearchPanel({ form, onFormChange, onStart, isLoading }) {
  const value = form.value;
  const guesses = detectInput(value);
  const problem = value.trim() ? validateInput(form.inputType, value) : null;
  const valid = Boolean(value.trim()) && !problem;
  const methods = methodsFor(form);
  const chain = chainOfType(form.inputType);
  const chainInfo = CHAIN_INFO[chain === "url" ? "web" : chain];

  // Never leave the demo method (or one this input cannot use) selected.
  useEffect(() => {
    if (methods.length && !methods.some((m) => m.id === form.method)) onFormChange({ method: methods[0].id });
  }, [form.mode, form.inputType, form.method]); // eslint-disable-line react-hooks/exhaustive-deps

  function useType(inputType, newValue = value) {
    const mode = MODES.find((m) => m.id === form.mode)?.inputTypes.includes(inputType) && form.mode !== "scam_early_warning"
      ? form.mode : modeFor(inputType);
    if (mode && mode !== form.mode) onFormChange({ mode });
    onFormChange({ inputType, value: newValue, caseId: "" });
  }

  function setValue(newValue) {
    const g = detectInput(newValue);
    if (g.length && !g.some((x) => x.inputType === form.inputType)) useType(g[0].inputType, newValue);
    else onFormChange({ value: newValue, caseId: "" });
  }

  async function paste() {
    try {
      const text = (await navigator.clipboard.readText()).trim();
      if (text) setValue(text);
    } catch {
      // The browser can refuse clipboard access; typing still works.
    }
  }

  return (
    <form className="card controls w3-search-panel" aria-label="Investigation controls"
      onSubmit={(e) => { e.preventDefault(); if (valid && methods.length && !isLoading) onStart(); }}>
      <label className="field">
        <span className="field-label">What do you want to look up?</span>
        <span className={`w3-bigsearch ${valid ? "valid" : problem ? "invalid" : ""}`}>
          <input type="text" className="mono" value={value} spellCheck={false} autoComplete="off"
            placeholder="Paste an address, transaction or website" onChange={(e) => setValue(e.target.value)}
            aria-invalid={problem ? "true" : "false"} />
          {value ? (
            <button type="button" className="w3-field-btn" onClick={() => onFormChange({ value: "", caseId: "" })} title="Clear" aria-label="Clear">×</button>
          ) : (
            <button type="button" className="w3-field-btn" onClick={paste} title="Paste from the clipboard" aria-label="Paste">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><rect x="8" y="3" width="8" height="4" rx="1" /><path d="M8 5H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2" /></svg>
            </button>
          )}
        </span>
      </label>

      {/* What the value was recognised as. 0x... can be Ethereum or Base: pick with the chips. */}
      {value.trim() && (
        <div className="w3-detect" role="status">
          {!guesses.length && <span className="w3-hint w3-hint-none">Not a format we recognise</span>}
          {guesses.length === 1 && (
            <span className="w3-hint w3-hint-ok" style={{ "--chain": CHAIN_INFO[guesses[0].chain].color }}>
              <ChainLogo id={guesses[0].chain} size={16} />
              {guesses[0].chain === "web" ? "Website" : `${CHAIN_INFO[guesses[0].chain].label} ${guesses[0].what}`} recognised
            </span>
          )}
          {guesses.length > 1 && (
            <span className="w3-chips" role="radiogroup" aria-label="Which chain">
              {guesses.map((g) => (
                <button key={g.inputType} type="button" role="radio" aria-checked={form.inputType === g.inputType}
                  className={`w3-hint-chip ${form.inputType === g.inputType ? "on" : ""}`} style={{ "--chain": CHAIN_INFO[g.chain].color }}
                  onClick={() => useType(g.inputType)}>
                  <ChainLogo id={g.chain} size={14} /> {CHAIN_INFO[g.chain].label}
                </button>
              ))}
            </span>
          )}
          {problem && guesses.length > 0 && <span className="small error-text">{problem}</span>}
        </div>
      )}

      <div className="field">
        <span className="field-label">Analysis method</span>
        {methods.length ? (
          <div className="w3-methods" role="radiogroup" aria-label="Analysis method">
            {methods.map((m) => {
              const info = METHOD_INFO[m.id] ?? { name: m.label, source: "" };
              const source = typeof info.source === "string" ? info.source : info.source[chain] ?? "";
              const on = form.method === m.id;
              return (
                <button key={m.id} type="button" role="radio" aria-checked={on} className={`w3-method ${on ? "on" : ""}`}
                  onClick={() => onFormChange({ method: m.id })}>
                  <span className="w3-method-dot" aria-hidden="true" />
                  <span className="w3-method-text">
                    <strong>{info.name}</strong>
                    {source && <span className="small muted">{source}</span>}
                  </span>
                </button>
              );
            })}
          </div>
        ) : (
          <p className="small muted">Nothing can run for this input yet.</p>
        )}
      </div>

      <details className="w3-advanced">
        <summary>Advanced</summary>
        <label className="field">
          <span className="field-label">Investigation mode</span>
          <select value={form.mode} onChange={(e) => onFormChange({ mode: e.target.value })}>
            {MODES.map((m) => {
              const runnable = m.inputTypes.some((t) => methodsFor({ mode: m.id, inputType: t }).length);
              return <option key={m.id} value={m.id} disabled={!runnable}>{m.label}{runnable ? "" : " (coming later)"}</option>;
            })}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Input type</span>
          <select value={form.inputType} onChange={(e) => onFormChange({ inputType: e.target.value })}>
            {MODES.find((m) => m.id === form.mode).inputTypes.map((t) => <option key={t} value={t}>{INPUT_TYPES[t].label}</option>)}
          </select>
        </label>
      </details>

      <button className="primary w3-start" type="submit" disabled={isLoading || !valid || !methods.length}
        style={chainInfo ? { "--chain": chainInfo.color } : undefined}>
        {isLoading ? "Investigating..." : valid ? `Investigate ${startLabel(form.inputType)}` : problem ? "Check the input above" : "Paste something to investigate"}
      </button>
      {valid && !isLoading && <p className="small muted w3-enter">or press Enter</p>}
    </form>
  );
}
