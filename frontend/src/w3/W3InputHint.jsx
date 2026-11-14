import { INPUT_TYPES, MODES } from "../config/modes.js";
import { CHAIN_INFO, detectInput } from "./chains.js";
import { ChainLogo } from "./ChainBadge.jsx";

// Under the Investigate text box: lights up the chain the typed value belongs to, and offers a one-click
// switch when the value does not match the selected input type (e.g. an 0x... address with "Bitcoin address" picked).
export default function W3InputHint({ form, onFormChange }) {
  const { value, inputType } = form;
  if (!value.trim()) return null;
  const allowed = MODES.find((m) => m.id === form.mode)?.inputTypes ?? [];
  const all = detectInput(value);
  const here = all.filter((g) => allowed.includes(g.inputType));

  if (!all.length) return <div className="w3-hint w3-hint-none" role="status">Not a format we recognise yet</div>;

  const current = here.find((g) => g.inputType === inputType);
  if (current) {
    const info = CHAIN_INFO[current.chain];
    return (
      <div className="w3-hint w3-hint-ok" style={{ "--chain": info.color }} role="status">
        <ChainLogo id={current.chain} size={16} />
        <span>{current.chain === "web" ? "Website" : `${info.label} ${current.what}`} recognised</span>
      </div>
    );
  }

  // Use a guess: switch input type, and the mode too when this mode does not take it (keeping the typed value).
  function use(guess) {
    if (!allowed.includes(guess.inputType)) {
      const mode = MODES.find((m) => m.inputTypes.includes(guess.inputType));
      if (!mode) return;
      onFormChange({ mode: mode.id });
    }
    onFormChange({ inputType: guess.inputType, value });
  }

  return (
    <div className="w3-hint w3-hint-switch" role="status">
      <span>This looks like</span>
      {all.map((g) => (
        <button key={g.inputType} type="button" className="w3-hint-chip" style={{ "--chain": CHAIN_INFO[g.chain].color }}
          onClick={() => use(g)} title={`Switch to ${INPUT_TYPES[g.inputType].label}`}>
          <ChainLogo id={g.chain} size={14} /> {INPUT_TYPES[g.inputType].label}
        </button>
      ))}
    </div>
  );
}
