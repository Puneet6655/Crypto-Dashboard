import { useState } from "react";
import { apiFetch } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { useApi } from "../hooks/useApi.js";
import PageHeader from "../components/PageHeader.jsx";
import Icon from "../components/Icon.jsx";

const isoDay = (date) => date.toISOString().slice(0, 10);

// Loading the latest Bitcoin transactions from Google BigQuery into the backend.
// Under development: admins can estimate the cost (a free dry run); running it waits until
// we decide where the data will be stored.
export default function BigQueryPage() {
  const { can } = useAuth();
  const isAdmin = can("admin");
  const status = useApi("/api/bigquery/status");
  const today = new Date();
  const weekAgo = new Date(today.getTime() - 6 * 86400000);
  const [form, setForm] = useState({ start: isoDay(weekAgo), end: isoDay(today), withAddresses: false });
  const [estimate, setEstimate] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function runEstimate(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      setEstimate(await apiFetch("/api/bigquery/estimate", {
        method: "POST",
        body: { start_date: form.start, end_date: form.end, include_inputs_outputs: form.withAddresses },
        timeoutMs: 60000,
      }));
    } catch (err) {
      setError(err.message);
      setEstimate(null);
    } finally {
      setBusy(false);
    }
  }

  const s = status.data;
  const freeShare = estimate ? Math.min(1, estimate.share_of_free_tier) : 0;

  return (
    <>
      <PageHeader icon="bigquery" title="BigQuery refresh" subtitle="Bring the latest Bitcoin transactions from Google's public dataset into the backend.">
        <span className="pill pill-dev"><Icon name="construction" size={14} /> Under development</span>
      </PageHeader>

      <div className="notice warn">
        <strong>Not live yet.</strong> Where the loaded data will be hosted has not been decided, so this page can only
        estimate what a load would cost. Nothing is downloaded or stored, and estimates are free.
      </div>

      <section className="card pipeline" aria-label="How a refresh will work">
        <Step icon="bigquery" title="Public dataset" text={s?.dataset ?? "bigquery-public-data.crypto_bitcoin"} done />
        <Arrow />
        <Step icon="search" title="Dry run" text="Estimate bytes scanned (free)" done />
        <Arrow />
        <Step icon="shield" title="Capped run" text="maximum_bytes_billed set" />
        <Arrow />
        <Step icon="database" title="Storage" text="To be decided" pending />
        <Arrow />
        <Step icon="investigate" title="Dashboard" text="Lookups and training use it" />
      </section>

      <div className="grid-2">
        <section className="card">
          <h2>Set-up</h2>
          {status.error && <p className="error-text small">{status.error}</p>}
          {s && (
            <ul className="checklist">
              <Check ok={s.library_installed} text="google-cloud-bigquery installed on the API server" />
              <Check ok={s.key_found} text="Service-account key found (backend/data or BQ_KEY_PATH)" />
              {isAdmin && s.project_id && <Check ok text={`Billing project: ${s.project_id}`} />}
              <Check ok={false} pending text="Destination for the loaded data chosen" />
            </ul>
          )}
          <h3 className="subhead">Next steps</h3>
          <ol className="steps small">{(s?.next_steps ?? []).map((step) => <li key={step}>{step}</li>)}</ol>
        </section>

        <form className="card form-grid" onSubmit={runEstimate}>
          <h2>Estimate a load</h2>
          {!isAdmin && <p className="notice info small"><Icon name="lock" size={14} /> Only admins can run BigQuery estimates.</p>}
          <div className="field-row">
            <label className="field">
              <span className="field-label">From</span>
              <input type="date" value={form.start} max={form.end} onChange={(e) => setForm({ ...form, start: e.target.value })} disabled={!isAdmin} />
            </label>
            <label className="field">
              <span className="field-label">To</span>
              <input type="date" value={form.end} min={form.start} max={isoDay(today)} onChange={(e) => setForm({ ...form, end: e.target.value })} disabled={!isAdmin} />
            </label>
          </div>
          <label className="check-row">
            <input type="checkbox" checked={form.withAddresses} onChange={(e) => setForm({ ...form, withAddresses: e.target.checked })} disabled={!isAdmin} />
            Include inputs and outputs (addresses and amounts). Much larger.
          </label>
          <div className="form-actions">
            <button type="submit" className="primary" disabled={!isAdmin || busy}>
              {busy ? "Asking BigQuery..." : "Estimate cost (free)"}
            </button>
            <button type="button" className="secondary" disabled title="Under development: choose where the data will be stored first">
              <Icon name="play" size={14} /> Run and load
            </button>
          </div>
          {error && <p className="notice error small">{error}</p>}
          {estimate && (
            <div className="estimate">
              <div className="estimate-number">
                <strong>{estimate.gb_processed.toLocaleString()} GB</strong>
                <span className="muted small">scanned for {estimate.days} day{estimate.days === 1 ? "" : "s"}</span>
              </div>
              <div className="meter" aria-label="Share of the monthly free tier">
                <span style={{ width: `${Math.max(1, freeShare * 100)}%` }} className={freeShare > 0.5 ? "high" : ""} />
              </div>
              <p className="small muted">
                {(estimate.share_of_free_tier * 100).toFixed(1)}% of the {s?.free_tier_gb_per_month ?? 1000} GB free each month.
                Dry runs are not billed.
              </p>
              <details>
                <summary className="small">Show the query</summary>
                <pre className="code">{estimate.sql}</pre>
              </details>
            </div>
          )}
        </form>
      </div>
    </>
  );
}

function Step({ icon, title, text, done, pending }) {
  return (
    <div className={`pipe-step ${done ? "done" : ""} ${pending ? "pending" : ""}`}>
      <span className="pipe-icon"><Icon name={icon} size={18} /></span>
      <strong>{title}</strong>
      <span className="small muted">{text}</span>
    </div>
  );
}

const Arrow = () => <span className="pipe-arrow" aria-hidden="true">→</span>;

function Check({ ok, pending, text }) {
  return (
    <li className={ok ? "ok" : pending ? "pending" : "bad"}>
      <Icon name={ok ? "check" : pending ? "clock" : "alert"} size={15} /> {text}
    </li>
  );
}
