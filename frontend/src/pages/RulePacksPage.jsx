import { useState } from "react";
import { apiFetch } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { useApi } from "../hooks/useApi.js";
import PageHeader from "../components/PageHeader.jsx";
import Icon from "../components/Icon.jsx";
import { useToast } from "../components/Toasts.jsx";
import { SEVERITY_LABEL } from "../services/redFlags.js";

// Rule packs: red-flag heuristics published by authorities (FATF, FinCEN, AUSTRAC, OFAC, ...), written as YAML files
// (backend/rule_packs). Everyone can read them and check a draft pack; admins switch packs on or off, set their
// weights and upload new packs. Each change is written to the audit log.
const STARTER = `id: my_authority_topic_2026
title: Short name shown in the dashboard
authority: Name of the authority
jurisdiction: International
document:
  title: Full title of the publication
  url: https://example.gov/the-publication
licence: Terms of the source document, and how this pack uses it
chains: [bitcoin]
rules:
  - id: example_rule
    title: Money passes straight through
    citation: section 2, paragraph 4
    severity: medium
    weight: 2
    when: pass_through_ratio >= 0.9 and median_hours_to_spend <= 24
    explain: "Sent on {pass_through_ratio:.0%} of what it received after {median_hours_to_spend:.1f} hours."
`;
const STARTER_FACTS = `{"pass_through_ratio": 0.97, "median_hours_to_spend": 3}`;

export default function RulePacksPage() {
  const { can } = useAuth();
  const toast = useToast();
  const isAdmin = can("admin");
  const { data, error, loading, reload } = useApi("/api/red-flags/packs");
  const facts = useApi("/api/red-flags/facts");
  const [editorOpen, setEditorOpen] = useState(false);
  const [showFacts, setShowFacts] = useState(false);

  async function change(pack, fields) {
    try {
      await apiFetch(`/api/red-flags/packs/${pack.id}`, { method: "PATCH", body: fields });
      toast(`Saved "${pack.title}"`);
      reload();
    } catch (err) {
      toast(err.message, { tone: "error" });
    }
  }

  async function remove(pack) {
    if (!window.confirm(`Remove the uploaded pack "${pack.title}"?`)) return;
    try {
      await apiFetch(`/api/red-flags/packs/${pack.id}`, { method: "DELETE" });
      toast(`Removed "${pack.title}"`);
      reload();
    } catch (err) {
      toast(err.message, { tone: "error" });
    }
  }

  async function reloadFolders() {
    try {
      const body = await apiFetch("/api/red-flags/reload", { method: "POST" });
      toast(`${body.packs.length} packs loaded${body.errors.length ? `, ${body.errors.length} failed` : ""}`);
      reload();
    } catch (err) {
      toast(err.message, { tone: "error" });
    }
  }

  const packs = data?.packs ?? [];
  return (
    <>
      <PageHeader icon="shield" title="Rule packs"
        subtitle="Red-flag indicators published by authorities, checked against every Bitcoin address lookup.">
        <button type="button" className="secondary" onClick={() => setShowFacts((v) => !v)}>
          {showFacts ? "Hide facts" : "Facts rules can use"}
        </button>
        <button type="button" className="primary" onClick={() => setEditorOpen((v) => !v)}>
          <Icon name="plus" size={16} /> {isAdmin ? "Add or test a pack" : "Test a pack"}
        </button>
        {isAdmin && (
          <button type="button" className="secondary" onClick={reloadFolders}><Icon name="refresh" size={16} /> Reload</button>
        )}
      </PageHeader>

      <p className="notice info small">
        A red flag is a reason to look closer, not proof of wrongdoing, and no red flags is not proof that an address is
        safe. Red flags are shown next to the model score and never mixed into it. Rules marked "not checked" need data
        the dashboard does not have yet.
      </p>
      {!isAdmin && <p className="notice info small"><Icon name="lock" size={14} /> Only admins can switch packs on or off or add packs.</p>}
      {error && <p className="notice error">{error}</p>}
      {loading && !data && <p className="muted">Loading rule packs...</p>}
      {data?.errors?.map((e) => (
        <p key={e.file} className="notice warn small">Could not load {e.file} ({e.origin}): {e.error}</p>
      ))}

      {showFacts && <FactsTable facts={facts.data ?? []} />}
      {editorOpen && <PackEditor isAdmin={isAdmin} onSaved={() => { reload(); setEditorOpen(false); }} />}

      <div className="rp-grid">
        {packs.map((pack) => (
          <PackCard key={pack.id} pack={pack} isAdmin={isAdmin} onChange={change} onRemove={remove} />
        ))}
      </div>
    </>
  );
}

