// Milestone 15: evidence export. Turns one investigation result, as the dashboard holds it, into a JSON file
// (machine-readable, keeps every field) and a Markdown report (for a case file or a supervisor). The live model
// score and the authority red flags are already merged into the result (withModelScore, withRedFlags), so they
// travel with it. The export adds no facts of its own: everything in it comes from the result.

export const EXPORT_FORMAT = "blockchain-intelligence-evidence-export";
export const EXPORT_VERSION = "1.0";
const GENERATOR = "Blockchain Intelligence Dashboard";

const list = (value) => (Array.isArray(value) ? value : []);

// The JSON export: the full result plus a small header and summary. The graph is kept in full so the
// result can be loaded and checked again later.
export function buildEvidenceJson(result, { exportedAt = new Date().toISOString() } = {}) {
  const findings = list(result.findings);
  const report = result.red_flags ?? null;
  const matched = list(report?.results).filter((r) => r.status === "matched");
  return {
    export_format: EXPORT_FORMAT,
    export_version: EXPORT_VERSION,
    exported_at: exportedAt,
    generator: GENERATOR,
    investigation: {
      id: result.investigation_id,
      created_at: result.created_at ?? null,
      schema_version: result.schema_version ?? null,
      mode: result.mode ?? null,
      method: result.method ?? null,
      data_mode: result.data_mode ?? null,
      input: result.input ?? null,
    },
    summary: {
      findings: findings.length,
      evidence_items: list(result.evidence).length,
      indicators_matched: list(result.indicators).filter((i) => i.matched === true).length,
      model_score: modelSummary(result.explanation),
      authority_red_flags: report
        ? {
            matched: matched.length,
            not_checked: list(report.results).filter((r) => r.status === "not_checked").length,
            rules: report.summary?.rules ?? list(report.results).length,
            highest_severity: report.summary?.highest_severity ?? null,
          }
        : null,
      graph: graphSummary(result.graph),
    },
    findings,
    indicators: list(result.indicators),
    evidence: list(result.evidence),
    model_score: result.explanation ?? null,
    authority_red_flags: report,
    x402_status: result.x402_status ?? null,
    source_timestamps: result.source_timestamps ?? {},
    limitations: list(result.limitations),
    uncertainty: result.uncertainty ?? {},
    evidence_assessment: result.scorecard?.configured
      ? result.scorecard
      : { configured: false, note: "The research team's legislative scorecard has not been supplied." },
    graph: result.graph ?? null,
  };
}

function modelSummary(score) {
  if (!score || typeof score.score !== "number") return null;
  return {
    model: score.model?.name ?? score.model?.id ?? null,
    model_version: score.model?.version ?? null,
    score: score.score,
    threshold: score.threshold ?? null,
    flagged: score.flagged ?? null,
    recent_2026_threshold: score.recent_2026_threshold ?? null,
    benchmark: score.benchmark ?? null,
  };
}

function graphSummary(graph) {
  if (!graph) return null;
  return {
    nodes: list(graph.nodes).length,
    edges: list(graph.edges).length,
    truncated: Boolean(graph.truncated),
    max_nodes: graph.limits?.max_nodes ?? null,
  };
}

// ---- Markdown ----

