import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import Graph from "graphology";
import SigmaGraph, { focusNode } from "../components/SigmaGraph.jsx";
import { streamGraphExpand } from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import HoldingsPanel from "../components/HoldingsPanel.jsx";
import { W3FlowOverlay, W3GraphFilters, W3GraphSearch, W3NodeMenu, minValueAt } from "../w3/W3GraphTools.jsx";
import W3TimeSlider from "../w3/W3TimeSlider.jsx";
import { nodesOutOfTime } from "../w3/timeFilter.js";

// The graph explorer on the Investigate page (the dashboard's only graph): grow a graph of thousands of
// addresses step by step around the investigation.
// Load 1 or 2 hops around an address, then double-click any node to expand it. Busy addresses
// (hubs), dust and long tails are grouped into summary nodes; click one to open it.
// The backend streams each fetched address as soon as it is ready (see app/graph/expand.py).
// It starts from the investigation's own graph (every lookup type), expands the looked-up address once,
// and stores every expansion in the investigation result (useInvestigation.mergeExplorerBatch), so the
// evidence tab and the AI analyst see what was found. The result is the record; this graph is the view.

const CHAINS = [
  { id: "bitcoin", label: "Bitcoin", unit: "BTC", placeholder: "bc1q... or 1... or 3..." },
  { id: "ethereum", label: "Ethereum", unit: "ETH", placeholder: "0x followed by 40 hexadecimal characters" },
  { id: "base", label: "Base", unit: "ETH", placeholder: "0x followed by 40 hexadecimal characters" },
  { id: "solana", label: "Solana", unit: "SOL", placeholder: "32 to 44 base58 characters" },
];
const DEFAULT_CAP = 5000;
const WARN_AT = 0.8; // warn when the graph reaches 80% of the cap
const UNPACK_STEP = 100; // members opened per click on a summary node
const LAYOUT_SECONDS = 6; // the layout keeps running this long after the last change

export const KIND_STYLE = {
  seed: { label: "Starting address", color: "#7c3aed" },
  address: { label: "Address (expanded)", color: "#2563eb" },
  frontier: { label: "Address (not expanded yet)", color: "#93b4f5" },
  exchange: { label: "Known exchange (Dune labels)", color: "#be185d" },
  hub: { label: "Busy address: exchange or service?", color: "#0d9488" },
  listed: { label: "On a sanctions or scam list", color: "#dc2626" },
  dust: { label: "Dust and zero-value group", color: "#9ca3af" },
  more: { label: "More counterparties (group)", color: "#d97706" },
  transaction: { label: "Transaction", color: "#8b5cf6" },
  other: { label: "Website, service or payment", color: "#64748b" },
};

function styleKey(attrs) {
  if (attrs.kind === "transaction" || attrs.kind === "other") return attrs.kind;
  if (attrs.listed?.length) return "listed";
  if (attrs.kind === "dust" || attrs.kind === "more") return attrs.kind;
  if (attrs.kind === "exchange") return "exchange";
  if (attrs.kind === "hub") return "hub";
  if (attrs.seed) return "seed";
  return attrs.expanded ? "address" : "frontier";
}

function nodeSize(attrs) {
  if (attrs.kind === "transaction") return 5;
  if (attrs.kind === "other") return 7;
  if (attrs.kind === "dust" || attrs.kind === "more") return 4 + Math.min(8, Math.log2(1 + (attrs.count ?? 1)));
  if (attrs.kind === "hub" || attrs.kind === "exchange") return attrs.expanded || attrs.kind === "hub" ? 12 : 8;
  if (attrs.seed) return 11;
  return attrs.expanded ? 7 : 4;
}

// Apply our attributes plus what Sigma draws with (color, size, label, position).
function drawAttrs({ type, ...attrs }) { // eslint-disable-line no-unused-vars
  const key = styleKey(attrs);
  return { ...attrs, color: KIND_STYLE[key].color, size: nodeSize(attrs), styleKey: key };
}

function placeNear(graph, parentId) {
  const angle = Math.random() * Math.PI * 2;
  if (parentId && graph.hasNode(parentId)) {
    const p = graph.getNodeAttributes(parentId);
    const r = 1 + Math.random() * 2;
    return { x: p.x + Math.cos(angle) * r, y: p.y + Math.sin(angle) * r };
  }
  const r = Math.sqrt(graph.order + 1) * 2;
  return { x: Math.cos(angle) * r, y: Math.sin(angle) * r };
}

function edgeSize(value) {
  return Math.min(6, 0.5 + Math.log10(1 + Math.abs(value ?? 0) * 100));
}

