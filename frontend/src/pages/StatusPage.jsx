import { useEffect, useState } from "react";
import { apiFetch } from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import PageHeader, { formatDate, timeAgo } from "../components/PageHeader.jsx";
import Icon from "../components/Icon.jsx";

const AUTO_REFRESH_SECONDS = 60;
const PROVIDER_NAMES = { mempool: "mempool.space", blockstream: "blockstream.info", etherscan: "etherscan.io", alchemy: "alchemy.com" };

// Is everything the dashboard depends on working right now?
export default function StatusPage() {
  const api = useApi("/api/health");
  const db = useApi("/api/status/database");
  const providers = useApi("/api/bitcoin/providers?check=true");
  const ethProviders = useApi("/api/ethereum/providers?check=true");
  const baseProviders = useApi("/api/base/providers?check=true");
  const history = useApi("/api/status/provider-history?limit=48");
  const lists = useApi("/api/indicators/status");
  const bigquery = useApi("/api/bigquery/status");
  const [auto, setAuto] = useState(false);

  async function checkAll() {
    // Saves one provider health check, which the history strip below shows.
    await apiFetch("/api/bitcoin/health").catch(() => null);
    await Promise.all([api.reload(), db.reload(), providers.reload(), ethProviders.reload(), baseProviders.reload(), lists.reload(), bigquery.reload()]);
    history.reload();
  }

  useEffect(() => {
    if (!auto) return;
    const timer = setInterval(checkAll, AUTO_REFRESH_SECONDS * 1000);
    return () => clearInterval(timer);
  });

  const overall = overallState({ api, db, providers, lists });
  const busy = [api, db, providers, lists].some((x) => x.loading);

  return (
    <>
      <PageHeader icon="status" title="Status" subtitle="Live lookup services, the database and the sanction lists.">
        <label className="switch">
          <input type="checkbox" checked={auto} onChange={(event) => setAuto(event.target.checked)} />
          <span>Auto-check every {AUTO_REFRESH_SECONDS} s</span>
        </label>
        <button type="button" className="secondary" onClick={checkAll} disabled={busy}>
          <Icon name="refresh" size={16} className={busy ? "spin" : ""} /> Check now
        </button>
      </PageHeader>

      <div className={`overall overall-${overall.tone}`}>
        <span className={`dot dot-${overall.tone} pulse`} aria-hidden="true" />
        <div>
          <strong>{overall.title}</strong>
          <div className="small">{overall.detail}</div>
        </div>
        <span className="small grow-right">Checked {api.loadedAt ? timeAgo(api.loadedAt) : "..."}</span>
      </div>

      <div className="status-grid">
        <StatusCard icon="server" title="API server" state={api.error ? "down" : api.data ? "up" : "checking"}>
          {api.error ? <p className="error-text small">{api.error}</p> : (
            <dl className="kv">
              <dt>Answers in</dt><dd>{api.ms != null ? `${api.ms} ms` : "..."}</dd>
              <dt>Version</dt><dd>{api.data?.version ?? "..."}</dd>
            </dl>
          )}
        </StatusCard>

        <StatusCard icon="database" title={`Database${db.data?.service ? ` (${db.data.service})` : ""}`}
          state={db.error ? "down" : !db.data ? "checking" : db.data.connected ? "up" : "down"}>
          {db.error && <p className="error-text small">{db.error}</p>}
          {db.data && !db.data.connected && <p className="error-text small">{db.data.detail}</p>}
          {db.data?.connected && (
            <>
              <dl className="kv">
                <dt>Query time</dt><dd>{db.data.latency_ms} ms</dd>
                {db.data.region && (<><dt>Region</dt><dd>{db.data.region}{db.data.pooled ? " (pooled)" : ""}</dd></>)}
                <dt>Postgres</dt><dd>{db.data.server_version}</dd>
              </dl>
              <TableCounts tables={db.data.tables} />
            </>
          )}
        </StatusCard>

        <StatusCard icon="investigate" title="Live lookup sources" wide
          state={providers.error ? "down" : !providers.data ? "checking" : providerState(providers.data)}>
          {providers.error && <p className="error-text small">{providers.error}</p>}
          {providers.data && (
            <>
              <div className="small muted">Bitcoin</div>
              <div className="provider-list">
                {providers.data.sources.map((s) => <ProviderRow key={s.provider} source={s} />)}
              </div>
              <EvmSources title="Ethereum" query={ethProviders} />
              <EvmSources title="Base" query={baseProviders} />
              <HealthStrip rows={history.data ?? []} />
              {providers.data.recent_fallbacks?.length > 0 && (
                <details className="fallbacks">
                  <summary className="small">Recent fallbacks ({providers.data.recent_fallbacks.length})</summary>
                  <ul className="compact-list small">
                    {providers.data.recent_fallbacks.map((f, i) => (
                      <li key={i}>
                        {timeAgo(f.at)}: {f.method} answered by <strong>{PROVIDER_NAMES[f.answered_by] ?? f.answered_by ?? "nobody"}</strong>
                        <div className="muted">{f.skipped.join("; ")}</div>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <p className="small muted">
                mempool.space answers first; blockstream.info takes over when it is rate limited or down.
                Ethereum lookups use etherscan.io (3 calls per second, 100,000 a day). alchemy.com is the Ethereum
                backup and the Base source once ALCHEMY_ENABLED is switched on.
                {providers.data.cache_entries > 0 && ` ${providers.data.cache_entries} recent answers cached.`}
              </p>
            </>
          )}
        </StatusCard>

        <StatusCard icon="shield" title="Sanction and threat lists" wide
          state={lists.error ? "down" : !lists.data ? "checking" : lists.data.some((l) => !l.loaded) ? "down" : lists.data.some((l) => l.stale) ? "warn" : "up"}>
          {lists.error && <p className="error-text small">{lists.error}</p>}
          {lists.data && (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>List</th><th className="num">Addresses</th><th>Source last changed</th><th>Downloaded</th><th>State</th></tr>
                </thead>
                <tbody>
                  {lists.data.map((l) => (
                    <tr key={l.source_id}>
                      <td>
                        <a href={l.url} target="_blank" rel="noreferrer">{l.title}</a>
                        {l.note && <div className="small muted">{l.note}</div>}
                      </td>
                      <td className="num">{l.record_count?.toLocaleString() ?? "—"}</td>
                      <td title={l.list_updated_at ?? ""}>{formatDate(l.list_updated_at)}</td>
                      <td title={l.retrieved_at ?? ""}>{l.retrieved_at ? timeAgo(l.retrieved_at) : "never"}</td>
                      <td>
                        {!l.loaded ? <span className="pill pill-down">Not loaded</span>
                          : l.stale ? <span className="pill pill-warn">Due for refresh</span>
                          : <span className="pill pill-up">Fresh</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="small muted">The API downloads each list again once it is a day old (checked hourly). A failed download keeps the previous copy.</p>
            </div>
          )}
        </StatusCard>

        <StatusCard icon="bigquery" title="BigQuery refresh" state="dev">
          {bigquery.data ? (
            <dl className="kv">
              <dt>Stage</dt><dd>Under development</dd>
              <dt>Key file</dt><dd>{bigquery.data.key_found ? "found" : "missing"}</dd>
              <dt>Library</dt><dd>{bigquery.data.library_installed ? "installed" : "not installed"}</dd>
            </dl>
          ) : <p className="small muted">{bigquery.error || "..."}</p>}
          <a className="small" href="#/bigquery">Open BigQuery refresh</a>
        </StatusCard>
      </div>
    </>
  );
}

const STATE_LABEL = { up: "Working", warn: "Needs attention", down: "Not working", checking: "Checking", dev: "In development" };

function StatusCard({ icon, title, state, wide, children }) {
  return (
    <section className={`card status-card ${wide ? "wide" : ""}`}>
      <div className="status-card-head">
        <span className={`status-icon tone-${state}`}><Icon name={icon} size={18} /></span>
        <h2>{title}</h2>
        <span className={`pill pill-${state}`}>{STATE_LABEL[state]}</span>
      </div>
      {children}
    </section>
  );
}

// The sources for one EVM chain, or why there are none (no API key).
function EvmSources({ title, query }) {
  return (
    <>
      <div className="small muted">{title}</div>
      {query.data?.configured ? (
        <div className="provider-list">
          {query.data.sources.map((s) => <ProviderRow key={s.provider} source={s} />)}
        </div>
      ) : (
        <p className="small muted">{query.error ?? query.data?.detail ?? "Checking..."}</p>
      )}
    </>
  );
}

function ProviderRow({ source }) {
  const health = source.health;
  const reachable = health ? health.reachable : null;
  const state = !source.ready ? "warn" : reachable === false ? "down" : "up";
  return (
    <div className="provider-row">
      <span className={`dot dot-${state}`} aria-hidden="true" />
      <div className="grow">
        <div>
          <strong>{PROVIDER_NAMES[source.provider] ?? source.provider}</strong>{" "}
          <span className="tag">{source.priority === 1 ? "Primary" : "Backup"}</span>
        </div>
        <div className="small muted">
          {!source.ready
            ? `Cooling down for ${Math.ceil(source.cooldown_seconds_left)} s after: ${source.last_error}`
            : reachable === false
              ? health.detail
              : health?.tip_height ? `Block ${health.tip_height.toLocaleString()}` : "Ready"}
          {source.last_success_at && ` · last answer ${timeAgo(source.last_success_at)}`}
        </div>
      </div>
      <div className="provider-stats small muted" title="Since the API started">
        <span><strong>{source.answers ?? 0}</strong> answered</span>
        <span><strong>{source.failures ?? 0}</strong> failed</span>
      </div>
    </div>
  );
}

// The last checks as a row of bars: green = reachable, red = not.
function HealthStrip({ rows }) {
  if (!rows.length) return <p className="small muted">No saved checks yet. Press "Check now" to record one.</p>;
  const ordered = [...rows].reverse();
  const ok = rows.filter((r) => r.reachable).length;
  return (
    <div className="health-strip-wrap">
      <div className="health-strip" role="img" aria-label={`${ok} of ${rows.length} recent checks reachable`}>
        {ordered.map((r, i) => (
          <span key={i} className={r.reachable ? "bar ok" : "bar bad"}
            title={`${formatDate(r.checked_at, true)}: ${r.reachable ? `ok, block ${r.tip_height?.toLocaleString() ?? "?"}` : r.detail}`} />
        ))}
      </div>
      <div className="small muted">Last {rows.length} saved check{rows.length === 1 ? "" : "s"}: {Math.round((ok / rows.length) * 100)}% reachable</div>
    </div>
  );
}

function TableCounts({ tables }) {
  const shown = ["transactions", "address_snapshots", "investigations", "cases", "case_items", "app_users"];
  const rows = tables.filter((t) => shown.includes(t.table)).sort((a, b) => shown.indexOf(a.table) - shown.indexOf(b.table));
  return (
    <div className="mini-counts">
      {rows.map((t) => (
        <div key={t.table}>
          <strong>{Number(t.rows).toLocaleString()}</strong>
          <span className="small muted">{t.table.replaceAll("_", " ")}</span>
        </div>
      ))}
    </div>
  );
}

function providerState(data) {
  const usable = data.sources.filter((s) => s.ready && s.health?.reachable !== false);
  if (usable.length === data.sources.length) return "up";
  return usable.length ? "warn" : "down";
}

function overallState({ api, db, providers, lists }) {
  if (api.error) return { tone: "down", title: "The API is not answering", detail: api.error };
  if (!api.data || !db.data || !providers.data || !lists.data) return { tone: "checking", title: "Checking...", detail: "Asking each service" };
  const problems = [];
  if (!db.data.connected) problems.push("database not connected");
  const p = providerState(providers.data);
  if (p === "down") problems.push("no live lookup source answering");
  else if (p === "warn") problems.push("running on the backup lookup source");
  if (lists.data.some((l) => !l.loaded)) problems.push("a sanction list is missing");
  else if (lists.data.some((l) => l.stale)) problems.push("a sanction list is due for refresh");
  if (!problems.length) return { tone: "up", title: "Everything is working", detail: "Lookups, database and sanction lists are all available." };
  const down = p === "down" || !db.data.connected;
  return { tone: down ? "down" : "warn", title: down ? "Something is not working" : "Working, with warnings", detail: problems.join("; ") };
}
