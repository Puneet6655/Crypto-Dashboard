import { useEffect } from "react";
import PageHeader, { formatDate, timeAgo } from "../components/PageHeader.jsx";
import Icon from "../components/Icon.jsx";

const short = (a) => (a ? `${a.slice(0, 8)}…${a.slice(-6)}` : "contract creation");

// Live alerts: new Ethereum transactions sent by or to an address in an open case or on the OFAC list.
export default function AlertsPage({ live, onInvestigate }) {
  const { alerts, status, markSeen, reload } = live;
  useEffect(() => markSeen(), [alerts.length]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <PageHeader icon="bell" title="Live alerts"
        subtitle="New Ethereum transactions by addresses in open cases or on the OFAC list, as they are mined.">
        <button type="button" className="secondary" onClick={reload}>
          <Icon name="refresh" size={16} /> Check now
        </button>
      </PageHeader>

      <WatcherStatus status={status} />

      <section className="card">
        <div className="section-head"><h2>Alerts ({alerts.length})</h2></div>
        {!alerts.length ? (
          <p className="muted small">
            No alerts yet. An alert appears here and as a pop-up when a watched address sends or receives ETH.
            Add Ethereum addresses to an open case to watch them.
          </p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>When</th><th>Watched address</th><th>Why watched</th><th>Direction</th><th className="num">ETH</th><th>Transaction</th><th aria-label="Actions" /></tr>
              </thead>
              <tbody>
                {alerts.map((a) => {
                  const watched = a.watched[0];
                  const other = a.direction === "out" ? a.to_address : a.from_address;
                  return (
                    <tr key={a.id} className={a.sanctioned ? "alert-row-sanctioned" : ""}>
                      <td title={formatDate(a.at, true)}>{timeAgo(a.at)}</td>
                      <td className="mono small">{short(watched?.address)}</td>
                      <td className="small">
                        {watched?.reasons.map((r) => (
                          <span key={r} className={`pill ${r.includes("OFAC") ? "pill-down" : "pill-muted"}`}>{r}</span>
                        ))}
                        {a.removed && <span className="pill pill-warn">dropped by re-org</span>}
                      </td>
                      <td className="small">{a.direction === "out" ? "sent to" : "received from"} <span className="mono">{short(other)}</span></td>
                      <td className="num">{a.value_eth}</td>
                      <td className="small">
                        <a href={`https://etherscan.io/tx/${a.tx_hash}`} target="_blank" rel="noreferrer" className="mono">
                          {short(a.tx_hash)} <Icon name="external" size={12} />
                        </a>
                      </td>
                      <td>
                        <button type="button" className="secondary small-button"
                          onClick={() => onInvestigate("ethereum_txid", a.tx_hash)}>Investigate</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="small muted">Alerts are kept in the API's memory (the newest 200) and clear when it restarts.</p>
      </section>
    </>
  );
}

function WatcherStatus({ status }) {
  if (!status) return <section className="card muted small">Checking the live alert connection...</section>;
  if (!status.enabled) return <section className="card small"><p className="muted">{status.detail}</p></section>;
  const tone = status.connected ? "up" : status.watching ? "down" : "warn";
  return (
    <section className="card">
      <div className="section-head">
        <h2>Connection to Alchemy</h2>
        <span className={`pill pill-${tone}`}>{status.connected ? "Live" : status.watching ? "Reconnecting" : "Nothing to watch"}</span>
      </div>
      <dl className="kv">
        <dt>Watching</dt>
        <dd>{status.watching} Ethereum addresses ({status.watching_cases} from open cases, {status.watching_ofac} OFAC)</dd>
        {status.left_out > 0 && (<><dt>Left out</dt><dd>{status.left_out} OFAC addresses (Alchemy allows 500 per subscription)</dd></>)}
        <dt>Connected since</dt><dd>{status.connected_since ? formatDate(status.connected_since, true) : "not connected"}</dd>
        <dt>Last alert</dt><dd>{status.last_event_at ? timeAgo(status.last_event_at) : "none yet"}</dd>
        <dt>Compute units used</dt><dd>about {status.estimated_cu.toLocaleString()} since the API started (free plan: 30 million a month)</dd>
        {status.paused?.length > 0 && (<><dt>Paused</dt><dd className="small">
          {status.paused.map((p) => <div key={p.address} className="mono">{short(p.address)} (over {status.busy_limit_per_hour} alerts an hour; paused for a day to save compute units)</div>)}
        </dd></>)}
        {status.last_error && (<><dt>Last problem</dt><dd className="error-text small">{status.last_error} ({timeAgo(status.last_error_at)})</dd></>)}
      </dl>
    </section>
  );
}
