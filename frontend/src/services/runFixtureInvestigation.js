import { FIXTURE_CASES } from "../data/fixtures.js";

// Format checks that match backend/app/validation.py. The backend checks again;
// this copy only gives faster feedback in the browser.
const PATTERNS = {
  bitcoin_address: /^([13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[qp][02-9ac-hj-np-z]{38,58})$/,
  bitcoin_txid: /^[0-9a-fA-F]{64}$/,
  url_domain: /^(https?:\/\/)?([a-z0-9-]+\.)+[a-z]{2,}(\/.*)?$/i,
  x402_payment_id: /^[A-Za-z0-9._:-]{4,128}$/,
};

export function validateInput(inputType, value) {
  const trimmed = value.trim();
  if (!trimmed) return "Enter a value or pick a preloaded case.";
  // bech32 addresses may be typed in capitals, so test those in lower case.
  const candidate = trimmed.toLowerCase().startsWith("bc1") ? trimmed.toLowerCase() : trimmed;
  if (!PATTERNS[inputType].test(candidate)) return "That does not match the selected input type.";
  return null;
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Pretend to run an investigation using the synthetic fixtures.
// Returns a result object, returns null when no demo data exists (empty state),
// or throws an Error (error state). Milestone 3 replaces this with a real API call.
export async function runFixtureInvestigation({ mode, inputType, value, caseId }) {
  await wait(600); // simulate network time so the loading state is visible

  const match = FIXTURE_CASES.find(
    (c) => c.caseId === caseId || (c.mode === mode && c.input.type === inputType && c.input.value === value.trim())
  );
  if (!match) return null;
  if (match.simulateError) throw new Error(match.simulateError);
  return match.result;
}
