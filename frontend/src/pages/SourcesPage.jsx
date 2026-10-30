import { useMemo, useState } from "react";
import { apiFetch } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { useApi } from "../hooks/useApi.js";
import PageHeader, { timeAgo } from "../components/PageHeader.jsx";
import Icon from "../components/Icon.jsx";
import { useToast } from "../components/Toasts.jsx";

const EMPTY = { name: "", category: "", url: "", used_for: "", licence: "", citation: "" };

// Acknowledgements: every data source, service and library the dashboard relies on.
// Everyone can read it; only admins can change it, and each change is written to the audit log.
export default function SourcesPage() {
  const { can } = useAuth();
  const toast = useToast();
  const { data, error, loading, reload } = useApi("/api/sources");
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState(null); // null | "new" | source id

  const isAdmin = can("admin");
  const editable = isAdmin && data?.editable;
  const sources = data?.sources ?? [];
  const categories = useMemo(
    () => [...new Set([...(data?.categories ?? []), ...sources.map((s) => s.category)])],
    [data, sources]
  );

  const visible = sources.filter((s) => {
    if (filter !== "all" && s.category !== filter) return false;
    const text = search.trim().toLowerCase();
    return !text || [s.name, s.used_for, s.licence, s.citation].some((v) => v?.toLowerCase().includes(text));
  });
  const groups = categories
    .map((category) => ({ category, items: visible.filter((s) => s.category === category) }))
    .filter((g) => g.items.length);

  async function save(source, fields) {
    if (source === "new") await apiFetch("/api/sources", { method: "POST", body: fields });
    else await apiFetch(`/api/sources/${source}`, { method: "PUT", body: fields });
    toast(source === "new" ? `Added "${fields.name}"` : `Saved "${fields.name}"`);
    setEditing(null);
    reload();
  }

  async function remove(source) {
    if (!window.confirm(`Remove "${source.name}" from the sources list?`)) return;
    try {
      await apiFetch(`/api/sources/${source.id}`, { method: "DELETE" });
      toast(`Removed "${source.name}"`);
      reload();
    } catch (err) {
      toast(err.message, { tone: "error" });
    }
  }

  async function move(source, direction) {
    try {
      await apiFetch(`/api/sources/${source.id}/move`, { method: "POST", body: { direction } });
      reload();
    } catch (err) {
      toast(err.message, { tone: "error" });
    }
  }

  async function copyCitations() {
    const text = sources
      .map((s) => `${s.name}. ${s.citation || ""} ${s.url}`.replace(/\s+/g, " ").trim())
      .join("\n");
    try {
      await navigator.clipboard.writeText(text);
      toast(`Copied ${sources.length} citations`);
    } catch {
      toast("Copying is blocked in this browser.", { tone: "error" });
    }
  }

  return (
    <>
      <PageHeader icon="sources" title="Sources" subtitle="The data, services and software this dashboard relies on, with credit and licence notes.">
        <button type="button" className="secondary" onClick={copyCitations} disabled={!sources.length}>
          <Icon name="copy" size={16} /> Copy citations
        </button>
        {editable && (
          <button type="button" className="primary" onClick={() => setEditing("new")}>
            <Icon name="plus" size={16} /> Add source
          </button>
        )}
      </PageHeader>

      {!isAdmin && (
        <p className="notice info small"><Icon name="lock" size={14} /> Only admins can change this list.</p>
      )}
      {isAdmin && data && !data.editable && (
        <p className="notice warn small">The database is not connected, so this built-in list cannot be edited.</p>
      )}

      <div className="toolbar">
        <div className="chips" role="group" aria-label="Filter by category">
          {["all", ...categories].map((c) => (
            <button key={c} type="button" className={`chip ${filter === c ? "chip-on" : ""}`} onClick={() => setFilter(c)}>
              {c === "all" ? `All (${sources.length})` : c}
            </button>
          ))}
        </div>
        <label className="search-box">
          <Icon name="search" size={16} />
          <input type="search" placeholder="Search sources" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search sources" />
        </label>
      </div>

      {editing === "new" && (
        <SourceForm initial={{ ...EMPTY, category: filter !== "all" ? filter : categories[0] ?? "" }} categories={categories}
          onSave={(fields) => save("new", fields)} onCancel={() => setEditing(null)} />
      )}

      {error && <p className="notice error">{error}</p>}
      {loading && !data && <p className="muted">Loading sources...</p>}

      {groups.map((group) => (
        <section key={group.category} className="source-group">
          <h2 className="group-title">{group.category}</h2>
          <div className="source-grid">
            {group.items.map((s) =>
              editing === s.id ? (
                <SourceForm key={s.id} initial={s} categories={categories}
                  onSave={(fields) => save(s.id, fields)} onCancel={() => setEditing(null)} />
              ) : (
                <article key={s.id} className="card source-card">
                  <div className="source-head">
                    <h3>
                      {s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.name} <Icon name="external" size={13} /></a> : s.name}
                    </h3>
                    {editable && (
                      <div className="row-actions">
                        <button type="button" className="ghost-icon" title="Move up" aria-label="Move up" onClick={() => move(s, "up")}><Icon name="up" size={15} /></button>
                        <button type="button" className="ghost-icon" title="Move down" aria-label="Move down" onClick={() => move(s, "down")}><Icon name="down" size={15} /></button>
                        <button type="button" className="ghost-icon" title="Edit" aria-label="Edit" onClick={() => setEditing(s.id)}><Icon name="edit" size={15} /></button>
                        <button type="button" className="ghost-icon danger" title="Remove" aria-label="Remove" onClick={() => remove(s)}><Icon name="trash" size={15} /></button>
                      </div>
                    )}
                  </div>
                  {s.used_for && <p>{s.used_for}</p>}
                  {s.licence && <p className="small"><span className="muted">Licence: </span>{s.licence}</p>}
                  {s.citation && <p className="small citation">{s.citation}</p>}
                  {s.updated_by_name && (
                    <p className="small muted">Edited by {s.updated_by_name}, {timeAgo(s.updated_at)}</p>
                  )}
                </article>
              )
            )}
          </div>
        </section>
      ))}
      {data && !groups.length && <p className="muted">No sources match.</p>}
    </>
  );
}