function upsertNode(graph, node, parentId) {
  if (graph.hasNode(node.id)) {
    const old = graph.getNodeAttributes(node.id);
    // Never downgrade: an expanded node stays expanded, a seed stays a seed.
    const merged = { ...old, ...node, expanded: old.expanded || node.expanded, seed: old.seed || node.seed,
      listed: node.listed?.length ? node.listed : old.listed };
    graph.replaceNodeAttributes(node.id, drawAttrs(merged));
    return false;
  }
  graph.addNode(node.id, drawAttrs({ ...node, ...placeNear(graph, parentId) }));
  return true;
}

function upsertEdge(graph, edge) {
  if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) return;
  const attrs = { ...edge, size: edgeSize(edge.value), color: "#cbd5e1" };
  if (graph.hasEdge(edge.id)) {
    const old = graph.getEdgeAttributes(edge.id);
    // The same relationship seen from the other end: keep the larger view of it.
    if ((old.tx_count ?? 0) >= (edge.tx_count ?? 0)) return;
    graph.replaceEdgeAttributes(edge.id, attrs);
  } else if (!graph.hasDirectedEdge(edge.source, edge.target)) {
    graph.addDirectedEdgeWithKey(edge.id, edge.source, edge.target, attrs);
  }
}

function formatAmount(value, unit) {
  if (value == null) return "?";
  const digits = Math.abs(value) >= 1 ? 4 : 8;
  return `${Number(value).toLocaleString(undefined, { maximumFractionDigits: digits })} ${unit}`;
}


const ADDRESS_ID = /^([a-z0-9]+):([^:]+):address:(.+)$/;
const AMOUNT = /([\d,]*\.?\d+)\s*(BTC|ETH|SOL)\b/;

// An investigation node as explorer attributes. Addresses on the four chains can be expanded.
function fromResultNode(node, subjectId) {
  const m = ADDRESS_ID.exec(node.id);
  const listed = node.details?.listed_in ?? node.details?.listed_on ?? [];
  if (node.type === "address" && m) {
    return { id: node.id, kind: "address", chain: m[1], address: m[3], label: node.label, listed,
      expanded: Boolean(node.details?.expanded), seed: node.id === subjectId, details: node.details ?? {} };
  }
  if (node.type === "transaction") {
    return { id: node.id, kind: "transaction", label: node.label, listed, details: node.details ?? {} };
  }
  // Not "type": Sigma reads that attribute as the name of its drawing program and stops rendering on an unknown one.
  return { id: node.id, kind: "other", label: node.label, listed, resultType: node.type, details: node.details ?? {} };
}

// An investigation edge as explorer attributes: amount (for the amount slider) and time (for the timeline).
function fromResultEdge(edge, nodeById) {
  const parsed = AMOUNT.exec(edge.label ?? "");
  const value = edge.value ?? (parsed ? Number(parsed[1].replace(/,/g, "")) : 0);
  const unit = edge.unit ?? parsed?.[2] ?? "";
  const tx = [edge.source, edge.target].map((id) => nodeById.get(id)).find((n) => n?.type === "transaction");
  const when = tx?.details?.block_time ?? null;
  return { id: edge.id, source: edge.source, target: edge.target, value, unit, tx_count: edge.tx_count ?? 1,
    tokens: "", label: edge.label, first_time: edge.first_time ?? when, last_time: edge.last_time ?? when };
}

// Copy into the explorer graph whatever the investigation has that the graph does not (the first time:
// everything; later: older transactions, "Expand node" from the details panel). Returns true if it changed.
function syncFromResult(graph, result, subjectId, removed) {
  let changed = false;
  for (const node of result.graph.nodes) {
    if (graph.hasNode(node.id) || removed.has(node.id)) continue;
    upsertNode(graph, fromResultNode(node, subjectId), null);
    changed = true;
  }
  const nodeById = new Map(result.graph.nodes.map((n) => [n.id, n]));
  for (const edge of result.graph.edges) {
    if (graph.hasEdge(edge.id) || graph.hasDirectedEdge(edge.source, edge.target)) continue;
    upsertEdge(graph, fromResultEdge(edge, nodeById));
    changed = true;
  }
  return changed;
}

function subjectOf(result) {
  const { type, value } = result.input ?? {};
  const chain = CHAINS.find((c) => type === `${c.id}_address`)?.id;
  if (!chain || !value) return null;
  const node = result.graph.nodes.find((n) => n.type === "address" && n.id.startsWith(`${chain}:`)
    && n.id.toLowerCase().endsWith(`:address:${value.toLowerCase()}`));
  return { chain, address: node ? ADDRESS_ID.exec(node.id)[3] : value, id: node?.id ?? null };
}

