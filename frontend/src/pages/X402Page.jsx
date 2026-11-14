import { useCallback, useEffect, useState } from "react";
import PageHeader, { formatDate, timeAgo } from "../components/PageHeader.jsx";
import Icon from "../components/Icon.jsx";
import { apiFetch } from "../api/client.js";

// x402 / agentic payments: AI agents paying APIs in USDC (mostly on Base). Data: Peter Robinson's x402analysis
// snapshot and x402scan's facilitator list (both MIT), live payments from Blockscout, and every payment on Base
// once a Coinbase CDP key is set (backend app/x402).
const short = (a) => (a ? (a.length > 16 ? `${a.slice(0, 8)}…${a.slice(-6)}` : a) : "—");
const usd = (n, digits = 2) => (n == null ? "—" : `US$${Number(n).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: n < 1 && n > 0 ? Math.min(digits, 4) : 0 })}`);
const SEVERITY_PILL = { high: "pill-down", medium: "pill-warn", low: "pill-muted", info: "pill-muted" };
const TX_URL = (tx) => `https://base.blockscout.com/tx/${tx}`;
const ADDRESS_URL = (a) => (a?.startsWith("0x") ? `https://base.blockscout.com/address/${a}` : `https://solscan.io/account/${a}`);

function Severity({ value }) {
  if (!value) return <span className="pill pill-muted">no flags</span>;
  return <span className={`pill ${SEVERITY_PILL[value] ?? "pill-muted"}`}>{value}</span>;
}

function Hits({ hits, max = 3 }) {
  if (!hits?.length) return <span className="muted small">—</span>;
  return (
    <>
      {hits.slice(0, max).map((h, i) => (
        <span key={`${h.rule}-${i}`} className={`pill ${SEVERITY_PILL[h.severity] ?? "pill-muted"}`} title={h.detail}>{h.title}</span>
      ))}
      {hits.length > max && <span className="small muted"> +{hits.length - max} more</span>}
    </>
  );
}