function SourceForm({ initial, categories, onSave, onCancel }) {
  const [fields, setFields] = useState({ ...EMPTY, ...initial });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (key) => (event) => setFields((old) => ({ ...old, [key]: event.target.value }));

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const { name, category, url, used_for, licence, citation } = fields;
      await onSave({ name, category, url, used_for, licence, citation });
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <form className="card source-card editing form-grid" onSubmit={submit}>
      <label className="field">
        <span className="field-label">Name</span>
        <input value={fields.name} onChange={set("name")} required maxLength={160} />
      </label>
      <label className="field">
        <span className="field-label">Category</span>
        <input value={fields.category} onChange={set("category")} list="source-categories" required maxLength={80} />
        <datalist id="source-categories">{categories.map((c) => <option key={c} value={c} />)}</datalist>
      </label>
      <label className="field">
        <span className="field-label">Link</span>
        <input type="url" value={fields.url} onChange={set("url")} placeholder="https://" maxLength={500} />
      </label>
      <label className="field">
        <span className="field-label">What we use it for</span>
        <textarea rows={2} value={fields.used_for} onChange={set("used_for")} maxLength={1000} />
      </label>
      <label className="field">
        <span className="field-label">Licence or terms</span>
        <input value={fields.licence} onChange={set("licence")} maxLength={500} />
      </label>
      <label className="field">
        <span className="field-label">Citation</span>
        <textarea rows={2} value={fields.citation} onChange={set("citation")} maxLength={1000} />
      </label>
      {error && <p className="notice error small">{error}</p>}
      <div className="form-actions">
        <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
        <button type="submit" className="primary" disabled={busy}>{busy ? "Saving..." : "Save"}</button>
      </div>
    </form>
  );
}
