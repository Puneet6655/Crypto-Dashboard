import { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { useCases } from "../cases/CasesContext.jsx";
import PageHeader, { formatDate, timeAgo } from "../components/PageHeader.jsx";
import Icon from "../components/Icon.jsx";
import { useToast } from "../components/Toasts.jsx";
import { navigate } from "../hooks/useHashRoute.js";
import ChainBadge from "./ChainBadge.jsx";
import { CHAIN_INFO, chainOf } from "./chains.js";

// Live alerts in Web3 mode: everything under monitoring on the left (watchlist wallets and open cases),
// click one to see its transactions on the right. Recorded alerts show at once; older on-chain history
// loads only on request, because on Base and Solana every lookup costs Alchemy compute units.

const LIVE_CHAINS = ["ethereum", "base", "solana", "bitcoin"];
const EXPLORER_TX = {
  bitcoin: (tx) => `https://mempool.space/tx/${tx}`,
  ethereum: (tx) => `https://etherscan.io/tx/${tx}`,
  base: (tx) => `https://basescan.org/tx/${tx}`,
  solana: (tx) => `https://solscan.io/tx/${tx}`,
};
const EXPLORER_ADDRESS = {
  bitcoin: (a) => `https://mempool.space/address/${a}`,
  ethereum: (a) => `https://etherscan.io/address/${a}`,
  base: (a) => `https://basescan.org/address/${a}`,
  solana: (a) => `https://solscan.io/account/${a}`,
};
const key = (chain, address) => `${chain}:${String(address).toLowerCase()}`;
const short = (a) => (a ? `${a.slice(0, 8)}…${a.slice(-6)}` : "—");
const RECENT_MS = 60 * 60 * 1000; // "moved money in the last hour" pulse

export default function W3AlertsView({ live, onInvestigate, ManageWatchlist, statusCards }) {
  const { alerts, status, statuses, markSeen, reload } = live;
  const { cases, refresh: refreshCases } = useCases();
  useEffect(() => { refreshCases(); }, [refreshCases]); // the case list may not be loaded yet
  const { can } = useAuth();
  const toast = useToast();
  const [watch, setWatch] = useState(null);
  const [selected, setSelected] = useState(null); // key of the selected wallet
  const [showManage, setShowManage] = useState(false);
  useEffect(() => markSeen(), [alerts.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadWatch = useCallback(async () => {
    try { setWatch(await apiFetch("/api/alerts/watchlist")); } catch { setWatch({ addresses: [], running: {}, limits: {} }); }
  }, []);
  useEffect(() => { loadWatch(); }, [loadWatch]);

  // Alerts per wallet, newest first.
  const byWallet = useMemo(() => {
    const map = new Map();
    for (const a of alerts) {
      const chain = a.chain ?? "ethereum";
      for (const w of a.watched ?? []) {
        const k = key(chain, w.address);
        if (!map.has(k)) map.set(k, []);
        map.get(k).push(a);
      }
    }
    return map;
  }, [alerts]);

  // Everything under monitoring, grouped. Ethereum addresses in open cases are watched live by the API;
  // the other chains' case addresses are listed too, marked "not watched", with a one-click Watch.
  const groups = useMemo(() => {
    const out = [];
    const watchRows = watch?.addresses ?? [];
    const watchedKeys = new Set(watchRows.map((r) => key(r.chain, r.address)));
    if (watchRows.length) {
      out.push({ id: "watchlist", title: "Watchlist", icon: "bell", wallets: watchRows.map((r) => ({
        k: key(r.chain, r.address), chain: r.chain, address: r.address, label: r.label, live: true, source: "Watchlist", row: r,
      })) });
    }
    for (const c of cases.filter((x) => x.status === "open")) {
      const wallets = c.items.filter((i) => i.item_type === "address").map((i) => {
        const chain = chainOf(i.network);
        const k = key(chain, i.value);
        return { k, chain, address: i.value, label: i.label || i.note || null, source: `Case #${c.id}`,
          live: chain === "ethereum" || watchedKeys.has(k) };
      }).filter((w) => LIVE_CHAINS.includes(w.chain));
      if (wallets.length) out.push({ id: `case-${c.id}`, title: c.title, caseId: c.id, priority: c.priority, icon: "cases", wallets });
    }
    return out;
  }, [watch, cases]);

  const allWallets = groups.flatMap((g) => g.wallets);
  const current = allWallets.find((w) => w.k === selected) ?? null;

  async function addToWatchlist(w) {
    try {
      await apiFetch("/api/alerts/watchlist", { method: "POST", body: { chain: w.chain, address: w.address, label: w.label?.slice(0, 100) ?? w.source } });
      toast(`Watching ${short(w.address)} on ${CHAIN_INFO[w.chain].label}`, { tone: "success" });
      loadWatch();
      reload();
    } catch (err) {
      toast(err.message, { tone: "error" });
    }
  }

  return (
    <>
      <PageHeader icon="bell" title="Live alerts"
        subtitle="Everything under watch on Ethereum, Base, Solana and Bitcoin. Click a wallet to see its transactions.">
        <button type="button" className="secondary" onClick={() => setShowManage((s) => !s)} aria-expanded={showManage}>
          <Icon name="plus" size={16} /> {showManage ? "Hide watchlist editor" : "Add or remove wallets"}
        </button>
        <button type="button" className="secondary" onClick={() => { reload(); loadWatch(); }}>
          <Icon name="refresh" size={16} /> Check now
        </button>
      </PageHeader>

      <ChainPulse status={status} statuses={statuses} alerts={alerts} />

      {showManage && <div className="w3-manage"><ManageWatchlist onChange={() => { reload(); loadWatch(); }} /></div>}

      <div className="w3-alerts">
        <section className="card w3-monitor" aria-label="Under monitoring">
          <div className="section-head">
            <h2>Under monitoring ({allWallets.length})</h2>
            {status?.watching_ofac > 0 && <span className="pill pill-down" title="Sanctioned Ethereum addresses from the OFAC list, watched in the background">+{status.watching_ofac} OFAC</span>}
          </div>
          {!watch && <p className="muted small">Loading...</p>}
          {watch && !allWallets.length && (
            <p className="muted small">Nothing is watched yet. Use “Add or remove wallets”, or add Ethereum addresses to an open case.</p>
          )}
          {groups.map((g) => (
            <div key={g.id} className="w3-group">
              <div className="w3-group-head">
                <Icon name={g.icon} size={14} />
                {g.caseId ? <a href={`#/cases/${g.caseId}`} className="grow" title="Open the case">{g.title}</a> : <span className="grow">{g.title}</span>}
                {g.priority && <span className={`priority priority-${g.priority}`}>{g.priority}</span>}
              </div>
              <ul className="w3-wallets">
                {g.wallets.map((w) => {
                  const hits = byWallet.get(w.k) ?? [];
                  const last = hits[0]?.at;
                  const hot = last && Date.now() - new Date(last).getTime() < RECENT_MS;
                  return (
                    <li key={g.id + w.k}>
                      <button type="button" className={`w3-wallet ${selected === w.k ? "active" : ""} ${w.live ? "" : "off"}`}
                        onClick={() => setSelected(w.k)} aria-pressed={selected === w.k}>
                        <span className={`w3-live-dot ${hot ? "hot" : w.live ? "on" : ""}`} title={hot ? "Moved money in the last hour" : w.live ? "Watched live" : "Not watched live"} />
                        <ChainBadge chain={w.chain} compact />
                        <span className="w3-wallet-text">
                          <span className="mono">{short(w.address)}</span>
                          {w.label && <span className="small muted w3-wallet-label">{w.label}</span>}
                        </span>
                        <span className="w3-wallet-meta small">
                          {hits.length ? <strong>{hits.length}</strong> : null}
                          <span className="muted">{last ? timeAgo(last) : w.live ? "quiet" : "not watched"}</span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </section>

        <section className="card w3-activity" aria-label="Activity">
          {current ? (
            <WalletPanel key={current.k} wallet={current} alerts={byWallet.get(current.k) ?? []} onInvestigate={onInvestigate}
              canWatch={can("analyst") && !current.live} onWatch={() => addToWatchlist(current)} onClose={() => setSelected(null)} />
          ) : (
            <Feed alerts={alerts} onPick={(a) => {
              const w = a.watched?.[0];
              if (w) setSelected(key(a.chain ?? "ethereum", w.address));
            }} />
          )}
        </section>
      </div>

      <details className="card w3-status-details">
        <summary>Connection details per chain</summary>
        <div className="w3-status-cards">{statusCards}</div>
      </details>
    </>
  );
}

// Live status of each chain as glowing chips.
function ChainPulse({ status, statuses, alerts }) {
  const chips = LIVE_CHAINS.map((chain) => {
    const s = chain === "ethereum" ? status : statuses?.[chain];
    const state = !s ? "checking" : !s.enabled ? "off" : s.connected ? "live" : s.watching ? "starting" : "idle";
    const words = { checking: "checking...", off: "off", live: chain === "ethereum" ? "live" : "checking", starting: "starting", idle: "nothing to watch" }[state];
    const n = alerts.filter((a) => (a.chain ?? "ethereum") === chain).length;
    return { chain, state, words, watching: s?.watching ?? 0, n };
  });
  return (
    <div className="w3-pulse-row">
      {chips.map((c) => (
        <div key={c.chain} className={`w3-pulse w3-pulse-${c.state}`} style={{ "--chain": CHAIN_INFO[c.chain].color }}>
          <span className="w3-pulse-dot" />
          <ChainBadge chain={c.chain} />
          <span className="small">{c.words}</span>
          <span className="small muted grow-right">{c.watching} watched · {c.n} alert{c.n === 1 ? "" : "s"}</span>
        </div>
      ))}
    </div>
  );
}

// No wallet chosen: every alert as a live feed.
function Feed({ alerts, onPick }) {
  return (
    <>
      <div className="section-head"><h2>Live feed ({alerts.length})</h2><span className="small muted">newest first</span></div>
      {!alerts.length ? (
        <div className="w3-empty">
          <div className="w3-radar" aria-hidden="true"><span /><span /><span /></div>
          <p className="muted small">Listening. A new transaction by any wallet on the left shows up here, and a ripple runs across the screen.</p>
        </div>
      ) : (
        <ul className="w3-feed">
          {alerts.map((a) => <AlertItem key={a.id} alert={a} onClick={() => onPick(a)} showWallet />)}
        </ul>
      )}
      <p className="small muted">Click an alert or a wallet to see that wallet's transactions. Alerts are kept in the API's memory (newest 200 per chain).</p>
    </>
  );
}

function AlertItem({ alert: a, onClick, showWallet = false, onInvestigate }) {
  const chain = a.chain ?? "ethereum";
  const amount = a.amount ?? a.value_eth;
  const w = a.watched?.[0];
  const dir = a.direction === "out" ? "out" : a.direction === "in" ? "in" : "any";
  return (
    <li className={`w3-alert w3-dir-${dir} ${a.sanctioned ? "sanctioned" : ""}`}>
      <button type="button" className="w3-alert-main" onClick={onClick} disabled={!onClick}>
        <span className="w3-dir-icon" aria-hidden="true">{dir === "out" ? "↗" : dir === "in" ? "↙" : "•"}</span>
        <span className="w3-alert-body">
          <span>
            {showWallet && <><span className="mono">{short(w?.address)}</span>{" "}</>}
            {a.direction ? (a.direction === "out" ? "sent" : "received") : "took part in a transaction"}
            {amount != null && <strong> {amount} {a.unit ?? "ETH"}</strong>}
            {a.direction && <> {a.direction === "out" ? "to" : "from"} <span className="mono">{a.counterparty ? short(a.counterparty) : "several"}</span></>}
          </span>
          <span className="small muted">
            {timeAgo(a.at)} · {w?.reasons?.join(", ")}
            {a.sanctioned && <span className="pill pill-down">sanctioned</span>}
            {a.removed && <span className="pill pill-warn">dropped by re-org</span>}
          </span>
        </span>
        <ChainBadge chain={chain} compact />
      </button>
      <span className="w3-alert-links">
        <a href={EXPLORER_TX[chain]?.(a.tx_hash)} target="_blank" rel="noreferrer" className="small mono">{short(a.tx_hash)} <Icon name="external" size={11} /></a>
        {onInvestigate && <button type="button" className="secondary small-button" onClick={() => (a.kind === "x402" ? onInvestigate("base_txid", a.tx_hash, "x402", "x402_rules") : onInvestigate(`${chain}_txid`, a.tx_hash))}>Investigate</button>}
      </span>
    </li>
  );
}

// One wallet: its recorded alerts, and its recent on-chain history on request.
function WalletPanel({ wallet: w, alerts, onInvestigate, canWatch, onWatch, onClose }) {
  const [history, setHistory] = useState(null); // null | "loading" | { rows } | { error }
  const costs = w.chain === "base" || w.chain === "solana";

  async function loadHistory() {
    setHistory("loading");
    try {
      setHistory({ rows: await fetchHistory(w.chain, w.address) });
    } catch (err) {
      setHistory({ error: err.message });
    }
  }

  return (
    <div className="w3-wallet-panel">
      <div className="w3-wallet-head">
        <ChainBadge chain={w.chain} />
        <span className="small muted">{w.source}{w.live ? " · watched live" : " · not watched live"}</span>
        <button type="button" className="ghost-icon grow-right" onClick={onClose} title="Back to the live feed" aria-label="Back to the live feed">
          <Icon name="close" size={16} />
        </button>
      </div>
      <p className="mono w3-wallet-address">{w.address}</p>
      {w.label && <p className="small muted">{w.label}</p>}
      <div className="w3-wallet-actions">
        {canWatch && <button type="button" className="primary compact" onClick={onWatch}><Icon name="bell" size={14} /> Watch live</button>}
        <button type="button" className="secondary w3-graph-btn" onClick={() => navigate(`/graph/${w.chain}/${encodeURIComponent(w.address)}`)}>Graph explorer</button>
        <button type="button" className="secondary" onClick={() => onInvestigate(`${w.chain}_address`, w.address)}>Investigate</button>
        <a className="secondary" href={EXPLORER_ADDRESS[w.chain]?.(w.address)} target="_blank" rel="noreferrer">Explorer <Icon name="external" size={12} /></a>
      </div>
      {!w.live && (
        <p className="notice small">This {CHAIN_INFO[w.chain].label} address is in a case, but only Ethereum case addresses are watched automatically. Use “Watch live” to add it to the watchlist.</p>
      )}

      <h3 className="w3-sub">Alerts since watching ({alerts.length})</h3>
      {alerts.length ? (
        <ul className="w3-feed">{alerts.map((a) => <AlertItem key={a.id} alert={a} onInvestigate={onInvestigate} />)}</ul>
      ) : (
        <p className="muted small">No alerts recorded for this wallet since the API started.</p>
      )}

      <h3 className="w3-sub">Recent on-chain history</h3>
      {history === null && (
        <button type="button" className="secondary" onClick={loadHistory}>
          Load recent transactions{costs ? " (uses Alchemy compute units)" : ""}
        </button>
      )}
      {history === "loading" && <p className="muted small"><span className="spinner small-spinner" aria-hidden="true" /> Loading...</p>}
      {history?.error && <p className="error-text small">{history.error}</p>}
      {history?.rows && (
        history.rows.length ? (
          <ul className="w3-history">
            {history.rows.map((r) => (
              <li key={r.hash} className={`w3-dir-${r.dir}`}>
                <span className="w3-dir-icon" aria-hidden="true">{r.dir === "out" ? "↗" : r.dir === "in" ? "↙" : "•"}</span>
                <span className="grow">
                  <span>{r.text}</span>
                  <span className="small muted block-line" title={r.time ? formatDate(r.time, true) : ""}>{r.time ? timeAgo(r.time) : "pending"}{r.failed ? " · failed" : ""}</span>
                </span>
                <a href={EXPLORER_TX[w.chain]?.(r.hash)} target="_blank" rel="noreferrer" className="small mono">{short(r.hash)}</a>
                <button type="button" className="ghost-icon" title="Investigate this transaction" aria-label="Investigate this transaction"
                  onClick={() => onInvestigate(`${w.chain}_txid`, r.hash)}><Icon name="investigate" size={15} /></button>
              </li>
            ))}
          </ul>
        ) : <p className="muted small">No transactions found.</p>
      )}
    </div>
  );
}

// Recent transactions of an address, in one shape: { hash, time, dir, text, failed }.
async function fetchHistory(chain, address) {
  const me = address.toLowerCase();
  if (chain === "bitcoin") {
    const txs = await apiFetch(`/api/bitcoin/address/${encodeURIComponent(address)}/transactions`);
    return txs.slice(0, 25).map((t) => {
      const got = t.outputs.filter((o) => o.address?.toLowerCase() === me).reduce((s, o) => s + (o.value_sats ?? 0), 0);
      const spent = t.inputs.filter((i) => i.address?.toLowerCase() === me).reduce((s, i) => s + (i.value_sats ?? 0), 0);
      const net = got - spent;
      return { hash: t.txid, time: t.block_time, dir: net < 0 ? "out" : "in",
        text: `${net < 0 ? "Sent" : "Received"} ${(Math.abs(net) / 1e8).toLocaleString(undefined, { maximumFractionDigits: 8 })} BTC` };
    });
  }
  if (chain === "ethereum" || chain === "base") {
    const txs = await apiFetch(`/api/${chain}/address/${encodeURIComponent(address)}/transactions`);
    return txs.slice(0, 25).map((t) => {
      const out = t.from_address?.toLowerCase() === me;
      const eth = Number(t.value_wei) / 1e18;
      const tokens = t.token_transfers?.length ?? 0;
      const what = eth > 0 ? `${eth.toLocaleString(undefined, { maximumFractionDigits: 6 })} ETH` : tokens ? `${tokens} token transfer${tokens === 1 ? "" : "s"}` : t.method ?? "a call";
      return { hash: t.tx_hash, time: t.block_time, dir: out ? "out" : "in", failed: t.status === "failed",
        text: `${out ? "Sent" : "Received"} ${what} ${out ? "to" : "from"} ${short(out ? t.to_address : t.from_address)}` };
    });
  }
  if (chain === "solana") {
    const sigs = await apiFetch(`/api/solana/address/${encodeURIComponent(address)}/signatures`);
    return sigs.slice(0, 25).map((s) => ({ hash: s.signature, time: s.block_time, dir: "any", failed: s.failed, text: "Took part in a transaction" }));
  }
  return [];
}