export default function GraphExplorer({ inv, onNodePick }) {
  const { result } = inv;
  const live = result.data_mode === "live";
  const status = useApi("/api/graph/status");
  const subject = useMemo(() => subjectOf(result), [result.investigation_id]); // eslint-disable-line react-hooks/exhaustive-deps
  const mainChain = subject?.chain ?? (result.graph.nodes.map((n) => ADDRESS_ID.exec(n.id)?.[1]).find((c) => CHAINS.some((x) => x.id === c)) ?? "bitcoin");
  const [cap, setCap] = useState(DEFAULT_CAP);
  const [perNode, setPerNode] = useState(40);

  // One graphology graph per investigation (this component is keyed by it), filled from the result first.
  const [graph] = useState(() => {
    const g = new Graph({ type: "directed" });
    syncFromResult(g, result, subject?.id, new Set());
    return g;
  });
  const sigmaRef = useRef(null);
  const [counts, setCounts] = useState({ nodes: graph.order, edges: graph.size });
  const [selectedId, setSelectedId] = useState(subject?.id ?? "");
  const [selectedVersion, setSelectedVersion] = useState(0);
  const [holdingsFor, setHoldingsFor] = useState("");
  const [running, setRunning] = useState(null); // { label, done, total } while a request streams
  const [messages, setMessages] = useState([]);
  const [lastRun, setLastRun] = useState(null);
  const [layoutOn, setLayoutOn] = useState(true);
  const [layoutPinned, setLayoutPinned] = useState(false); // the analyst paused it
  const abortRef = useRef(null);
  const removedIds = useRef(new Set()); // opened groups: kept in the result, never drawn again
  const layoutTimer = useRef(null);
  const countFrame = useRef(0);
  // Filters, hidden nodes, right-click menu, money-flow particles.
  const [hiddenKinds, setHiddenKinds] = useState(() => new Set());
  const [hiddenNodes, setHiddenNodes] = useState(() => new Set());
  const [minIndex, setMinIndex] = useState(0);
  const [menu, setMenu] = useState(null); // { node, x, y }
  const [flowOn, setFlowOn] = useState(true);
  // Timeline slider: only links that moved money in this period (null = all time).
  const [timeRange, setTimeRange] = useState(null);
  const deferredRange = useDeferredValue(timeRange);
  const timeHidden = useMemo(() => nodesOutOfTime(graph, deferredRange), [graph, deferredRange, counts]); // eslint-disable-line react-hooks/exhaustive-deps
  const view = useMemo(() => ({
    hiddenKinds, hiddenNodes, minValue: minValueAt(minIndex), timeRange: deferredRange, timeHidden,
    onContext: (node, x, y) => { setSelectedId(node); setMenu({ node, x, y }); },
  }), [hiddenKinds, hiddenNodes, minIndex, deferredRange, timeHidden]);

  const maxCap = status.data?.max_nodes ?? 20000;
  const unit = CHAINS.find((c) => c.id === mainChain)?.unit ?? "";
  const nearCap = counts.nodes >= cap * WARN_AT;
  const atCap = counts.nodes >= cap;

  // Re-count at most once a frame, however many nodes a batch adds.
  const recount = useCallback(() => {
    if (countFrame.current) return;
    countFrame.current = requestAnimationFrame(() => {
      countFrame.current = 0;
      setCounts({ nodes: graph.order, edges: graph.size });
      setSelectedVersion((v) => v + 1);
    });
  }, [graph]);

  // Run the layout for a few seconds after each change, unless the analyst paused it.
  const nudgeLayout = useCallback(() => {
    if (layoutPinned) return;
    setLayoutOn(true);
    clearTimeout(layoutTimer.current);
    const seconds = LAYOUT_SECONDS + Math.min(10, graph.order / 1000);
    layoutTimer.current = setTimeout(() => setLayoutOn(false), seconds * 1000);
  }, [layoutPinned, graph]);
  useEffect(() => {
    layoutTimer.current = setTimeout(() => setLayoutOn(false), LAYOUT_SECONDS * 1000);
    return () => {
      clearTimeout(layoutTimer.current);
      if (abortRef.current) { abortRef.current.quiet = true; abortRef.current.abort(); }
    };
  }, []);

  // Investigate tells Investigate which node is selected (its risk and details panels follow).
  useEffect(() => { onNodePick?.(selectedId); }, [selectedId]); // eslint-disable-line react-hooks/exhaustive-deps

  // The investigation grew from elsewhere (older transactions, "Expand node" in the details): show it here too.
  useEffect(() => {
    if (syncFromResult(graph, result, subject?.id, removedIds.current)) { recount(); nudgeLayout(); }
  }, [result.graph]); // eslint-disable-line react-hooks/exhaustive-deps

  const note = (kind, text) => setMessages((old) => [{ kind, text, at: Date.now() }, ...old].slice(0, 6));

  // Stream one expand request into the graph, and into the investigation result.
  const expand = useCallback(async (targets, hopCount, label, chain = mainChain) => {
    if (!live) {
      note("info", "Demo results cannot be expanded. Look up a real address to grow the graph.");
      return;
    }
    const room = cap - graph.order;
    if (room <= 0) {
      note("warn", `The graph is at its ${cap.toLocaleString()}-node cap. Raise the cap first.`);
      return;
    }
    if (abortRef.current) { abortRef.current.quiet = true; abortRef.current.abort(); } // the new request replaces it
    const controller = new AbortController();
    abortRef.current = controller;
    setRunning({ label, done: 0, total: targets.length });
    let summary = null;
    try {
      await streamGraphExpand(
        { chain, addresses: targets, hops: hopCount, max_new_nodes: room, per_node_limit: perNode },
        {
          signal: controller.signal,
          onEvent: (event) => {
            if (event.event === "progress") setRunning((r) => r && { ...r, label: `${label}: hop ${event.hop}`, done: 0, total: event.fetching });
            if (event.event === "batch") {
              const center = event.expanded;
              upsertNode(graph, center, null);
              for (const node of event.nodes) {
                if (graph.order >= cap && !graph.hasNode(node.id)) break; // hard stop at the cap
                upsertNode(graph, node, node.parent ?? center.id);
              }
              for (const edge of event.edges) upsertEdge(graph, edge);
              // Store only what the graph really shows (the cap may have cut the batch short).
              const nodes = event.nodes.filter((n) => graph.hasNode(n.id));
              const edges = event.edges.filter((e) => graph.hasNode(e.source) && graph.hasNode(e.target));
              inv.mergeExplorerBatch({ ...event, nodes, edges }, chain, result.investigation_id);
              setRunning((r) => r && { ...r, done: event.progress.done, total: event.progress.total });
              recount();
              nudgeLayout();
            }
            if (event.event === "warning") note("warn", event.message);
            if (event.event === "error") note("error", event.message);
            if (event.event === "done") summary = event;
          },
        },
      );
      if (summary) {
        setLastRun(summary);
        if (summary.stopped === "node_limit") {
          note("warn", `Stopped at the ${cap.toLocaleString()}-node cap.`);
          inv.addLimitation(`The graph explorer stopped at its ${cap.toLocaleString()}-node cap; some counterparties were not added.`);
        }
        if (summary.stopped === "fetch_cap") inv.addLimitation("The graph explorer read the most addresses one request allows; further hops were not loaded.");
        if (summary.stopped === "budget") inv.addLimitation("The graph explorer stopped at the daily Alchemy allowance.");
      }
    } catch (err) {
      if (err.name !== "AbortError") note("error", err.message);
      else if (!controller.quiet) note("info", "Stopped.");
    } finally {
      inv.flushExplorer();
      if (abortRef.current === controller) {
        abortRef.current = null;
        setRunning(null);
      }
      status.reload();
    }
  }, [cap, graph, mainChain, perNode, recount, nudgeLayout, status, live, inv, result.investigation_id]);

  // First visit of this investigation: expand the looked-up address (and any extra start addresses) one hop.
  const autoLoaded = useRef(false);
  useEffect(() => {
    if (autoLoaded.current || !live || !subject) return;
    autoLoaded.current = true;
    const already = result.graph.edges.some((e) => e.id.startsWith("expand:"));
    const extras = inv.explorerExtras?.first?.toLowerCase() === subject.address.toLowerCase() && inv.explorerExtras.chain === subject.chain
      ? inv.explorerExtras.addresses : [];
    if (already && !extras.length) return;
    const starts = [subject.address, ...extras];
    for (const a of extras) {
      const id = `${subject.chain}:mainnet:address:${a}`;
      if (graph.hasNode(id)) graph.mergeNodeAttributes(id, { seed: true });
    }
    let cancelled = false;
    expand(starts, 1, `Loading counterparties${starts.length > 1 ? ` of ${starts.length} addresses` : ""}`, subject.chain).then(() => {
      if (cancelled) return;
      for (const a of starts) {
        const id = [...graph.nodes()].find((n) => graph.getNodeAttribute(n, "address")?.toLowerCase() === a.toLowerCase());
        if (id) { graph.mergeNodeAttributes(id, { seed: true }); graph.replaceNodeAttributes(id, drawAttrs(graph.getNodeAttributes(id))); }
      }
      recount();
    });
    // React's development mode mounts twice: the first request is stopped quietly and this runs again.
    return () => { cancelled = true; autoLoaded.current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function expandNode(id, hopCount = 1) {
    if (!graph.hasNode(id)) return;
    const attrs = graph.getNodeAttributes(id);
    if (attrs.kind === "dust" || attrs.kind === "more") return unpack(id);
    if (!attrs.address || !CHAINS.some((c) => c.id === attrs.chain)) return;
    expand([attrs.address], hopCount, `Expanding ${attrs.label}`, attrs.chain);
  }

  // Expand every address of the main chain not expanded yet (hubs and groups excepted).
  function expandFrontier() {
    const open = (a) => a.kind === "address" && a.chain === mainChain && !a.expanded;
    const listed = graph.filterNodes((_, a) => open(a) && a.listed?.length);
    const frontier = graph.filterNodes((_, a) => open(a) && !a.listed?.length);
    const targets = [...listed, ...frontier].slice(0, 100).map((id) => graph.getNodeAttribute(id, "address"));
    if (!targets.length) {
      note("info", "Every address in the graph is already expanded.");
      return;
    }
    expand(targets, 1, `Expanding ${targets.length} addresses`);
  }

  // Open a summary node: its members become ordinary nodes, UNPACK_STEP at a time. No API call,
  // but they are stored in the investigation like any expansion.
  function unpack(id) {
    const s = graph.getNodeAttributes(id);
    const room = cap - graph.order + 1;
    const take = (s.members ?? []).slice(0, Math.min(UNPACK_STEP, room));
    if (!take.length) {
      note("warn", s.members ? `The graph is at its ${cap.toLocaleString()}-node cap.` : "This group's members are not loaded any more. Expand its address again to open it.");
      return;
    }
    const chain = s.chain ?? mainChain;
    const groupUnit = CHAINS.find((c) => c.id === chain)?.unit ?? unit;
    const batchNodes = [];
    const batchEdges = [];
    for (const m of take) {
      const mid = `${chain}:mainnet:address:${m.address}`;
      const node = { id: mid, kind: "address", chain, address: m.address,
        label: `${m.address.slice(0, 6)}...${m.address.slice(-6)}`, expanded: false, listed: [] };
      upsertNode(graph, node, id);
      batchNodes.push(node);
      const tokens = Object.entries(m.tokens ?? {}).map(([k, n]) => `${k} x${n}`).join(", ");
      const times = { first_time: m.first_time ?? null, last_time: m.last_time ?? null };
      const edges = [];
      if (m.sent) edges.push({ id: `${s.parent}>${mid}`, source: s.parent, target: mid, value: m.sent, tx_count: m.tx_count, unit: groupUnit, tokens, ...times });
      if (m.received || !m.sent) edges.push({ id: `${mid}>${s.parent}`, source: mid, target: s.parent, value: m.received, tx_count: m.tx_count, unit: groupUnit, tokens, ...times });
      for (const e of edges) { upsertEdge(graph, e); batchEdges.push(e); }
    }
    if (live && graph.hasNode(s.parent)) {
      const p = graph.getNodeAttributes(s.parent);
      inv.mergeExplorerBatch({ expanded: { id: s.parent, address: p.address, label: p.label, counterparties: p.counterparties ?? take.length,
        tx_seen: p.tx_seen ?? 0, source: p.source ?? "opened group", note: `Opened ${take.length} from the ${s.kind === "dust" ? "dust" : "more"} group` },
        nodes: batchNodes, edges: batchEdges }, chain, result.investigation_id);
    }
    const rest = s.members.slice(take.length);
    if (rest.length) {
      graph.replaceNodeAttributes(id, drawAttrs({ ...s, members: rest, count: s.count - take.length,
        label: `${(s.count - take.length).toLocaleString()} ${s.kind === "dust" ? "dust or zero-value" : "more"}` }));
    } else {
      graph.dropNode(id);
      removedIds.current.add(id);
      setSelectedId(s.parent);
    }
    recount();
    nudgeLayout();
  }

  function stop() {
    abortRef.current?.abort();
  }

  function downloadJson() {
    const blob = new Blob([JSON.stringify(graph.export())], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${result.investigation_id}-graph.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  const kindCounts = useMemo(() => {
    const out = {};
    graph.forEachNode((_, a) => { out[a.styleKey] = (out[a.styleKey] ?? 0) + 1; });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, counts]);

  const budget = status.data;
  const shown = result.uncertainty?.transactions_shown;
  const total = result.uncertainty?.transactions_total;

  return (
    <section className="card inv-explorer" aria-label="Graph explorer">
      <div className="inv-explorer-form">
        <div className="graph-header grow">
          <h2>
            Graph explorer: {counts.nodes.toLocaleString()} nodes, {counts.edges.toLocaleString()} links
            <span className="small muted"> · cap {cap.toLocaleString()}</span>
          </h2>
          {atCap ? <span className="pill pill-down">Node cap reached</span>
            : nearCap ? <span className="pill pill-warn">Near the node cap ({Math.round((counts.nodes / cap) * 100)}%)</span> : null}
        </div>
        <label className="field">
          <span className="field-label" title="Counterparties drawn per address before the rest are grouped">Per address: {perNode}</span>
          <input type="range" min={10} max={200} step={10} value={perNode} onChange={(e) => setPerNode(Number(e.target.value))} />
        </label>
        <label className="field">
          <span className="field-label">Node cap</span>
          <input type="number" min={100} max={maxCap} step={500} value={cap} className="inv-cap-input"
            onChange={(e) => setCap(Math.max(100, Math.min(maxCap, Number(e.target.value) || DEFAULT_CAP)))} />
        </label>
        {budget && (
          <span className="data-badge" title="Graph mode's own daily share of the Alchemy free plan. Bitcoin and Etherscan lookups do not use it.">
            Alchemy today: {budget.alchemy_cu_used_today.toLocaleString()} / {budget.alchemy_cu_daily_limit.toLocaleString()} CU
          </span>
        )}
      </div>
      {counts.nodes > 0 && (
        <div className="cap-meter" aria-hidden="true">
          <span style={{ width: `${Math.min(100, (counts.nodes / cap) * 100)}%` }} className={atCap ? "full-bar" : nearCap ? "warn-bar" : ""} />
        </div>
      )}

      <div className="inv-explorer-body">
        <div className="explorer-main" aria-label="Graph">
          <div className="graph-toolbar">
            <W3GraphSearch graph={graph} version={counts.nodes} onPick={(id) => { setSelectedId(id); focusNode(sigmaRef.current, id); }} />
            <div className="button-group">
              {running ? (
                <button type="button" className="secondary" onClick={stop}>Stop</button>
              ) : (
                <button type="button" className="secondary" onClick={expandFrontier} disabled={!live || !counts.nodes || atCap}
                  title="Expand every address not expanded yet (up to the per-request limit)">Expand all</button>
              )}
              <button type="button" className="secondary" onClick={() => sigmaRef.current?.getCamera().animatedReset()} disabled={!counts.nodes}>Fit</button>
              <button type="button" className="secondary" onClick={() => { setLayoutPinned(layoutOn); setLayoutOn(!layoutOn); }} disabled={!counts.nodes}
                title="ForceAtlas2 runs in a background thread">{layoutOn ? "Pause layout" : "Run layout"}</button>
              <button type="button" className={`secondary ${flowOn ? "w3-on" : ""}`} onClick={() => setFlowOn((f) => !f)} disabled={!counts.nodes}
                aria-pressed={flowOn} title="Moving dots show which way money flows along each link">Money flow</button>
              <button type="button" className="secondary" onClick={downloadJson} disabled={!counts.nodes} title="Download the graph (graphology JSON)">JSON</button>
            </div>
          </div>

          {running && (
            <div className="notice small" role="status" aria-live="polite">
              <span className="spinner small-spinner" aria-hidden="true" />
              {running.label}{running.total ? ` (${running.done} of ${running.total} addresses)` : "..."}
            </div>
          )}
          {messages.map((m) => (
            <p key={m.at + m.text} className={`notice small notice-${m.kind}`}>{m.text}</p>
          ))}

          <div className="sigma-wrap">
            <SigmaGraph graph={graph} selectedId={selectedId} onSelect={setSelectedId}
              onExpand={(id) => expandNode(id)} layoutOn={layoutOn} sigmaRef={sigmaRef} view={view} />
            <W3FlowOverlay sigmaRef={sigmaRef} graph={graph} focusId={selectedId} view={view} on={flowOn && counts.nodes > 0} />
          </div>
          <W3NodeMenu menu={menu} graph={graph} chain={graph.hasNode(menu?.node ?? "") ? graph.getNodeAttribute(menu.node, "chain") ?? mainChain : mainChain}
            onClose={() => setMenu(null)}
            onExpand={expandNode}
            onSelectFocus={(id) => { setSelectedId(id); focusNode(sigmaRef.current, id); }}
            onHide={(id) => { setHiddenNodes((old) => new Set(old).add(id)); if (selectedId === id) setSelectedId(""); }}
            onTogglePin={(id) => { graph.setNodeAttribute(id, "fixed", !graph.getNodeAttribute(id, "fixed")); recount(); }} />

          <W3TimeSlider graph={graph} version={counts.edges} range={timeRange} onRange={setTimeRange} />
          <W3GraphFilters kinds={KIND_STYLE} counts={kindCounts} hiddenKinds={hiddenKinds} unit={unit}
            onToggleKind={(key) => setHiddenKinds((old) => { const next = new Set(old); next.has(key) ? next.delete(key) : next.add(key); return next; })}
            minIndex={minIndex} onMinIndex={setMinIndex} hiddenNodes={hiddenNodes} onShowHidden={() => setHiddenNodes(new Set())} />
          {inv.canLoadOlder || inv.older ? (
            <div className="inv-older">
              {inv.canLoadOlder && (
                <button type="button" className="secondary" onClick={inv.loadOlder} disabled={inv.older?.status === "loading"}
                  title={{ bitcoin: "Read the next 100 transactions of the looked-up address (4 pages of 25)",
                    solana: "Read the next older Solana transactions in full (Alchemy compute units per transaction)" }[mainChain]
                    ?? "Read the activity older than the oldest block in the graph (Etherscan, or Alchemy as backup)"}>
                  {inv.older?.status === "loading" ? "Loading older transactions..." : "Load older transactions"}
                </button>
              )}
              <span className="small muted">
                {shown != null && total != null ? `${shown.toLocaleString()} of ${total.toLocaleString()} transactions loaded. `
                  : result.uncertainty?.older_transactions_loaded ? `${result.uncertainty.older_transactions_loaded.toLocaleString()} older transactions loaded. ` : ""}
                {inv.older?.text ?? ""}
              </span>
            </div>
          ) : null}
          <p className="small muted hint">
            Scroll to zoom, drag to pan. Click a node for details, double-click an address to expand it (or open a group).
            Hover a node to light up its links, right-click it for more actions. Everything you expand is saved with this
            investigation, so the evidence tab and the AI analyst see it.
            {lastRun && ` Last request: ${lastRun.fetched} fetched, ${lastRun.from_cache} from cache, ${lastRun.seconds} s` +
              (lastRun.alchemy_cu ? `, ${lastRun.alchemy_cu.toLocaleString()} Alchemy CU.` : ".")}
          </p>
        </div>

        <NodeDetails key={selectedId + selectedVersion} graph={graph} id={selectedId} unit={unit} running={Boolean(running) || !live} atCap={atCap}
          labelCount={status.data?.exchange_labels?.addresses ?? 0}
          onExpand={expandNode} onSelect={(id) => { setSelectedId(id); focusNode(sigmaRef.current, id); }}
          chain={graph.hasNode(selectedId) ? graph.getNodeAttribute(selectedId, "chain") ?? mainChain : mainChain}
          holdingsOpen={holdingsFor === selectedId} onHoldings={() => setHoldingsFor(selectedId)} />
      </div>
    </section>
  );
}

function holdingsNodeId(chain, address) {
  if (!address) return "";
  if (chain === "ethereum" || chain === "base") return /^0x[0-9a-fA-F]{40}$/.test(address) ? `${chain}:mainnet:address:${address.toLowerCase()}` : "";
  if (chain === "solana") return `solana:mainnet:address:${address}`;
  return "";
}

function NodeDetails({ graph, id, unit, running, atCap, onExpand, onSelect, labelCount, chain, holdingsOpen, onHoldings }) {
  if (!id || !graph.hasNode(id)) {
    return (
      <aside className="card details">
        <h2>Details</h2>
        <p className="muted small">Click a node to see it here.</p>
        <ul className="small muted plain-list">
          <li><strong>Exchanges</strong> are named from Dune's community exchange labels{labelCount ? ` (${labelCount.toLocaleString()} addresses loaded)` : " (none loaded yet: set DUNE_API_KEY)"}. Always drawn on their own, never expanded automatically.</li>
          <li><strong>Hubs</strong> are busy addresses (many counterparties, or a full page of history within a day). Exchanges and services look like this; they are not expanded automatically.</li>
          <li><strong>Groups</strong> hold dust and the smaller counterparties of a node. Opening one costs no API call.</li>
          <li>Addresses on a sanctions or scam list are always shown on their own.</li>
        </ul>
      </aside>
    );
  }
  const a = graph.getNodeAttributes(id);
  if (a.kind === "transaction" || a.kind === "other" || !a.address) return <PlainDetails a={a} degree={graph.degree(id)} />;
  const isGroup = a.kind === "dust" || a.kind === "more";
  const inEdges = graph.inEdges(id).map((e) => graph.getEdgeAttributes(e));
  const outEdges = graph.outEdges(id).map((e) => graph.getEdgeAttributes(e));
  const sum = (edges) => edges.reduce((t, e) => t + (e.value ?? 0), 0);

  return (
    <aside className="card details" aria-label="Node details">
      <h2>{KIND_STYLE[a.styleKey]?.label ?? "Node"}</h2>
      {isGroup ? (
        <>
          <p className="small">{a.count.toLocaleString()} counterparties of{" "}
            <button type="button" className="link" onClick={() => onSelect(a.parent)}>{graph.hasNode(a.parent) ? graph.getNodeAttribute(a.parent, "label") : "its parent"}</button>
            {a.kind === "dust" ? ", each with less than a dust amount and no token value (often address poisoning or spam)." : ", smaller than the ones drawn."}
          </p>
          <dl className="grid-dl small">
            <dt>Sent to them</dt><dd>{formatAmount(sum(inEdges), unit)}</dd>
            <dt>Received from them</dt><dd>{formatAmount(sum(outEdges), unit)}</dd>
          </dl>
          <button type="button" className="primary" disabled={atCap} onClick={() => onExpand(id)}>
            Open {Math.min(UNPACK_STEP, a.members.length)} of {a.count.toLocaleString()}
          </button>
          {a.members_cut > 0 && <p className="small muted">The backend sent the first 2,000 members; {a.members_cut.toLocaleString()} more were left out.</p>}
          <ul className="small plain-list member-list">
            {a.members.slice(0, 20).map((m) => (
              <li key={m.address} className="mono">{m.address.slice(0, 10)}... {formatAmount(m.sent + m.received, unit)}, {m.tx_count} tx</li>
            ))}
          </ul>
        </>
      ) : (
        <>
          <p className="mono small">{a.address}</p>
          {a.listed?.length > 0 && <p className="notice notice-error small">Listed: {a.listed.join(", ")}</p>}
          {a.exchange && (
            <p className="notice small">
              <strong>{a.exchange.exchange}</strong>{a.exchange.name && a.exchange.name !== a.exchange.exchange ? ` (${a.exchange.name})` : ""}:
              listed in Dune's community exchange labels{a.exchange.added_by ? `, added by ${a.exchange.added_by}` : ""}
              {a.exchange.added_date ? ` on ${a.exchange.added_date}` : ""}. A community label, not proof. Exchanges are not expanded automatically.
            </p>
          )}
          {a.hub_reason && !a.exchange && <p className="notice notice-warn small">Busy address: {a.hub_reason}. Its counterparties are mostly its customers, so it is not expanded further automatically.</p>}
          <dl className="grid-dl small">
            <dt>Links</dt><dd>{graph.degree(id).toLocaleString()}</dd>
            <dt>Received</dt><dd>{formatAmount(sum(inEdges), unit)} ({inEdges.length} in)</dd>
            <dt>Sent</dt><dd>{formatAmount(sum(outEdges), unit)} ({outEdges.length} out)</dd>
            {a.expanded && (<><dt>History read</dt><dd>{a.tx_seen?.toLocaleString()} records, {a.counterparties?.toLocaleString()} counterparties</dd></>)}
            {a.source && (<><dt>Source</dt><dd>{a.source}</dd></>)}
          </dl>
          {a.note && <p className="small muted">{a.note}</p>}
          <div className="form-actions">
            <button type="button" className="primary" disabled={running || atCap} onClick={() => onExpand(id, 1)}>
              {a.expanded ? "Expand again" : "Expand"}
            </button>
            <button type="button" className="secondary" disabled={running || atCap} onClick={() => onExpand(id, 2)}>2 hops</button>
          </div>
          <p className="small muted">Amounts are only what the loaded history shows between these addresses, not their full balances.</p>
          {holdingsNodeId(chain, a.address) && (
            <div className="w3-holdings">
              {holdingsOpen ? <HoldingsPanel nodeId={holdingsNodeId(chain, a.address)} /> : (
                <>
                  <button type="button" className="secondary" onClick={onHoldings}>Load wallet holdings (tokens, then NFTs)</button>
                  <p className="small muted">Live balances from Alchemy. Uses compute units, so they load only when asked.</p>
                </>
              )}
            </div>
          )}
        </>
      )}
    </aside>
  );
}

// A transaction, website, service or payment from the lookup: what the lookup said about it.
function PlainDetails({ a, degree }) {
  const rows = Object.entries(a.details ?? {}).filter(([, v]) => v != null && typeof v !== "object").slice(0, 12);
  return (
    <aside className="card details" aria-label="Node details">
      <h2>{KIND_STYLE[a.styleKey]?.label ?? "Node"}</h2>
      <p className="mono small">{a.label}</p>
      {a.listed?.length > 0 && <p className="notice notice-error small">Listed: {a.listed.join(", ")}</p>}
      <dl className="grid-dl small">
        <dt>Links</dt><dd>{degree.toLocaleString()}</dd>
        {rows.flatMap(([k, v]) => [<dt key={`k${k}`}>{k.replace(/_/g, " ")}</dt>, <dd key={`v${k}`}>{String(v)}</dd>])}
      </dl>
      <p className="small muted">Only addresses can be expanded. The panel on the right has this item's full details and actions.</p>
    </aside>
  );
}
