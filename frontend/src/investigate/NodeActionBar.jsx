import { useState } from "react";
import { itemFromNode } from "../cases/caseItems.js";
import { AddToCaseButton } from "../components/AddToCaseMenu.jsx";
import { nodeTypeInfo } from "../config/graphStyle.js";
import { bitcoinAddressOf } from "../services/modelScore.js";

// Investigate graph: a floating bar of actions for the selected node, so the next step is one click away:
// investigate it (pivot), expand its counterparties, score it with the model, copy it, add it to a case,
// or open it in the graph explorer.
export default function NodeActionBar({ result, selectedId, inv, onSelect }) {
  const [copied, setCopied] = useState(false);
  const node = result.graph.nodes.find((n) => n.id === selectedId);
  if (!node) return null;
  const item = itemFromNode(node);
  const pivot = pivotOf(item);
  const isSubject = pivot && result.input && pivot.inputType === result.input.type
    && pivot.value.toLowerCase() === String(result.input.value).toLowerCase();
  const btc = bitcoinAddressOf(node.id);
  const score = btc ? inv.scoreOf(btc) : null;
  const graphChain = item?.item_type === "address" && ["bitcoin", "ethereum", "base", "solana"].includes(item.network) ? item.network : null;
  const info = nodeTypeInfo(node.type);

  async function copy() {
    try {
      await navigator.clipboard.writeText(item?.value ?? node.id);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      // clipboard blocked; the value is in the details panel
    }
  }

  return (
    <div className="inv-actionbar" role="toolbar" aria-label={`Actions for ${node.label}`}>
      <span className="inv-actionbar-label" title={node.id}>
        <span className="swatch" style={{ background: info.color }} />
        {node.label}
      </span>
      {pivot && !isSubject && (
        <button type="button" className="inv-act primary-act" onClick={() => inv.investigateLive(pivot.inputType, pivot.value, pivot.mode, pivot.method)}
          title="Run a new investigation on this item">
          <ActIcon d="M11 4a7 7 0 1 0 4.9 12l4.6 4.6M11 8v6M8 11h6" /> Investigate
        </button>
      )}
      {inv.canExpand(node.id) && (
        <button type="button" className="inv-act" onClick={() => inv.expandNode(node.id)} disabled={Boolean(inv.expandingId)}
          title="Fetch this address's counterparties and add them to the graph">
          <ActIcon d="M12 5v14M5 12h14" /> {inv.expandingId === node.id ? "Expanding..." : inv.expandedIds.has(node.id) ? "Expand again" : "Expand"}
        </button>
      )}
      {btc && (
        <button type="button" className="inv-act" onClick={() => inv.scoreAddress(btc, inv.modelFamily, { force: score?.status === "error" })}
          disabled={score?.status === "loading" || score?.status === "done"} title="Live model score for this address">
          <ActIcon d="M4 19h16M7 16V9M12 16V5M17 16v-4" />
          {score?.status === "loading" ? "Scoring..." : score?.status === "done" ? `Score ${score.data.score.toFixed(2)}` : "Score"}
        </button>
      )}
      <button type="button" className="inv-act" onClick={copy} title="Copy to the clipboard">
        <ActIcon d="M9 9h10v10H9zM5 15V5h10" /> {copied ? "Copied" : "Copy"}
      </button>
      {graphChain && (
        <a className="inv-act" href={`#/graph/${graphChain}/${encodeURIComponent(item.value)}`} title="Open in the graph explorer (thousands of nodes)">
          <ActIcon d="M6 6m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0M18 8m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0M12 18m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0M8 7l8 1M7 8l4 8M17 10l-4 6" /> Explorer
        </a>
      )}
      {item && <AddToCaseButton item={item} />}
      <button type="button" className="inv-act inv-act-close" onClick={() => onSelect("")} title="Clear the selection (Esc)" aria-label="Clear the selection">×</button>
    </div>
  );
}

// What a new investigation on this item would run.
export function pivotOf(item) {
  if (!item) return null;
  const chain = item.network;
  if (["bitcoin", "ethereum", "base", "solana"].includes(chain)) {
    if (item.item_type === "address") return { inputType: `${chain}_address`, value: item.value, mode: "crypto_investigation", method: "live_lookup" };
    if (item.item_type === "transaction") return { inputType: `${chain}_txid`, value: item.value, mode: "crypto_investigation", method: "live_lookup" };
  }
  if (item.item_type === "domain") return { inputType: "url_domain", value: item.value, mode: "scam_early_warning", method: "rules_v1" };
  return null;
}

function ActIcon({ d }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}
