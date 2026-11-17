import { apiFetch } from "../api/client.js";

// The live model score that now comes with every Bitcoin address lookup. The lookup itself is sent with
// model_score=false so the graph shows at once, and the score is asked for at the same time with
// POST /api/investigations/model-score (4 to 30 s: the server reads the address's history first).

export const MODEL_FAMILIES = [
  { id: "xgboost", label: "XGBoost" },
  { id: "random_forest", label: "Random Forest" },
];
export const MODEL_METHODS = new Set(MODEL_FAMILIES.map((f) => f.id));

// Mainnet Bitcoin address nodes look like bitcoin:mainnet:address:<address>.
export function bitcoinAddressOf(nodeId) {
  const match = /^bitcoin:mainnet:address:(.+)$/.exec(nodeId ?? "");
  return match ? match[1] : null;
}

// Same spelling the backend uses (bech32 addresses are case-insensitive and stored lower case).
export function normalizeBitcoin(value) {
  const text = (value ?? "").trim();
  return /^bc1/i.test(text) ? text.toLowerCase() : text;
}

export const scoreKey = (family, address) => `${family}|${address}`;

export function requestModelScore(address, family, { signal } = {}) {
  return apiFetch("/api/investigations/model-score", {
    method: "POST",
    body: { address, family },
    signal,
    timeoutMs: 120000, // first call after a restart loads the model and SHAP explainer
  });
}

const MODEL_FINDING = "fm1";
const MODEL_EVIDENCE = "em1";

// Put a score into an investigation result as its "explanation" plus one finding and one evidence item, so
// the Findings, Why Flagged? and Evidence tabs show it exactly like a result from a model method.
export function withModelScore(result, score) {
  const address = score.address ?? result.input?.value;
  const entityId = `bitcoin:mainnet:address:${address}`;
  const verdict = score.flagged ? "at or above" : "below";
  const recent = score.recent_2026_threshold;
  const recentText = recent
    ? `; ${score.score >= recent ? "at or above" : "below"} the 2026 alert threshold ${recent.toFixed(3)}` : "";
  const finding = {
    id: MODEL_FINDING,
    kind: "model_output",
    entity_ids: [entityId],
    evidence_ids: [MODEL_EVIDENCE],
    title: `${score.model?.name ?? "Model"}: live score ${score.score.toFixed(3)}, ${verdict} its threshold ${score.threshold.toFixed(2)}${recentText} (see Why Flagged?)`,
  };
  const live = score.live ?? {};
  const evidence = {
    id: MODEL_EVIDENCE,
    source: `Model registry: ${score.model?.id ?? "model"} (live features, SHAP)`,
    source_url: "backend/models/registry.json",
    retrieved_at: score.registry_generated_utc ?? new Date().toISOString(),
    method_version: score.model?.version ?? "",
    excerpt: live.transactions_in_window != null
      ? `${live.transactions_in_window} transactions in the window ending at block ${live.anchor_block}`
      : "Live features from the address history",
  };
  return {
    ...result,
    explanation: score,
    findings: [...result.findings.filter((f) => f.id !== MODEL_FINDING), finding],
    evidence: [...result.evidence.filter((e) => e.id !== MODEL_EVIDENCE), evidence],
    limitations: [
      ...result.limitations.filter((l) => !l.includes("requested separately") && l !== score.note),
      ...(score.note ? [score.note] : []),
    ],
  };
}
