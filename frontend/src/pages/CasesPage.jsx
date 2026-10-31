import { useEffect, useState } from "react";
import { apiFetch } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { useCases } from "../cases/CasesContext.jsx";
import { ITEM_TYPES, caseItemAttrs, explorerName, explorerUrl, liveInputType, shortValue } from "../cases/caseItems.js";
import { navigate } from "../hooks/useHashRoute.js";
import PageHeader, { formatDate, timeAgo } from "../components/PageHeader.jsx";
import Icon from "../components/Icon.jsx";
import { useToast } from "../components/Toasts.jsx";

const PRIORITIES = ["high", "medium", "low"];

export default function CasesPage({ caseId, onInvestigate }) {
  return caseId ? <CaseDetail caseId={caseId} onInvestigate={onInvestigate} /> : <CaseList />;
}

// ---- the list ------------------------------------------------------------------------------

function CaseList() {
  const { cases, loading, error, canEdit, refresh } = useCases();
  const { config } = useAuth();
  const [filter, setFilter] = useState("open");
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const counts = { open: 0, closed: 0, all: cases.length };
  cases.forEach((c) => counts[c.status]++);
  const text = search.trim().toLowerCase();
  const visible = cases.filter(
    (c) =>
      (filter === "all" || c.status === filter) &&
      (!text || c.title.toLowerCase().includes(text) || c.description.toLowerCase().includes(text) ||
        c.items.some((i) => i.value.toLowerCase().includes(text)))
  );

  return (
    <>
      <PageHeader icon="cases" title="Cases" subtitle="Group the addresses and transactions you want to follow up. Add them from anywhere: right-click, or use “Add to case”.">
        {canEdit && (
          <button type="button" className="primary" onClick={() => setCreating(true)}>
            <Icon name="plus" size={16} /> New case
          </button>
        )}
      </PageHeader>

      {!config?.database && <p className="notice warn">Cases need the database. Set DATABASE_URL in backend/.env.</p>}
      {!canEdit && <p className="notice info small"><Icon name="lock" size={14} /> You can read cases. Analysts and admins can create and change them.</p>}
      {creating && <NewCaseForm onDone={() => setCreating(false)} />}

      <div className="toolbar">
        <div className="segmented small" role="group" aria-label="Show">
          {["open", "closed", "all"].map((f) => (
            <button key={f} type="button" className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>
              {f[0].toUpperCase() + f.slice(1)} ({counts[f]})
            </button>
          ))}
        </div>
        <label className="search-box">
          <Icon name="search" size={16} />
          <input type="search" placeholder="Search titles, notes, addresses" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search cases" />
        </label>
      </div>

      {error && <p className="notice error">{error}</p>}
      {loading && !cases.length && <p className="muted">Loading cases...</p>}

      {!loading && !cases.length && config?.database && (
        <div className="empty card">
          <Icon name="cases" size={40} />
          <h2>No cases yet</h2>
          <p className="muted">
            Run an investigation, then right-click an address or transaction (in the graph, the details panel or the
            findings) and choose a case, or press “New case”.
          </p>
        </div>
      )}

      <div className="case-grid">
        {visible.map((c) => {
          const addresses = c.items.filter((i) => i.item_type === "address").length;
          const txs = c.items.filter((i) => i.item_type === "transaction").length;
          return (
            <a key={c.id} href={`#/cases/${c.id}`} className={`card case-card ${c.status}`}>
              <div className="case-card-head">
                <span className={`priority priority-${c.priority}`}>{c.priority}</span>
                {c.status === "closed" && <span className="pill pill-muted">Closed</span>}
                <span className="small muted grow-right">#{c.id}</span>
              </div>
              <h3>{c.title}</h3>
              {c.description && <p className="small muted clamp">{c.description}</p>}
              <div className="case-card-foot small">
                <span title="Wallet addresses"><Icon name="wallet" size={14} /> {addresses}</span>
                <span title="Transactions"><Icon name="transaction" size={14} /> {txs}</span>
                {c.item_count - addresses - txs > 0 && <span title="Other items"><Icon name="tag" size={14} /> {c.item_count - addresses - txs}</span>}
                <span className="muted grow-right">updated {timeAgo(c.updated_at)}</span>
              </div>
            </a>
          );
        })}
      </div>
    </>
  );
}

