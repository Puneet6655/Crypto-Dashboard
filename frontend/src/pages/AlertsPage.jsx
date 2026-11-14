import { useCallback, useEffect, useState } from "react";
import PageHeader, { formatDate, timeAgo } from "../components/PageHeader.jsx";
import Icon from "../components/Icon.jsx";
import { apiFetch } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { useToast } from "../components/Toasts.jsx";
import { WEB3_DESIGN } from "../config/w3Design.js";
import W3AlertsView from "../w3/W3AlertsView.jsx";

const short = (a) => (a ? `${a.slice(0, 8)}…${a.slice(-6)}` : "contract creation");

// What each chain needs. The API checks the same rules again (app/alerts/manual.py).
const CHAINS = {
  ethereum: {
    label: "Ethereum", placeholder: "0x followed by 40 hexadecimal characters",
    explorer: (tx) => `https://etherscan.io/tx/${tx}`, investigate: "ethereum_txid",
    how: "Alchemy WebSocket, filtered to watched addresses",
  },
  base: {
    label: "Base", placeholder: "0x followed by 40 hexadecimal characters",
    explorer: (tx) => `https://basescan.org/tx/${tx}`, investigate: "base_txid",
    how: "Alchemy, each address checked in turn (at most one check every 3 minutes)",
  },
  solana: {
    label: "Solana", placeholder: "32 to 44 base58 characters",
    explorer: (tx) => `https://solscan.io/tx/${tx}`, investigate: "solana_txid",
    how: "Alchemy, each address checked in turn (at most one call every 30 seconds)",
  },
  bitcoin: {
    label: "Bitcoin", placeholder: "bc1q..., bc1p..., 1... or 3...",
    explorer: (tx) => `https://mempool.space/tx/${tx}`, investigate: "bitcoin_txid",
    how: "mempool.space, each address checked in turn (no Alchemy units)",
  },
};

const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
function base58Bytes(value) {
  let n = 0n;
  for (const c of value) {
    const d = B58.indexOf(c);
    if (d < 0) return null;
    n = n * 58n + BigInt(d);
  }
  const zeros = value.length - value.replace(/^1+/, "").length;
  return zeros + (n === 0n ? 0 : Math.ceil(n.toString(2).length / 8));
}

// null when the address fits the chain, otherwise what is wrong.
export function addressProblem(chain, raw) {
  const value = raw.trim();
  if (!value) return "Enter an address.";
  if (chain === "ethereum") return /^0x[0-9a-fA-F]{40}$/.test(value) ? null : "An Ethereum address is 0x followed by 40 hexadecimal characters.";
  if (chain === "base") return /^0x[0-9a-fA-F]{40}$/.test(value) ? null : "A Base address is 0x followed by 40 hexadecimal characters.";
  if (chain === "solana") {
    return value.length >= 32 && value.length <= 44 && base58Bytes(value) === 32
      ? null : "A Solana address is 32 to 44 base58 characters (no 0, O, I or l) that decode to 32 bytes.";
  }
  if (chain === "bitcoin") {
    return /^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$/.test(value) || /^bc1[qp][02-9ac-hj-np-z]{38,58}$/.test(value.toLowerCase())
      ? null : "A Bitcoin address starts with 1 or 3 (26 to 35 characters) or bc1q / bc1p (SegWit or Taproot).";
  }
  return "Choose a chain.";
}

