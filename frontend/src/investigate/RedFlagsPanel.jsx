import { useState } from "react";
import { SEVERITY_LABEL } from "../services/redFlags.js";

// Investigate, right column under the model score: what the authority rule packs say about the looked-up Bitcoin
// address. Each red flag shows the numbers behind it and the authority and paragraph it comes from. Rules that could
// not run are listed as "not checked", never as clear.
export default function RedFlagsPanel({ inv }) {
  const address = inv.subjectAddress;
  const entry = inv.flagsOf(address);
  const [showAll, setShowAll] = useState(false);
  if (!address) return null;

  const report = entry?.status === "done" ? entry.data : null;
  const matched = report?.results.filter((r) => r.status === "matched") ?? [];
  const notChecked = report?.results.filter((r) => r.status === "not_checked") ?? [];
  const clear = report?.results.filter((r) => r.status === "clear") ?? [];
  const tone = !report ? "idle" : !matched.length ? "low" : ["high", "critical"].includes(report.summary.highest_severity) ? "high" : "mid";

  return (
    <section className={`card inv-risk rf-panel inv-tone-${tone}`} aria-label="Authority red flags" aria-busy={entry?.status === "loading"}>
      <div className="inv-risk-head">
        <h2>Authority red flags</h2>
        <a className="small" href="#/rule-packs">Rule packs</a>
      </div>

      {!entry && (
        <button type="button" className="secondary compact" onClick={() => inv.checkFlags(address)}>Check red flags</button>
      )}
      {entry?.status === "loading" && <p className="small muted" role="status">Checking FATF, FinCEN, AUSTRAC and OFAC indicators...</p>}
      {entry?.status === "error" && (
        <div>
          <p className="small error-text">{entry.error}</p>
          <button type="button" className="secondary compact" onClick={() => inv.checkFlags(address, { force: true })}>Try again</button>
        </div>
      )}

      {report && (
        <>
          <div className="rf-summary">
            <strong>
              {matched.length ? `${matched.length} red flag${matched.length === 1 ? "" : "s"}` : "No red flags"}
            </strong>
            <span className="small muted">
              {report.summary.rules} rules from {report.packs.length} pack{report.packs.length === 1 ? "" : "s"}
              {notChecked.length ? ` · ${notChecked.length} not checked` : ""}
            </span>
          </div>
          {matched.length > 0 && (
            <ul className="rf-list">
              {matched.map((r) => <Flag key={r.key} r={r} />)}
            </ul>
          )}
          <button type="button" className="link small" onClick={() => setShowAll((v) => !v)} aria-expanded={showAll}>
            {showAll ? "Hide the other rules" : `Show the other rules (${clear.length} clear, ${notChecked.length} not checked)`}
          </button>
          {showAll && (
            <ul className="rf-list rf-quiet">
              {[...notChecked, ...clear].map((r) => <Flag key={r.key} r={r} />)}
            </ul>
          )}
          <p className="small muted rf-note">{report.disclaimer}</p>
        </>
      )}
    </section>
  );
}

function Flag({ r }) {
  const [open, setOpen] = useState(false);
  const state = r.status === "matched" ? SEVERITY_LABEL[r.severity] : r.status === "clear" ? "Clear" : "Not checked";
  return (
    <li className={`rf-item rf-${r.status} rf-sev-${r.severity}`}>
      <button type="button" className="rf-item-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="rf-badge">{state}</span>
        <span className="rf-title">{r.title}</span>
      </button>
      <div className="small muted rf-source">
        {r.authority} · <a href={r.document_url} target="_blank" rel="noopener noreferrer">{r.citation}</a>
      </div>
      {(open || r.status === "matched") && <p className="small rf-reason">{r.reason}</p>}
      {open && (
        <div className="small muted rf-more">
          <div>Rule: <code>{r.condition}</code></div>
          {r.advice && <div>Next step: {r.advice}</div>}
          <div>Pack: {r.pack_title} · weight {r.weight}</div>
        </div>
      )}
    </li>
  );
}