function NewCaseForm({ onDone }) {
  const { createCase } = useCases();
  const toast = useToast();
  const [fields, setFields] = useState({ title: "", description: "", priority: "medium" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    try {
      const created = await createCase(fields);
      toast(`Created "${created.title}"`);
      onDone();
      navigate(`/cases/${created.id}`);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <form className="card form-grid new-case" onSubmit={submit}>
      <h2>New case</h2>
      <label className="field">
        <span className="field-label">Title</span>
        <input value={fields.title} onChange={(e) => setFields({ ...fields, title: e.target.value })} required maxLength={120} autoFocus
          placeholder="e.g. Ransomware payments to exchange X" />
      </label>
      <label className="field">
        <span className="field-label">Description</span>
        <textarea rows={3} value={fields.description} onChange={(e) => setFields({ ...fields, description: e.target.value })} maxLength={4000}
          placeholder="What is this case about, and what should someone check?" />
      </label>
      <PriorityPicker value={fields.priority} onChange={(priority) => setFields({ ...fields, priority })} />
      {error && <p className="notice error small">{error}</p>}
      <div className="form-actions">
        <button type="button" className="secondary" onClick={onDone}>Cancel</button>
        <button type="submit" className="primary" disabled={busy}>{busy ? "Creating..." : "Create case"}</button>
      </div>
    </form>
  );
}

function PriorityPicker({ value, onChange, disabled }) {
  return (
    <div className="field">
      <span className="field-label">Priority</span>
      <div className="segmented small">
        {PRIORITIES.map((p) => (
          <button key={p} type="button" disabled={disabled} className={value === p ? `active priority-${p}` : ""} onClick={() => onChange(p)}>
            {p[0].toUpperCase() + p.slice(1)}
          </button>
        ))}
      </div>
    </div>
  );
}

// ---- one case ------------------------------------------------------------------------------

function CaseDetail({ caseId, onInvestigate }) {
  const { can } = useAuth();
  const { refresh: refreshList } = useCases();
  const toast = useToast();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [editingText, setEditingText] = useState(false);
  const canEdit = can("analyst");

  async function load() {
    try {
      setData(await apiFetch(`/api/cases/${caseId}`));
      setError("");
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    setData(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);

  async function update(changes, message) {
    try {
      setData(await apiFetch(`/api/cases/${caseId}`, { method: "PATCH", body: changes }));
      refreshList();
      if (message) toast(message);
    } catch (err) {
      toast(err.message, { tone: "error" });
    }
  }

  async function removeCase() {
    if (!window.confirm(`Delete case "${data.title}" and its ${data.item_count} item(s)? This cannot be undone.`)) return;
    try {
      await apiFetch(`/api/cases/${caseId}`, { method: "DELETE" });
      toast(`Deleted "${data.title}"`);
      refreshList();
      navigate("/cases");
    } catch (err) {
      toast(err.message, { tone: "error" });
    }
  }

  async function removeItem(item) {
    try {
      await apiFetch(`/api/cases/${caseId}/items/${item.id}`, { method: "DELETE" });
      toast(`Removed ${shortValue(item.value)} from the case`);
      load();
      refreshList();
    } catch (err) {
      toast(err.message, { tone: "error" });
    }
  }

  async function saveNote(item, note) {
    await apiFetch(`/api/cases/${caseId}/items/${item.id}`, { method: "PATCH", body: { note } });
    load();
  }

  function exportCsv() {
    const header = ["type", "value", "network", "label", "note", "added_by", "added_at", "source"];
    const quote = (v) => `"${String(v ?? "").replaceAll('"', '""')}"`;
    const rows = data.items.map((i) => [i.item_type, i.value, i.network, i.label, i.note, i.added_by_name, i.added_at, i.source].map(quote).join(","));
    const blob = new Blob([[header.join(","), ...rows].join("\n")], { type: "text/csv" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `case-${data.id}-${data.title.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  if (error) {
    return (
      <>
        <a href="#/cases" className="back-link">← All cases</a>
        <p className="notice error">{error}</p>
      </>
    );
  }
  if (!data) return <p className="muted">Loading case...</p>;

  return (
    <>
      <a href="#/cases" className="back-link">← All cases</a>
      <header className="case-header card">
        {editingText ? (
          <CaseTextForm data={data} onCancel={() => setEditingText(false)}
            onSave={async (changes) => { await update(changes, "Saved"); setEditingText(false); }} />
        ) : (
          <>
            <div className="case-header-top">
              <span className={`priority priority-${data.priority}`}>{data.priority}</span>
              <span className={`pill ${data.status === "open" ? "pill-up" : "pill-muted"}`}>{data.status === "open" ? "Open" : "Closed"}</span>
              <span className="small muted">#{data.id} · opened by {data.created_by_name ?? "someone"} {timeAgo(data.created_at)}</span>
            </div>
            <h1>{data.title}</h1>
            {data.description ? <p className="pre-wrap">{data.description}</p> : <p className="muted small">No description.</p>}
          </>
        )}
        <div className="case-actions">
          {canEdit && !editingText && (
            <>
              <button type="button" className="secondary" onClick={() => setEditingText(true)}><Icon name="edit" size={15} /> Edit</button>
              <select value={data.priority} onChange={(e) => update({ priority: e.target.value }, "Priority changed")} aria-label="Priority" className="auto">
                {PRIORITIES.map((p) => <option key={p} value={p}>{p[0].toUpperCase() + p.slice(1)} priority</option>)}
              </select>
              <button type="button" className="secondary" onClick={() => update({ status: data.status === "open" ? "closed" : "open" },
                data.status === "open" ? "Case closed" : "Case reopened")}>
                <Icon name={data.status === "open" ? "check" : "refresh"} size={15} /> {data.status === "open" ? "Close case" : "Reopen"}
              </button>
            </>
          )}
          <button type="button" className="secondary" onClick={exportCsv} disabled={!data.items.length}><Icon name="download" size={15} /> CSV</button>
          {can("admin") && (
            <button type="button" className="secondary danger" onClick={removeCase}><Icon name="trash" size={15} /> Delete</button>
          )}
        </div>
      </header>

      <section className="card">
        <div className="section-head">
          <h2>Items ({data.items.length})</h2>
        </div>
        {canEdit && <AddItemForm caseId={caseId} onAdded={() => { load(); refreshList(); }} />}
        {!data.items.length ? (
          <p className="muted small">Nothing in this case yet. Add an address or transaction above, or right-click one on the Investigate page.</p>
        ) : (
          <div className="table-wrap">
            <table className="table items-table">
              <thead>
                <tr><th>Type</th><th>Identifier</th><th>Note</th><th>Added</th><th aria-label="Actions" /></tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <ItemRow key={item.id} item={item} canEdit={canEdit} onInvestigate={onInvestigate}
                    onRemove={() => removeItem(item)} onSaveNote={(note) => saveNote(item, note)} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function CaseTextForm({ data, onSave, onCancel }) {
  const [title, setTitle] = useState(data.title);
  const [description, setDescription] = useState(data.description);
  return (
    <form className="form-grid" onSubmit={(e) => { e.preventDefault(); onSave({ title, description }); }}>
      <label className="field">
        <span className="field-label">Title</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={120} autoFocus />
      </label>
      <label className="field">
        <span className="field-label">Description</span>
        <textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={4000} />
      </label>
      <div className="form-actions">
        <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
        <button type="submit" className="primary">Save</button>
      </div>
    </form>
  );
}

function ItemRow({ item, canEdit, onInvestigate, onRemove, onSaveNote }) {
  const type = ITEM_TYPES[item.item_type] ?? ITEM_TYPES.other;
  const [note, setNote] = useState(item.note);
  const [saving, setSaving] = useState(false);
  const link = explorerUrl(item);
  const lookUpType = liveInputType(item);
  const canLookUp = lookUpType !== null;

  async function commitNote() {
    if (note === item.note) return;
    setSaving(true);
    try {
      await onSaveNote(note);
    } finally {
      setSaving(false);
    }
  }

  return (
    <tr>
      <td><span className="type-chip"><Icon name={type.icon} size={14} /> {type.short}</span></td>
      <td>
        <span className="mono small" title={item.value} {...caseItemAttrs(item)}>{shortValue(item.value, 14)}</span>
        {item.label && item.label !== item.value && <div className="small muted">{item.label}</div>}
      </td>
      <td className="note-cell">
        {canEdit ? (
          <input className={`note-input ${saving ? "saving" : ""}`} value={note} placeholder="Add a note"
            onChange={(e) => setNote(e.target.value)} onBlur={commitNote}
            onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()} maxLength={2000} aria-label="Note" />
        ) : (
          <span className="small">{item.note || <span className="muted">—</span>}</span>
        )}
      </td>
      <td className="small muted" title={formatDate(item.added_at, true)}>
        {item.added_by_name ?? "someone"}, {timeAgo(item.added_at)}
      </td>
      <td>
        <div className="row-actions">
          {canLookUp && (
            <button type="button" className="ghost-icon" title="Investigate live" aria-label="Investigate live"
              onClick={() => onInvestigate(lookUpType, item.value)}>
              <Icon name="investigate" size={16} />
            </button>
          )}
          {link && (
            <a className="ghost-icon" href={link} target="_blank" rel="noreferrer" title={`Open on ${explorerName(item)}`} aria-label={`Open on ${explorerName(item)}`}>
              <Icon name="external" size={16} />
            </a>
          )}
          <CopyButton value={item.value} />
          {canEdit && (
            <button type="button" className="ghost-icon danger" title="Remove from case" aria-label="Remove from case" onClick={onRemove}>
              <Icon name="trash" size={16} />
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

function CopyButton({ value }) {
  const [copied, setCopied] = useState(false);
  return (
    <button type="button" className="ghost-icon" title={copied ? "Copied" : "Copy"} aria-label="Copy"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        } catch {
          // Clipboard can be blocked; the value is still selectable.
        }
      }}>
      <Icon name={copied ? "check" : "copy"} size={16} />
    </button>
  );
}

// Guess the type from what was typed: 64 hex characters (or 0x + 64, or a Solana signature) is a
// transaction, anything else an address.
const SOLANA_SIGNATURE = /^[1-9A-HJ-NP-Za-km-z]{80,90}$/;
const SOLANA_ADDRESS = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const BITCOIN_ADDRESS = /^([13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[a-z0-9]{8,87})$/i;

function guessType(value) {
  const v = value.trim();
  if (/^(0x)?[0-9a-fA-F]{64}$/.test(v) || SOLANA_SIGNATURE.test(v)) return "transaction";
  if (/^(https?:\/\/)?([a-z0-9-]+\.)+[a-z]{2,}(\/.*)?$/i.test(v) && !/^(bc1|[13])/i.test(v)) return "domain";
  return "address";
}

// Which chain a typed identifier belongs to, so the case can look it up live and watch it.
function guessNetwork(type, v) {
  if (type === "domain") return "web";
  if (type !== "address" && type !== "transaction") return "other";
  if (/^0x[0-9a-fA-F]+$/.test(v)) return "ethereum";
  if (BITCOIN_ADDRESS.test(v) || /^[0-9a-fA-F]{64}$/.test(v)) return "bitcoin";
  if (SOLANA_SIGNATURE.test(v) || SOLANA_ADDRESS.test(v)) return "solana";
  return "bitcoin";
}

function AddItemForm({ caseId, onAdded }) {
  const toast = useToast();
  const [value, setValue] = useState("");
  const [type, setType] = useState("auto");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const resolved = type === "auto" ? guessType(value) : type;

  async function submit(event) {
    event.preventDefault();
    if (!value.trim()) return;
    setBusy(true);
    setError("");
    try {
      const { created } = await apiFetch(`/api/cases/${caseId}/items`, {
        method: "POST",
        body: { item_type: resolved, value: value.trim(), note, network: guessNetwork(resolved, value.trim()) },
      });
      toast(created ? "Added to the case" : "That is already in the case", { tone: created ? "success" : "info" });
      setValue("");
      setNote("");
      onAdded();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="add-item" onSubmit={submit}>
      <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Item type" className="auto">
        <option value="auto">Auto ({ITEM_TYPES[guessType(value)].short.toLowerCase()})</option>
        {Object.entries(ITEM_TYPES).map(([id, t]) => <option key={id} value={id}>{t.label}</option>)}
      </select>
      <input className="grow mono" value={value} onChange={(e) => setValue(e.target.value)} placeholder="Paste an address or transaction ID" aria-label="Address or transaction ID" maxLength={300} />
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" aria-label="Note" maxLength={2000} />
      <button type="submit" className="primary compact" disabled={busy || !value.trim()}><Icon name="plus" size={14} /> Add</button>
      {error && <p className="notice error small full">{error}</p>}
    </form>
  );
}
