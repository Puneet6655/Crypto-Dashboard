// Run with: npm test (node --test, no extra packages).
import { test } from "node:test";
import assert from "node:assert/strict";
import { FIXTURE_CASES } from "../data/fixtures.js";
import { buildEvidenceJson, buildEvidenceMarkdown, exportFileName, EXPORT_FORMAT } from "./evidenceExport.js";

const AT = "2026-11-17T06:30:21.123Z";
const ADDRESS = "bc1qexampleaddress0000000000000000000000";
const NODE = `bitcoin:mainnet:address:${ADDRESS}`;

// A live-style Bitcoin result with the model score and red flags merged in, the way
// withModelScore and withRedFlags leave it.
function scoredResult() {
  return {
    schema_version: "1.0",
    investigation_id: "inv-123",
    created_at: "2026-11-17T06:00:00Z",
    input: { type: "bitcoin_address", value: ADDRESS },
    mode: "crypto_investigation",
    method: "address_lookup",
    data_mode: "live",
    findings: [
      { id: "f1", title: "Received 12 payments | 3 senders", kind: "observation", entity_ids: [NODE], evidence_ids: ["e1"] },
      { id: "fm1", title: "XGBoost: live score 0.950, at or above its threshold 0.91", kind: "model_output", entity_ids: [NODE], evidence_ids: ["em1"] },
      { id: "rf1", title: "Rapid movement after deposit", kind: "rule_match", entity_ids: [NODE], evidence_ids: ["rfe1"] },
    ],
    indicators: [{ id: "rfi1", rule: "fatf_rapid_movement", matched: true, reason: "3 of 4 deposits left within 2 blocks" }],
    graph: { nodes: [{ id: NODE, type: "address", label: "bc1qexam...0000" }], edges: [], truncated: true, limits: { max_nodes: 200 } },
    evidence: [
      { id: "e1", source: "mempool.space", source_url: "https://mempool.space/api/address/x", retrieved_at: "2026-11-17T06:00:00Z", method_version: "v1", excerpt: "line one\nline two" },
      { id: "em1", source: "Model registry: xgb", source_url: "backend/models/registry.json", retrieved_at: "2026-11-02T00:00:00Z", method_version: "3", excerpt: "40 transactions in the window" },
      { id: "rfe1", source: "FATF red flags 2020", source_url: "https://www.fatf-gafi.org/", retrieved_at: "2026-11-17T06:00:00Z", method_version: "pack 1", excerpt: "rapid movement" },
    ],
    source_timestamps: { "mempool.space": "2026-11-17T06:00:00Z" },
    limitations: ["Only the latest 100 transactions were read."],
    uncertainty: { truncated: true },
    scorecard: { configured: false },
    explanation: {
      model: { id: "xgb", name: "XGBoost", version: "3" },
      score: 0.95, threshold: 0.914, flagged: true, benchmark: "live", recent_2026_threshold: 0.97,
      note: "Trained on 2016-17 labels.",
      explanation: { method: "TreeSHAP", units: "log-odds", base_value: -2.1, output: 2.9, probability: 0.95,
        features: [{ feature: "in_degree", value: 12, contribution: 1.2345, pushes: "towards illicit" }],
        other_features: { count: 40, contribution: 0.5 } },
    },
    red_flags: {
      packs: [{ id: "fatf" }],
      summary: { rules: 3, highest_severity: "high" },
      disclaimer: "Indicators, not proof.",
      results: [
        { key: "a", status: "matched", severity: "high", title: "Rapid movement after deposit", authority: "FATF", citation: "para 12", document_url: "https://www.fatf-gafi.org/x", reason: "3 of 4 deposits", condition: "fast_out_ratio >= 0.75", advice: "Check the receiving side", pack_title: "FATF VA red flags", weight: 2 },
        { key: "b", status: "not_checked", severity: "medium", title: "Near reporting threshold", authority: "FinCEN", citation: "s 3", document_url: "javascript:alert(1)", reason: "No price feed" },
        { key: "c", status: "clear", severity: "low", title: "OFAC listed", authority: "OFAC", citation: "SDN", document_url: "https://ofac.treasury.gov/" },
      ],
    },
  };
}

test("JSON export keeps the whole result and summarises score and red flags", () => {
  const data = buildEvidenceJson(scoredResult(), { exportedAt: AT });
  assert.equal(data.export_format, EXPORT_FORMAT);
  assert.equal(data.exported_at, AT);
  assert.equal(data.investigation.id, "inv-123");
  assert.equal(data.findings.length, 3);
  assert.equal(data.evidence.length, 3);
  assert.deepEqual(data.summary.model_score.score, 0.95);
  assert.equal(data.summary.model_score.flagged, true);
  assert.deepEqual(data.summary.authority_red_flags, { matched: 1, not_checked: 1, rules: 3, highest_severity: "high" });
  assert.equal(data.summary.graph.truncated, true);
  assert.equal(data.evidence_assessment.configured, false);
  assert.ok(JSON.parse(JSON.stringify(data)));
});

test("Markdown export has every section and cites the red-flag sources", () => {
  const md = buildEvidenceMarkdown(scoredResult(), { exportedAt: AT });
  for (const heading of ["## Summary", "## Findings", "## Model score", "## Authority red flags", "## Indicators", "## Evidence", "## Provenance", "## Limitations", "## Evidence assessment"]) {
    assert.ok(md.includes(heading), heading);
  }
  assert.ok(md.includes("XGBoost** (version 3): score 0.950, at or above its threshold 0.91"));
  assert.ok(md.includes("[para 12](https://www.fatf-gafi.org/x)"));
  assert.ok(!md.includes("javascript:"), "unsafe link dropped");
  assert.ok(md.includes("> line one\n> line two"), "multi-line excerpt stays quoted");
  assert.ok(md.includes("12 payments \\| 3 senders") || md.includes("12 payments | 3 senders"));
  assert.ok(md.includes("| in_degree | 12 | 1.2345 |"));
  assert.ok(md.includes("1 matched, 1 clear and 1 not checked"));
});

test("Markdown table cells escape pipes", () => {
  const r = scoredResult();
  r.indicators[0].reason = "a | b";
  assert.ok(buildEvidenceMarkdown(r, { exportedAt: AT }).includes("| a \\| b |"));
});

test("demo fixtures without score, red flags or created_at still export", () => {
  for (const c of FIXTURE_CASES.filter((f) => f.result)) {
    const data = buildEvidenceJson(c.result, { exportedAt: AT });
    assert.equal(data.summary.model_score, null);
    assert.equal(data.authority_red_flags, null);
    const md = buildEvidenceMarkdown(c.result, { exportedAt: AT });
    assert.ok(md.includes("Model score: not part of this result."));
    assert.ok(md.startsWith("# Evidence export:"));
  }
});

test("file names are safe and stamped in UTC", () => {
  assert.equal(exportFileName({ investigation_id: "inv/1:a b" }, "md", AT), "evidence-inv-1-a-b-20261117T063021Z.md");
});
