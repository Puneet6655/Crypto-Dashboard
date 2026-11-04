import { formatDate, timeAgo } from "./PageHeader.jsx";

// API usage per outside source, counted by our own API (GET /api/status/usage).
// The source cards on the Sources page use the built-in names below to find their usage row.
export const USAGE_ID_BY_SOURCE_NAME = {
  "Alchemy API": "alchemy",
  "Etherscan API V2": "etherscan",
  "mempool.space REST API": "mempool.space",
};
export function usageIdFor(name) {
  if (USAGE_ID_BY_SOURCE_NAME[name]) return USAGE_ID_BY_SOURCE_NAME[name];
  if (name?.startsWith("Blockstream Esplora")) return "blockstream.info";
  return null;
}

// "2026-11-04" (a UTC day from the API) shown as that calendar day, not shifted by the time zone.
const day = (value) => formatDate(value?.length === 10 ? `${value}T00:00:00` : value);
const fmt = (n) => Math.round(n ?? 0).toLocaleString();
const periodWord = (s) => (s.period === "month" ? "this month" : "today");

function tone(percent) {
  if (percent == null) return "up";
  if (percent >= 90) return "down";
  if (percent >= 70) return "warn";
  return "up";
}

// One line and a bar: "1,234 of 100,000 calls today (1.2%)".
export function UsageMeter({ source, compact }) {
  if (!source) return null;
  const { quota, percent, used, unit } = source;
  const label = quota
    ? `${fmt(used)} of ${fmt(quota)} ${unit} ${periodWord(source)} (${percent < 0.1 && used > 0 ? "<0.1" : percent.toFixed(1)}%)`
    : `${fmt(source.today.calls)} calls today · ${fmt(source.this_month.calls)} this month`;
  return (
    <div className={`usage-meter ${compact ? "compact" : ""}`}>
      <div className="small">
        {compact && <span className="muted">API usage: </span>}
        {label}
        {source.today.errors > 0 && <span className="muted"> · {fmt(source.today.errors)} failed today</span>}
      </div>
      {quota ? (
        <div className="usage-bar" role="meter" aria-valuemin={0} aria-valuemax={quota} aria-valuenow={used}
          aria-label={`${source.label} usage ${periodWord(source)}`}>
          <span className={`usage-fill tone-${tone(percent)}`} style={{ width: `${Math.min(100, Math.max(percent, used > 0 ? 0.5 : 0))}%` }} />
        </div>
      ) : null}
    </div>
  );
}

// The last days as small bars, scaled to the busiest day.
function DailyBars({ source }) {
  const values = source.daily.map((d) => (source.unit === "CU" ? d.units : d.calls));
  const top = Math.max(1, ...values);
  return (
    <div className="usage-days" role="img" aria-label={`${source.label}: ${source.unit} per day, last ${values.length} days`}>
      {source.daily.map((d, i) => (
        <span key={d.day} className="usage-day" title={`${day(d.day)}: ${fmt(values[i])} ${source.unit}${source.unit === "CU" ? ` (${fmt(d.calls)} calls)` : ""}`}>
          <span style={{ height: `${values[i] ? Math.max(6, (values[i] / top) * 100) : 0}%` }} />
        </span>
      ))}
    </div>
  );
}

// The full panel for the Status page: every source with its quota bar, today and this month,
// the last 14 days, and which kinds of call used the most.
export function UsagePanel({ query }) {
  if (query.error) return <p className="error-text small">{query.error}</p>;
  if (!query.data) return <p className="small muted">Loading usage...</p>;
  const { sources, saved, counting_since } = query.data;
  return (
    <>
      <div className="usage-list">
        {sources.map((s) => (
          <div key={s.id} className="usage-row">
            <div className="usage-head">
              <strong>{s.label}</strong>
              <span className="small muted">{s.used_for}</span>
              {s.dashboard_url && (
                <a className="small grow-right" href={s.dashboard_url} target="_blank" rel="noreferrer">Official usage</a>
              )}
            </div>
            <UsageMeter source={s} />
            <dl className="kv usage-kv small">
              <dt>Today</dt><dd>{fmt(s.today.calls)} calls{s.unit === "CU" ? ` · ${fmt(s.today.units)} CU` : ""}</dd>
              <dt>{s.id === "alchemy" ? "This billing month" : "This month"}</dt>
              <dd>{fmt(s.this_month.calls)} calls{s.unit === "CU" ? ` · ${fmt(s.this_month.units)} CU` : ""}</dd>
              <dt>Rate limit</dt><dd>{s.rate_limit}</dd>
            </dl>
            <DailyBars source={s} />
            {s.by_kind.length > 0 && (
              <details>
                <summary className="small">Busiest calls {s.id === "alchemy" ? "this billing month" : "this month"}</summary>
                <table className="table compact-table small">
                  <thead><tr><th>Call</th><th className="num">Calls</th>{s.unit === "CU" && <th className="num">CU</th>}<th className="num">Failed</th></tr></thead>
                  <tbody>
                    {s.by_kind.map((k) => (
                      <tr key={k.kind}>
                        <td><code>{k.kind}</code></td>
                        <td className="num">{fmt(k.calls)}</td>
                        {s.unit === "CU" && <td className="num">{fmt(k.units)}</td>}
                        <td className="num">{fmt(k.errors)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            )}
            <p className="small muted">{s.note}</p>
          </div>
        ))}
      </div>
      <p className="small muted">
        Counted by this API since {saved ? day(counting_since) : `it started ${timeAgo(counting_since)} (no database, so counts reset on restart)`}.
        Calls made with the same keys elsewhere (scripts, another copy of the API) are not included; the
        official usage pages have the exact figures. Cached answers cost nothing and are not counted.
      </p>
    </>
  );
}