// Live alerts: new transactions by watched addresses on Ethereum, Base, Solana and Bitcoin.
export default function AlertsPage({ live, onInvestigate }) {
  const { alerts, status, statuses, markSeen, reload } = live;
  useEffect(() => markSeen(), [alerts.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // Web3 mode: monitored wallets on the left, click one to see its transactions (w3/W3AlertsView.jsx).
  if (WEB3_DESIGN) {
    return (
      <W3AlertsView live={live} onInvestigate={onInvestigate} ManageWatchlist={Watchlist}
        statusCards={<>
          <WatcherStatus status={status} />
          {statuses && ["base", "solana", "bitcoin"].map((chain) => <PollerStatus key={chain} chain={chain} status={statuses[chain]} />)}
        </>} />
    );
  }

  return (
    <>
      <PageHeader icon="bell" title="Live alerts"
        subtitle="New transactions by watched addresses (your watchlist, open cases, the OFAC list) on Ethereum, Base, Solana and Bitcoin.">
        <button type="button" className="secondary" onClick={reload}>
          <Icon name="refresh" size={16} /> Check now
        </button>
      </PageHeader>

      <Watchlist onChange={reload} />

      <WatcherStatus status={status} />
      {statuses && ["base", "solana", "bitcoin"].map((chain) => <PollerStatus key={chain} chain={chain} status={statuses[chain]} />)}

      <section className="card">
        <div className="section-head"><h2>Alerts ({alerts.length})</h2></div>
        {!alerts.length ? (
          <p className="muted small">
            No alerts yet. An alert appears here and as a pop-up when a watched address sends or receives money.
            Add addresses to the watchlist above, or to an open case (Ethereum).
          </p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>When</th><th>Chain</th><th>Watched address</th><th>Why watched</th><th>Direction</th><th className="num">Amount</th><th>Transaction</th><th aria-label="Actions" /></tr>
              </thead>
              <tbody>
                {alerts.map((a) => {
                  const watched = a.watched[0];
                  const chain = CHAINS[a.chain ?? "ethereum"] ?? CHAINS.ethereum;
                  const amount = a.amount ?? a.value_eth;
                  return (
                    <tr key={a.id} className={a.sanctioned ? "alert-row-sanctioned" : ""}>
                      <td title={formatDate(a.at, true)}>{timeAgo(a.at)}</td>
                      <td className="small">{chain.label}{a.kind === "x402" && <span className="pill pill-warn">x402</span>}</td>
                      <td className="mono small" title={watched?.address}>{short(watched?.address)}</td>
                      <td className="small">
                        {watched?.reasons.map((r) => (
                          <span key={r} className={`pill ${r.includes("OFAC") ? "pill-down" : "pill-muted"}`}>{r}</span>
                        ))}
                        {a.removed && <span className="pill pill-warn">dropped by re-org</span>}
                        {a.failed && <span className="pill pill-warn">failed</span>}
                      </td>
                      <td className="small">
                        {a.direction
                          ? <>{a.direction === "out" ? "sent to" : "received from"} <span className="mono">{a.counterparty ? short(a.counterparty) : "several"}</span></>
                          : <span className="muted">took part</span>}
                      </td>
                      <td className="num">{amount != null ? `${amount} ${a.unit ?? "ETH"}` : "—"}</td>
                      <td className="small">
                        <a href={chain.explorer(a.tx_hash)} target="_blank" rel="noreferrer" className="mono">
                          {short(a.tx_hash)} <Icon name="external" size={12} />
                        </a>
                      </td>
                      <td>
                        <button type="button" className="secondary small-button"
                          onClick={() => (a.kind === "x402" ? onInvestigate("base_txid", a.tx_hash, "x402", "x402_rules") : onInvestigate(chain.investigate, a.tx_hash))}>Investigate</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="small muted">Alerts are kept in the API's memory (the newest 200 per chain) and clear when it restarts.</p>
      </section>
    </>
  );
}

function Watchlist({ onChange }) {
  const { can } = useAuth();
  const toast = useToast();
  const canEdit = can("analyst");
  const [data, setData] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [form, setForm] = useState({ chain: "ethereum", address: "", label: "" });
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await apiFetch("/api/alerts/watchlist"));
      setLoadError("");
    } catch (e) {
      setLoadError(e.message);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const submit = async (e) => {
    e.preventDefault();
    const why = addressProblem(form.chain, form.address);
    setProblem(why ?? "");
    if (why) return;
    setBusy(true);
    try {
      await apiFetch("/api/alerts/watchlist", { method: "POST", body: { chain: form.chain, address: form.address.trim(), label: form.label.trim() || null } });
      toast(`Watching ${short(form.address.trim())} on ${CHAINS[form.chain].label}`, { tone: "success" });
      setForm({ ...form, address: "", label: "" });
      await load();
      onChange?.();
    } catch (err) {
      setProblem(err.message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (row) => {
    try {
      await apiFetch(`/api/alerts/watchlist/${row.id}`, { method: "DELETE" });
      toast(`Stopped watching ${short(row.address)}`);
      await load();
      onChange?.();
    } catch (err) {
      toast(err.message, { tone: "error" });
    }
  };

  const rows = data?.addresses ?? [];
  const count = (chain) => rows.filter((r) => r.chain === chain).length;

  return (
    <section className="card">
      <div className="section-head"><h2>Watchlist ({rows.length})</h2></div>
      {loadError ? <p className="error-text small">{loadError}</p> : null}
      {canEdit ? (
        <form className="form-grid" onSubmit={submit} noValidate>
          <label className="field">
            <span className="field-label">Chain</span>
            <select value={form.chain} onChange={(e) => { setForm({ ...form, chain: e.target.value }); setProblem(""); }}>
              {Object.entries(CHAINS).map(([id, c]) => (
                <option key={id} value={id}>{c.label}{data ? ` (${count(id)} of ${data.limits[id]})` : ""}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">{CHAINS[form.chain].label} address</span>
            <input className="mono" value={form.address} placeholder={CHAINS[form.chain].placeholder} maxLength={120}
              onChange={(e) => { setForm({ ...form, address: e.target.value }); setProblem(""); }}
              aria-invalid={problem ? "true" : "false"} spellCheck={false} autoComplete="off" />
          </label>
          <label className="field">
            <span className="field-label">Label (optional)</span>
            <input value={form.label} maxLength={100} placeholder="e.g. Suspect exchange deposit"
              onChange={(e) => setForm({ ...form, label: e.target.value })} />
          </label>
          {problem && <p className="error-text small" role="alert">{problem}</p>}
          <p className="small muted">{CHAINS[form.chain].how}.</p>
          <div className="form-actions">
            <button type="submit" disabled={busy}><Icon name="plus" size={16} /> Add to watchlist</button>
          </div>
        </form>
      ) : (
        <p className="muted small">Analysts and admins can add addresses to the watchlist.</p>
      )}
      {rows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Chain</th><th>Address</th><th>Label</th><th>Added</th>{canEdit && <th aria-label="Actions" />}</tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="small">{CHAINS[r.chain]?.label ?? r.chain}</td>
                  <td className="mono small" title={r.address}>{short(r.address)}</td>
                  <td className="small">{r.label ?? <span className="muted">—</span>}</td>
                  <td className="small" title={formatDate(r.added_at, true)}>{timeAgo(r.added_at)}{r.added_by_email ? ` by ${r.added_by_email}` : ""}</td>
                  {canEdit && (
                    <td><button type="button" className="secondary small-button" onClick={() => remove(r)}>Remove</button></td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && Object.entries(data.running).some(([, on]) => !on) && (
        <p className="small muted">
          Not watching right now: {Object.entries(data.running).filter(([, on]) => !on).map(([c]) => CHAINS[c].label).join(", ")}.
          Addresses are saved and watched once that chain's alerts are on (see below).
        </p>
      )}
    </section>
  );
}

function PollerStatus({ chain, status }) {
  const label = CHAINS[chain].label;
  if (!status) return null;
  if (!status.enabled) return <section className="card small"><h2>{label} alerts</h2><p className="muted">{status.detail}</p></section>;
  const tone = status.connected ? "up" : status.watching ? "warn" : "muted";
  return (
    <section className="card">
      <div className="section-head">
        <h2>{label} alerts</h2>
        <span className={`pill pill-${tone}`}>{status.connected ? "Checking" : status.watching ? "Starting" : "Nothing to watch"}</span>
      </div>
      <dl className="kv">
        <dt>Source</dt><dd>{status.source}</dd>
        <dt>Watching</dt><dd>{status.watching} of {status.max_watched} {label} addresses{status.left_out > 0 ? ` (${status.left_out} left out)` : ""}</dd>
        <dt>Each address checked</dt><dd>about every {Math.round(status.round_seconds / 60)} minutes</dd>
        <dt>Last round</dt><dd>{status.last_round_at ? timeAgo(status.last_round_at) : "not yet"}</dd>
        <dt>Last alert</dt><dd>{status.last_event_at ? timeAgo(status.last_event_at) : "none yet"}</dd>
        {status.cu_per_call > 0 && (<><dt>Compute units used</dt><dd>about {status.estimated_cu.toLocaleString()} since the API started ({status.cu_per_call} per check; capped near 3.5 million a month)</dd></>)}
        {status.last_error && (<><dt>Last problem</dt><dd className="error-text small">{status.last_error} ({timeAgo(status.last_error_at)})</dd></>)}
      </dl>
    </section>
  );
}

function WatcherStatus({ status }) {
  if (!status) return <section className="card muted small">Checking the live alert connection...</section>;
  if (!status.enabled) return <section className="card small"><h2>Ethereum alerts</h2><p className="muted">{status.detail}</p></section>;
  const tone = status.connected ? "up" : status.watching ? "down" : "warn";
  return (
    <section className="card">
      <div className="section-head">
        <h2>Ethereum alerts (Alchemy connection)</h2>
        <span className={`pill pill-${tone}`}>{status.connected ? "Live" : status.watching ? "Reconnecting" : "Nothing to watch"}</span>
      </div>
      <dl className="kv">
        <dt>Watching</dt>
        <dd>{status.watching} Ethereum addresses ({status.watching_manual ?? 0} from the watchlist, {status.watching_cases} from open cases, {status.watching_ofac} OFAC)</dd>
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