// Table cells: keep on one line and escape the pipe.
const cell = (value) => String(value ?? "").replace(/\r?\n/g, " ").replace(/\|/g, "\\|");
// Inline text: stop stray Markdown characters from changing the layout.
// Underscores inside words (snake_case ids) do not start emphasis, so they are left alone.
const text = (value) => String(value ?? "").replace(/([\\`*[\]<>])/g, "\\$1").replace(/(^|\s)_/g, "$1\\_");
const code = (value) => {
  const s = String(value ?? "");
  return s.includes("`") ? `\`\` ${s} \`\`` : `\`${s}\``;
};
const quote = (value) => String(value ?? "").split(/\r?\n/).map((line) => `> ${line}`).join("\n");
const num = (value, digits) => (typeof value === "number" ? value.toFixed(digits) : "n/a");
const safeUrl = (url) => (/^https?:\/\//i.test(url ?? "") ? url : null);

export function buildEvidenceMarkdown(result, { exportedAt = new Date().toISOString() } = {}) {
  const data = buildEvidenceJson(result, { exportedAt });
  const nodes = new Map(list(result.graph?.nodes).map((n) => [n.id, n]));
  const label = (id) => nodes.get(id)?.label ?? id;
  const out = [];
  const push = (...lines) => out.push(...lines);

  const inv = data.investigation;
  const subject = inv.input?.value ? `${inv.input.value}` : inv.id;
  push(`# Evidence export: ${text(subject)}`, "");
  push(`Exported ${exportedAt} from the ${GENERATOR}. Everything below comes from the investigation result as the dashboard held it at export time; nothing has been added.`, "");

  push("| Field | Value |", "| --- | --- |");
  push(`| Investigation | ${cell(inv.id)} |`);
  if (inv.created_at) push(`| Created | ${cell(inv.created_at)} |`);
  if (inv.input) push(`| Input | ${cell(inv.input.type)}: ${cell(inv.input.value)} |`);
  if (inv.mode) push(`| Mode | ${cell(inv.mode)} |`);
  push(`| Method | ${cell(inv.method)} |`);
  push(`| Data mode | ${cell(inv.data_mode)} |`);
  if (inv.schema_version) push(`| Result schema | ${cell(inv.schema_version)} |`);
  push(`| Export format | ${EXPORT_FORMAT} ${EXPORT_VERSION} |`, "");

  // Summary
  const s = data.summary;
  push("## Summary", "");
  push(`- ${s.findings} finding(s), ${s.evidence_items} evidence item(s), ${s.indicators_matched} indicator(s) matched.`);
  if (s.model_score) {
    const m = s.model_score;
    push(`- Model score: ${text(m.model ?? "model")} ${num(m.score, 3)}, ${m.flagged ? "at or above" : "below"} its threshold ${num(m.threshold, 2)}.`);
  } else {
    push("- Model score: not part of this result.");
  }
  if (s.authority_red_flags) {
    const r = s.authority_red_flags;
    push(`- Authority red flags: ${r.matched} matched of ${r.rules} rule(s)${r.not_checked ? `, ${r.not_checked} not checked` : ""}${r.matched && r.highest_severity ? `, highest severity ${r.highest_severity}` : ""}.`);
  } else {
    push("- Authority red flags: not part of this result.");
  }
  if (s.graph) push(`- Graph: ${s.graph.nodes} node(s), ${s.graph.edges} edge(s)${s.graph.truncated ? `, truncated at ${s.graph.max_nodes} nodes` : ""}.`);
  push("");

  if (data.x402_status) {
    push("## x402 payment status", "");
    for (const [key, value] of Object.entries(data.x402_status)) push(`- ${text(key)}: ${text(value)}`);
    push("");
  }

  // Findings
  push("## Findings", "");
  if (!data.findings.length) push("No findings.");
  data.findings.forEach((f, index) => {
    push(`${index + 1}. **${text(f.title)}** (${text(f.kind)})`);
    if (list(f.evidence_ids).length) push(`   - Evidence: ${f.evidence_ids.map(code).join(", ")}`);
    if (list(f.entity_ids).length) push(`   - Entities: ${f.entity_ids.map((id) => (label(id) === id ? code(id) : `${text(label(id))} (${code(id)})`)).join(", ")}`);
  });
  push("");

  // Model score
  const score = data.model_score;
  if (score && typeof score.score === "number") {
    push("## Model score", "");
    push(`**${text(score.model?.name ?? "Model")}**${score.model?.version ? ` (version ${text(score.model.version)})` : ""}: score ${num(score.score, 3)}, ${score.flagged ? "at or above" : "below"} its threshold ${num(score.threshold, 2)}.`);
    if (typeof score.recent_2026_threshold === "number") {
      push("", `Against the 2026 alert threshold ${num(score.recent_2026_threshold, 3)}: ${score.score >= score.recent_2026_threshold ? "at or above" : "below"}.`);
    }
    if (score.benchmark) push("", `Basis: ${score.benchmark === "live" ? "live score on current features, model trained on 2016-17 labels" : "offline benchmark"}.`);
    if (score.note) push("", text(score.note));
    const shap = score.explanation;
    if (shap && list(shap.features).length) {
      push("", `Feature contributions (${text(shap.method ?? "SHAP")}, in ${text(shap.units ?? "model units")}; positive pushes towards illicit):`, "");
      push("| Feature | Value | Contribution |", "| --- | --- | --- |");
      for (const f of shap.features) {
        push(`| ${cell(f.feature)} | ${cell(f.value ?? "missing")} | ${num(f.contribution, 4)} |`);
      }
      if (shap.other_features?.count > 0) push(`| ${shap.other_features.count} other features | | ${num(shap.other_features.contribution, 4)} |`);
      if (typeof shap.base_value === "number") push("", `Average training score ${num(shap.base_value, 3)} + contributions = ${num(shap.output, 3)}.`);
    }
    push("");
  }

  // Authority red flags
  const report = data.authority_red_flags;
  if (report) {
    const results = list(report.results);
    const matched = results.filter((r) => r.status === "matched");
    const notChecked = results.filter((r) => r.status === "not_checked");
    const clear = results.filter((r) => r.status === "clear");
    push("## Authority red flags", "");
    push(`${matched.length} matched, ${clear.length} clear and ${notChecked.length} not checked, from ${list(report.packs).length} rule pack(s). These are shown beside the model score and never blended into it.`, "");
    for (const r of matched) {
      const url = safeUrl(r.document_url);
      push(`### ${text(r.title)} (${text(r.severity ?? "")})`, "");
      push(`- Source: ${text(r.authority)}, ${url ? `[${text(r.citation)}](${url})` : text(r.citation)}`);
      if (r.pack_title) push(`- Pack: ${text(r.pack_title)}${r.weight != null ? `, weight ${r.weight}` : ""}`);
      if (r.reason) push(`- Why: ${text(r.reason)}`);
      if (r.condition) push(`- Rule: ${code(r.condition)}`);
      if (r.advice) push(`- Next step: ${text(r.advice)}`);
      push("");
    }
    if (notChecked.length) {
      push("Not checked (could not run, so not counted as clear):", "");
      for (const r of notChecked) push(`- ${text(r.title)} (${text(r.authority)}${r.reason ? `: ${text(r.reason)}` : ""})`);
      push("");
    }
    if (clear.length) {
      push("Clear:", "");
      for (const r of clear) push(`- ${text(r.title)} (${text(r.authority)})`);
      push("");
    }
    if (report.disclaimer) push(`_${text(report.disclaimer)}_`, "");
  }

  // Indicators
  if (data.indicators.length) {
    push("## Indicators", "");
    push("| Rule | Result | Reason |", "| --- | --- | --- |");
    for (const i of data.indicators) {
      const state = i.matched === true ? "matched" : i.matched === false ? "no match" : "not run";
      push(`| ${cell(i.rule)} | ${state} | ${cell(i.reason)} |`);
    }
    push("");
  }

  // Evidence
  push("## Evidence", "");
  if (!data.evidence.length) push("No evidence items.");
  for (const e of data.evidence) {
    push(`### ${text(e.id)}: ${text(e.source)}`, "");
    if (e.source_url) push(`- Source: ${code(e.source_url)}`);
    push(`- Retrieved: ${text(e.retrieved_at)}`);
    if (e.method_version) push(`- Method version: ${text(e.method_version)}`);
    if (e.excerpt) push("", quote(e.excerpt));
    push("");
  }

  // Provenance and limitations
  push("## Provenance", "");
  const stamps = Object.entries(data.source_timestamps);
  if (stamps.length) for (const [source, time] of stamps) push(`- ${text(source)}: ${text(time)}`);
  else push("- No source timestamps recorded.");
  const uncertainty = Object.entries(data.uncertainty);
  if (uncertainty.length) push(`- Uncertainty: ${uncertainty.map(([k, v]) => `${text(k)} = ${text(v)}`).join("; ")}`);
  push("");

  push("## Limitations", "");
  if (data.limitations.length) for (const l of data.limitations) push(`- ${text(l)}`);
  else push("- None recorded.");
  push("");

  push("## Evidence assessment", "");
  push(data.evidence_assessment.configured
    ? "Scorecard configured; see the JSON export for its contents."
    : "Not configured. The research team's legislative scorecard has not been supplied, so no legal criteria or scores are given.");
  push("");

  return out.join("\n");
}

// evidence-<investigation>-<UTC time>.<ext>, safe on Windows.
export function exportFileName(result, ext, exportedAt = new Date().toISOString()) {
  const id = String(result?.investigation_id ?? "investigation").replace(/[^A-Za-z0-9_-]+/g, "-").slice(0, 60);
  const stamp = exportedAt.replace(/[-:]/g, "").replace(/\.\d+/, "").slice(0, 15);
  return `evidence-${id}-${stamp}Z.${ext}`;
}

// Hand the browser a file to save.
export function downloadEvidence(result, format) {
  const exportedAt = new Date().toISOString();
  const isJson = format === "json";
  const body = isJson
    ? JSON.stringify(buildEvidenceJson(result, { exportedAt }), null, 2)
    : buildEvidenceMarkdown(result, { exportedAt });
  const blob = new Blob([body], { type: isJson ? "application/json" : "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = exportFileName(result, isJson ? "json" : "md", exportedAt);
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