export default function X402Page({ onInvestigate }) {
  const [summary, setSummary] = useState(null);
  const [payments, setPayments] = useState([]);
  const [servers, setServers] = useState([]);
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [s, p, r] = await Promise.all([
        apiFetch("/api/x402/summary"),
        apiFetch(`/api/x402/payments?limit=100${flaggedOnly ? "&flagged_only=true" : ""}`),
        apiFetch("/api/x402/risky-servers?limit=25"),
      ]);
      setSummary(s); setPayments(p.payments ?? []); setServers(r.servers ?? []); setError("");
    } catch (e) {
      setError(e.message || "Could not load x402 data.");
    }
  }, [flaggedOnly]);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  const snap = summary?.snapshot;
  const monitor = summary?.monitor;
  const cdp = summary?.sources?.cdp;

  return (
    <>
      <PageHeader icon="payment" title="x402 payments"
        subtitle="AI agents paying for APIs in USDC over HTTP 402. Spot payments linked to phishing, cash-out services and sanctioned addresses.">
        <button type="button" className="secondary" onClick={load}><Icon name="refresh" size={16} /> Refresh</button>
      </PageHeader>

      {error && <p className="error-text">{error}</p>}

      <QuickCheck onInvestigate={onInvestigate} />

      <section className="card">
        <div className="section-head"><h2>Monitoring</h2></div>
        {!monitor ? <p className="muted small">Loading…</p> : !monitor.enabled ? (
          <p className="small">{monitor.detail}</p>
        ) : (
          <dl className="kv">
            <dt>Mode</dt>
            <dd>
              {monitor.network ? "Every x402 payment on Base (CDP SQL API), plus the watchlist" : `Watchlist: ${monitor.watching} risky x402 server addresses`}
              {monitor.network_error && <span className="pill pill-warn" title={monitor.network_error}>CDP problem</span>}
            </dd>
            <dt>Source</dt><dd>{monitor.source}</dd>
            <dt>Payments seen</dt><dd>{monitor.payments_seen.toLocaleString()} ({usd(monitor.volume_usdc)}) since the API started</dd>
            <dt>Alerts</dt><dd>{monitor.events} (shown on Live alerts with the other chains)</dd>
            <dt>Last check</dt><dd>{monitor.last_round_at ? timeAgo(monitor.last_round_at) : "not yet"}; a round every {Math.round(monitor.round_seconds / 60)} min</dd>
            {monitor.last_error && <><dt>Last problem</dt><dd className="small">{monitor.last_error} ({timeAgo(monitor.last_error_at)})</dd></>}
          </dl>
        )}
        {cdp && !cdp.ready && (
          <p className="small muted"><Icon name="lock" size={12} /> {cdp.detail} <a href={cdp.docs} target="_blank" rel="noreferrer">CDP SQL API <Icon name="external" size={11} /></a></p>
        )}
        {monitor?.watchlist?.length > 0 && (
          <details className="small">
            <summary>Watched server addresses ({monitor.watchlist.length})</summary>
            <ul className="plain-list">
              {monitor.watchlist.map((w) => (
                <li key={w.address}><a className="mono" href={ADDRESS_URL(w.address)} target="_blank" rel="noreferrer">{short(w.address)}</a> {w.reasons.join(" · ")}</li>
              ))}
            </ul>
          </details>
        )}
      </section>

      <section className="card">
        <div className="section-head">
          <h2>Recent x402 payments ({payments.length})</h2>
          <label className="small"><input type="checkbox" checked={flaggedOnly} onChange={(e) => setFlaggedOnly(e.target.checked)} /> Flagged only</label>
        </div>
        {!payments.length ? (
          <p className="muted small">No payments seen yet. The monitor records payments to watched servers as they happen{monitor?.network ? ", and every x402 payment on Base" : ""}.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>When</th><th>Payer</th><th>Server</th><th className="num">Amount</th><th>Facilitator</th><th>Flags</th><th>Transaction</th></tr></thead>
              <tbody>
                {payments.map((p) => (
                  <tr key={`${p.tx}-${p.payer}-${p.pay_to}`} className={p.severity === "high" ? "alert-row-sanctioned" : ""}>
                    <td title={formatDate(p.at || p.seen_at, true)} style={{ whiteSpace: "nowrap" }}>{timeAgo(p.at || p.seen_at)}</td>
                    <td className="mono small" style={{ whiteSpace: "nowrap" }}><a href={ADDRESS_URL(p.payer)} target="_blank" rel="noreferrer">{short(p.payer)}</a></td>
                    <td className="small" title={p.pay_to}>{p.server?.[0] ?? short(p.pay_to)}</td>
                    <td className="num">{usd(p.amount, 4)}</td>
                    <td className="small">{p.facilitator_name ?? short(p.facilitator)}</td>
                    <td className="small"><Severity value={p.severity} /> <Hits hits={p.hits} max={2} /></td>
                    <td className="small">
                      <a href={TX_URL(p.tx)} target="_blank" rel="noreferrer" className="mono" style={{ whiteSpace: "nowrap" }}>{short(p.tx)} <Icon name="external" size={12} /></a>{" "}
                      {onInvestigate && <button type="button" className="secondary small-button" onClick={() => onInvestigate("base_txid", p.tx, "x402", "x402_rules")}>Investigate</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <div className="section-head"><h2>Riskiest x402 servers</h2></div>
        <p className="small muted">Servers that took the most USDC from phishing-linked clients, alternating with the busiest servers selling high-risk services (gift cards, eSIMs, attack tools, exposure lookups). From the snapshot.</p>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Server</th><th>Payment address</th><th className="num">Received</th><th className="num">From phishing-linked</th><th>Services</th><th /></tr></thead>
            <tbody>
              {servers.map((s) => (
                <tr key={s.address}>
                  <td className="small">{s.servers.slice(0, 2).join(", ")}{s.servers.length > 2 ? ` +${s.servers.length - 2}` : ""}<br /><span className="muted">{s.chain}{s.countries?.length ? ` · ${s.countries.join(", ")}` : ""}</span></td>
                  <td className="mono small" style={{ whiteSpace: "nowrap" }}><a href={ADDRESS_URL(s.address)} target="_blank" rel="noreferrer">{short(s.address)}</a></td>
                  <td className="num">{usd(s.usdc_received, 0)}</td>
                  <td className="num">{s.from_phishing >= 1 ? <>{usd(s.from_phishing, 0)} <span className={`pill ${s.from_phishing_percent >= 15 ? "pill-down" : s.from_phishing_percent >= 5 ? "pill-warn" : "pill-muted"}`}>{s.from_phishing_percent?.toFixed(1)}%</span></> : "—"}</td>
                  <td className="small">{s.risk === "high" && <span className="pill pill-warn">high-risk</span>} {s.categories.map((c) => c.replace(/^\*/, "")).slice(0, 4).join(", ")}</td>
                  <td>{onInvestigate && s.chain !== "other" && (
                    <button type="button" className="secondary small-button"
                      onClick={() => onInvestigate(`${s.chain}_address`, s.address, "x402", "x402_rules")}>Investigate</button>)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {snap && (
        <section className="card">
          <div className="section-head"><h2>Data</h2></div>
          {!snap.loaded ? (
            <p className="small">The x402 snapshot is missing. Build it with <code>python -m training.x402_snapshot</code> (see backend/data/x402/README.md).</p>
          ) : (
            <>
              <p className="small">
                {snap.counts.payment_addresses?.toLocaleString()} server payment addresses, {snap.counts.facilitator_addresses} facilitator addresses,
                {" "}{snap.counts.clients?.toLocaleString()} paying clients, {snap.counts.phishing_addresses} phishing-tagged addresses
                and {snap.counts.phishing_funded_addresses} addresses they funded. Services gathered {formatDate(snap.gathered?.services)},
                clients {formatDate(snap.gathered?.clients_base)}.
              </p>
              <p className="small muted">
                {snap.sources.map((s, i) => (
                  <span key={s.url}>{i > 0 && " · "}<a href={s.url} target="_blank" rel="noreferrer">{s.name}</a> ({s.licence})</span>
                ))}
                {" "}· Live: {summary.sources.explorer}{summary.sources.alchemy_backup ? ", Alchemy as backup" : ""}{cdp?.ready ? ", Coinbase CDP SQL API" : ""}.
                Rules point to things worth a closer look; they are not verdicts. No model is trained on this data.
              </p>
            </>
          )}
        </section>
      )}
    </>
  );
}

function QuickCheck({ onInvestigate }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState(null);
  const [problem, setProblem] = useState("");

  async function check(e) {
    e.preventDefault();
    const v = value.trim();
    setOut(null); setProblem("");
    const isTx = /^0x[0-9a-fA-F]{64}$/.test(v);
    const isEvm = /^0x[0-9a-fA-F]{40}$/.test(v);
    const isSol = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(v);
    if (!isTx && !isEvm && !isSol) { setProblem("Paste a Base transaction hash, a Base address or a Solana address."); return; }
    setBusy(true);
    try {
      const body = isTx ? await apiFetch(`/api/x402/tx/${v}`) : await apiFetch(`/api/x402/address/${v}?chain=${isEvm ? "base" : "solana"}`);
      setOut({ kind: isTx ? "tx" : "address", chain: isEvm || isTx ? "base" : "solana", value: v, body });
    } catch (err) {
      setProblem(err.message || "Check failed.");
    } finally {
      setBusy(false);
    }
  }

  const inputType = out ? (out.kind === "tx" ? "base_txid" : `${out.chain}_address`) : null;
  return (
    <section className="card">
      <div className="section-head"><h2>Check a payment or address</h2></div>
      <form onSubmit={check} style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input className="mono" style={{ flex: "1 1 320px" }} value={value} onChange={(e) => setValue(e.target.value)} maxLength={100}
          placeholder="Base transaction hash, Base address or Solana address" aria-label="Transaction or address" />
        <button type="submit" disabled={busy}>{busy ? "Checking…" : "Check"}</button>
      </form>
      {problem && <p className="error-text small">{problem}</p>}
      {out?.kind === "tx" && (
        <div className="small">
          <p>
            {out.body.x402 ? <strong>x402 payment</strong> : <strong>Not an x402 payment</strong>}
            {" "}sent by {out.body.facilitator ? `${out.body.facilitator.names[0]} (facilitator)` : <span className="mono">{short(out.body.sender)}</span>}.
            {" "}Source: {out.body.source}.
          </p>
          {out.body.payments.map((p, i) => (
            <p key={i}>
              <span className="mono">{short(p.payer)}</span> paid <span className="mono">{short(p.pay_to)}</span> {usd(p.amount, 4)} USDC{" "}
              <Severity value={p.severity} /> <Hits hits={p.hits} max={6} />
            </p>
          ))}
          {!out.body.payments.length && <p className="muted">No USDC moved in this transaction.</p>}
        </div>
      )}
      {out?.kind === "address" && (
        <div className="small">
          <p>
            {out.body.roles.length ? <>Known to the snapshot as: <strong>{out.body.roles.join(", ")}</strong>. </> : "Not in the x402 snapshot. "}
            {out.body.client && <>Paid {out.body.client.servers_paid} servers {out.body.client.transfers.toLocaleString()} times ({usd(out.body.client.usdc)}). </>}
            {out.body.server && <>Payment address of {out.body.server.servers.map((s) => s.name).slice(0, 3).join(", ")}. </>}
            <Severity value={out.body.severity} />
          </p>
          <p><Hits hits={out.body.hits} max={6} /></p>
        </div>
      )}
      {out && onInvestigate && (
        <button type="button" className="secondary small-button" onClick={() => onInvestigate(inputType, out.value, "x402", "x402_rules")}>
          Open full investigation (graph, evidence{out.chain === "base" ? ", live payments" : ""})
        </button>
      )}
    </section>
  );
}
