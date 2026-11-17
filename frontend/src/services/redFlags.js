import { apiFetch } from "../api/client.js";

// Authority red flags (FATF, FinCEN, AUSTRAC, OFAC rule packs; backend/app/redflags). Asked for at the same time as
// every Bitcoin address lookup, like the model score, and shown in their own panel next to it. They are never mixed
// into the model score.

export function requestRedFlags(address, { signal } = {}) {
  return apiFetch("/api/red-flags/bitcoin", { method: "POST", body: { address }, signal, timeoutMs: 90000 });
}

export const SEVERITY_LABEL = { info: "Info", low: "Low", medium: "Medium", high: "High", critical: "Critical" };

// Put the red flags into an investigation result: their findings, indicators and evidence (ids start with "rf")
// join the Findings, Evidence and indicator lists, so exports and the Explanation tab can cite them.
export function withRedFlags(result, report) {
  const notRf = (item) => !String(item.id).startsWith("rf");
  return {
    ...result,
    red_flags: report,
    findings: [...result.findings.filter(notRf), ...report.findings],
    indicators: [...(result.indicators ?? []).filter(notRf), ...report.indicators],
    evidence: [...result.evidence.filter(notRf), ...report.evidence],
    limitations: [...result.limitations.filter((l) => !report.limitations.includes(l)), ...report.limitations],
  };
}