function PackCard({ pack, isAdmin, onChange, onRemove }) {
  const [showRules, setShowRules] = useState(false);
  const [yaml, setYaml] = useState(null);
  const [weight, setWeight] = useState(pack.setting.weight);

  async function toggleYaml() {
    if (yaml !== null) { setYaml(null); return; }
    try {
      setYaml((await apiFetch(`/api/red-flags/packs/${pack.id}/source`)).yaml);
    } catch (err) {
      setYaml(`Could not load: ${err.message}`);
    }
  }

  return (
    <article className="card rp-card">
      <div className="rp-card-head">
        <div>
          <h3>{pack.title}</h3>
          <div className="small muted">{pack.authority} · {pack.jurisdiction}</div>
        </div>
        <span className={`chip ${pack.setting.enabled ? "chip-on" : ""}`}>{pack.setting.enabled ? "On" : "Off"}</span>
      </div>
      <a className="small" href={pack.document.url} target="_blank" rel="noopener noreferrer">
        {pack.document.title}{pack.document.reference ? ` (${pack.document.reference})` : ""}
        {pack.document.published ? `, ${pack.document.published}` : ""} <Icon name="external" size={12} />
      </a>
      {pack.summary && <p className="small">{pack.summary}</p>}
      <p className="small muted">Licence: {pack.licence}</p>
      <div className="small muted">{pack.rules.length} rules · {pack.origin === "builtin" ? "built in" : pack.origin} · version {pack.version}</div>

      {isAdmin && (
        <div className="rp-controls small">
          <label><input type="checkbox" checked={pack.setting.enabled} onChange={(e) => onChange(pack, { enabled: e.target.checked })} /> Use this pack</label>
          <label>Weight <input type="number" min="0" max="10" step="0.5" value={weight}
            onChange={(e) => setWeight(e.target.value)}
            onBlur={() => Number(weight) !== pack.setting.weight && onChange(pack, { weight: Number(weight) })} /></label>
        </div>
      )}

      <div className="rp-controls small">
        <button type="button" className="link" onClick={() => setShowRules((v) => !v)} aria-expanded={showRules}>
          {showRules ? "Hide rules" : "Show rules"}
        </button>
        <button type="button" className="link" onClick={toggleYaml} aria-expanded={yaml !== null}>
          {yaml === null ? "View YAML" : "Hide YAML"}
        </button>
        {isAdmin && pack.origin === "uploaded" && (
          <button type="button" className="link" onClick={() => onRemove(pack)}>Remove</button>
        )}
      </div>
      {showRules && (
        <ul className="rp-rules small">
          {pack.rules.map((r) => (
            <li key={r.id}>
              <strong>{r.title}</strong> ({SEVERITY_LABEL[r.severity]}, weight {r.weight}) · {r.citation}
              <div className="muted"><code>{r.when}</code></div>
            </li>
          ))}
        </ul>
      )}
      {yaml !== null && <pre className="rp-yaml">{yaml}</pre>}
    </article>
  );
}

function PackEditor({ isAdmin, onSaved }) {
  const toast = useToast();
  const [text, setText] = useState(STARTER);
  const [factsText, setFactsText] = useState(STARTER_FACTS);
  const [answer, setAnswer] = useState(null);
  const [busy, setBusy] = useState(false);

  async function check() {
    let facts = null;
    if (factsText.trim()) {
      try {
        facts = JSON.parse(factsText);
      } catch {
        setAnswer({ valid: false, errors: ["The example facts are not valid JSON, e.g. {\"tx_count\": 40}"] });
        return;
      }
    }
    setBusy(true);
    try {
      setAnswer(await apiFetch("/api/red-flags/packs/validate", { method: "POST", body: { yaml: text, facts } }));
    } catch (err) {
      setAnswer({ valid: false, errors: [err.message] });
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    try {
      const pack = await apiFetch("/api/red-flags/packs", { method: "POST", body: { yaml: text } });
      toast(`Added "${pack.title}"`);
      onSaved();
    } catch (err) {
      setAnswer({ valid: false, errors: err.message.split("; ") });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card rp-editor" aria-label="Add or test a rule pack">
      <h2>{isAdmin ? "Add or test a pack" : "Test a pack"}</h2>
      <p className="small muted">
        Paste a pack in YAML. Conditions use fact names (see "Facts rules can use"), numbers, + - * /, comparisons,
        and/or/not and min/max/abs. Add example facts to see what each rule would say.
      </p>
      <label className="small">Pack (YAML)
        <textarea value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} aria-label="Pack YAML" />
      </label>
      <label className="small">Example facts (JSON, optional)
        <textarea value={factsText} onChange={(e) => setFactsText(e.target.value)} spellCheck={false} style={{ minHeight: 60 }} aria-label="Example facts" />
      </label>
      <div className="rp-controls">
        <button type="button" className="secondary" onClick={check} disabled={busy}>Check pack</button>
        {isAdmin && <button type="button" className="primary" onClick={save} disabled={busy}>Save pack</button>}
      </div>
      {answer && !answer.valid && (
        <div className="notice error small">
          <strong>Not valid yet:</strong>
          <ul>{answer.errors.map((e) => <li key={e}>{e}</li>)}</ul>
        </div>
      )}
      {answer?.valid && (
        <div className="notice info small">
          <strong>Valid: {answer.title} ({answer.rules} rule{answer.rules === 1 ? "" : "s"}).</strong>
          {answer.unknown_facts?.length > 0 && <div>Ignored, not fact names: {answer.unknown_facts.join(", ")}</div>}
          {answer.trial_results && (
            <ul>
              {answer.trial_results.map((r) => (
                <li key={r.key}><strong>{r.status === "matched" ? "Red flag" : r.status === "clear" ? "Clear" : "Not checked"}:</strong> {r.title}. {r.reason}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

function FactsTable({ facts }) {
  return (
    <section className="card" aria-label="Facts">
      <h2>Facts rules can use</h2>
      <p className="small muted">Worked out for each address from its newest four weeks of activity (at most 100 transactions) and the downloaded lists.</p>
      <table className="rp-facts">
        <thead><tr><th>Fact</th><th>Unit</th><th>Meaning</th></tr></thead>
        <tbody>
          {facts.map((f) => (
            <tr key={f.name}><td><code>{f.name}</code></td><td>{f.unit}</td><td>{f.description}</td></tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
