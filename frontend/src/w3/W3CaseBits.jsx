import { useState } from "react";
import { useToast } from "../components/Toasts.jsx";
import { navigate } from "../hooks/useHashRoute.js";
import ChainBadge from "./ChainBadge.jsx";
import { CHAIN_INFO, chainOf } from "./chains.js";
import { GRAPH_CHAINS, graphGroups, openInGraph } from "./caseGraph.js";

// Web3 mode pieces for the Cases page.

// Chains in a case with how many items each: [Bitcoin 3] [Ethereum 1].
export function W3CaseChains({ items, compact = false }) {
  const counts = {};
  for (const item of items) {
    const id = chainOf(item.network);
    counts[id] = (counts[id] ?? 0) + 1;
  }
  const order = Object.keys(CHAIN_INFO);
  const entries = Object.entries(counts).sort(([a], [b]) => order.indexOf(a) - order.indexOf(b));
  if (!entries.length) return null;
  return (
    <span className="w3-case-chains">
      {entries.map(([id, n]) => (
        <span key={id} className="w3-case-chain">
          <ChainBadge chain={id} compact={compact} title={`${CHAIN_INFO[id].label}: ${n} item${n === 1 ? "" : "s"}`} />
          <span className="small muted">{n}</span>
        </span>
      ))}
    </span>
  );
}

// "Open in graph explorer" for a whole case: one button per chain the explorer can draw.
export function W3CaseGraphButton({ items }) {
  const toast = useToast();
  const [busy, setBusy] = useState("");
  const groups = graphGroups(items);
  const chains = GRAPH_CHAINS.filter((c) => groups[c]);
  if (!chains.length) return null;

  async function open(chain) {
    setBusy(chain);
    const { opened, trimmed, failed } = await openInGraph(chain, groups[chain]);
    setBusy("");
    if (!opened) toast(`Could not find addresses to start from on ${CHAIN_INFO[chain].label}.`, { tone: "error" });
    else if (trimmed || failed) {
      toast(`Opened ${opened} addresses${trimmed ? `; ${trimmed} more did not fit in one request` : ""}${failed ? `; ${failed} transaction(s) could not be looked up` : ""}.`, { tone: "info" });
    }
  }

  return (
    <span className="w3-case-graph">
      {chains.map((chain) => {
        const g = groups[chain];
        const n = g.addresses.length + g.transactions.length;
        return (
          <button key={chain} type="button" className="secondary w3-graph-btn" onClick={() => open(chain)} disabled={Boolean(busy)}
            title={`Draw this case's ${CHAIN_INFO[chain].label} items together in the graph explorer${g.transactions.length ? " (transactions through the addresses in them)" : ""}`}>
            <GraphIcon /> {busy === chain ? "Opening..." : chains.length > 1 ? `Graph: ${CHAIN_INFO[chain].label} (${n})` : "Open in graph explorer"}
          </button>
        );
      })}
    </span>
  );
}

// One item: open it alone in the explorer.
export function W3ItemGraphButton({ item }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const chain = chainOf(item.network);
  if (!GRAPH_CHAINS.includes(chain) || (item.item_type !== "address" && item.item_type !== "transaction")) return null;

  async function open() {
    if (item.item_type === "address") {
      navigate(`/graph/${chain}/${encodeURIComponent(item.value)}`);
      return;
    }
    setBusy(true);
    const { opened } = await openInGraph(chain, { transactions: [item.value] });
    setBusy(false);
    if (!opened) toast("Could not look up the addresses in this transaction.", { tone: "error" });
  }

  return (
    <button type="button" className="ghost-icon" title="Open in graph explorer" aria-label="Open in graph explorer" onClick={open} disabled={busy}>
      {busy ? <span className="spinner small-spinner" aria-hidden="true" /> : <GraphIcon />}
    </button>
  );
}

// Chain picker for "Add item": "Auto" guesses from the format; 0x addresses can be Ethereum or Base.
export function W3ChainSelect({ value, onChange, guess }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} aria-label="Chain" className="auto w3-chain-select">
      <option value="auto">Auto ({CHAIN_INFO[chainOf(guess)].label})</option>
      {["bitcoin", "ethereum", "base", "solana"].map((c) => <option key={c} value={c}>{CHAIN_INFO[c].label}</option>)}
    </select>
  );
}

// Card to case view (and back) with a smooth morph where the browser supports view transitions.
export function goWithTransition(path, waitForSelector) {
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  if (!document.startViewTransition || reduce) {
    navigate(path);
    return;
  }
  document.startViewTransition(() => new Promise((resolve) => {
    navigate(path);
    const started = performance.now();
    // Rendering is paused during the update, so poll with timers (animation frames do not fire).
    const check = () => {
      if (!waitForSelector || document.querySelector(waitForSelector) || performance.now() - started > 700) resolve();
      else setTimeout(check, 16);
    };
    setTimeout(check, 16);
  }));
}

function GraphIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <circle cx="5" cy="6" r="2.5" /><circle cx="19" cy="6" r="2.5" /><circle cx="12" cy="18" r="2.5" />
      <path d="M7.2 7.3 10.6 16M16.8 7.3 13.4 16M7.5 6h9" />
    </svg>
  );
}
