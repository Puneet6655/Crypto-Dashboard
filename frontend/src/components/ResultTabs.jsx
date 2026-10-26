import { useState } from "react";

const TABS = ["Findings", "Why Flagged?", "Evidence", "Provenance", "Explanation", "Evidence Assessment"];

// Build a plain-language summary only from the findings and evidence we hold.
// Milestone 14 grows this into the AI Analyst; it never adds facts of its own.
function templateSummary(result) {
  const lines = result.findings.map((f) => `${f.title} [${f.evidence_ids.join(", ")}]`);
  return `This ${result.data_mode} case has ${result.findings.length} finding(s): ${lines.join("; ")}.`;
}

// Bottom area: six views of the same result.
export default function ResultTabs({ result, highlightedEvidence, onEvidenceClick }) {
  const [active, setActive] = useState("Findings");
  if (!result) return null;

  return (
    <section className="card tabs-card">
      <div className="tab-row" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={tab === active}
            className={tab === active ? "tab active" : "tab"}
            onClick={() => setActive(tab)}
          >
            {tab}
          </button>
        ))}
      </div>

      <div className="tab-body" role="tabpanel">
        {active === "Findings" && result.x402_status && (
          <dl className="grid-dl status-strip">
            <dt>Request</dt><dd>{result.x402_status.request}</dd>
            <dt>Verification</dt><dd>{result.x402_status.verification}</dd>
            <dt>Settlement</dt><dd>{result.x402_status.settlement}</dd>
            <dt>Delivery</dt><dd>{result.x402_status.delivery}</dd>
          </dl>
        )}

        {active === "Findings" && (
          <ul className="plain-list">
            {result.findings.map((f) => (
              <li key={f.id}>
                <strong>{f.title}</strong> <span className="badge">{f.kind}</span>
                <div className="small">
                  Evidence:{" "}
                  {f.evidence_ids.map((id) => (
                    <button key={id} type="button" className="link" onClick={() => { onEvidenceClick(id); setActive("Evidence"); }}>
                      {id}
                    </button>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        )}

        {active === "Why Flagged?" && (
          <ul className="plain-list">
            {result.indicators.map((i) => (
              <li key={i.id}>
                <strong>{i.rule}</strong>:{" "}
                {i.matched === true ? "matched" : i.matched === false ? "no match" : "not run"}
                <div className="small">{i.reason}</div>
              </li>
            ))}
          </ul>
        )}

        {active === "Evidence" && (
          <ul className="plain-list">
            {result.evidence.map((e) => (
              <li key={e.id} className={e.id === highlightedEvidence ? "highlight" : ""}>
                <strong>{e.id}</strong> {e.source}
                <div className="small mono">{e.source_url}</div>
                <blockquote>{e.excerpt}</blockquote>
              </li>
            ))}
          </ul>
        )}

        {active === "Provenance" && (
          <dl className="grid-dl">
            <dt>Investigation</dt><dd>{result.investigation_id}</dd>
            <dt>Data mode</dt><dd>{result.data_mode}</dd>
            <dt>Method</dt><dd>{result.method}</dd>
            <dt>Schema</dt><dd>{result.schema_version}</dd>
            {Object.entries(result.source_timestamps).map(([source, time]) => (
              <div key={source} className="contents">
                <dt>{source}</dt><dd>{time}</dd>
              </div>
            ))}
            <dt>Limitations</dt><dd>{result.limitations.join("; ")}</dd>
          </dl>
        )}

        {active === "Explanation" && (
          <p>
            {templateSummary(result)}
            <span className="small block">Template summary built only from the findings above.</span>
          </p>
        )}

        {active === "Evidence Assessment" && (
          <p className="muted">
            Not configured. The research team's legislative scorecard has not been supplied, so no legal
            criteria or scores are shown.
          </p>
        )}
      </div>
    </section>
  );
}
