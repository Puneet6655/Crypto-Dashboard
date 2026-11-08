import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Graph from "graphology";
import PageHeader from "../components/PageHeader.jsx";
import SigmaGraph, { focusNode } from "../components/SigmaGraph.jsx";
import { streamGraphExpand } from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import { navigate } from "../hooks/useHashRoute.js";

// Advanced graph mode: grow a graph of thousands of addresses step by step.
// Load 1 or 2 hops around an address, then double-click any node to expand it. Busy addresses
// (hubs), dust and long tails are grouped into summary nodes; click one to open it.
// The backend streams each fetched address as soon as it is ready (see app/graph/expand.py).

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
};

function styleKey(attrs) {
  if (attrs.listed?.length) return "listed";
  if (attrs.kind === "dust" || attrs.kind === "more") return attrs.kind;
  if (attrs.kind === "exchange") return "exchange";
  if (attrs.kind === "hub") return "hub";
  if (attrs.seed) return "seed";
  return attrs.expanded ? "address" : "frontier";
}

function nodeSize(attrs) {
  if (attrs.kind === "dust" || attrs.kind === "more") return 4 + Math.min(8, Math.log2(1 + (attrs.count ?? 1)));
  if (attrs.kind === "hub" || attrs.kind === "exchange") return attrs.expanded || attrs.kind === "hub" ? 12 : 8;
  if (attrs.seed) return 11;
  return attrs.expanded ? 7 : 4;
}

// Apply our attributes plus what Sigma draws with (color, size, label, position).
function drawAttrs(attrs) {
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

export default function GraphExplorerPage({ route }) {
  const status = useApi("/api/graph/status");
  const [chain, setChain] = useState(() => (CHAINS.some((c) => c.id === route.params[0]) ? route.params[0] : "bitcoin"));
  const [address, setAddress] = useState(() => decodeURIComponent(route.params[1] ?? ""));
  const [hops, setHops] = useState(1);
  const [cap, setCap] = useState(DEFAULT_CAP);
  const [perNode, setPerNode] = useState(40);

  // One graphology graph per exploration. A new object = a fresh canvas.
  const [graph, setGraph] = useState(() => new Graph({ type: "directed" }));
  const [graphChain, setGraphChain] = useState(chain);
  const sigmaRef = useRef(null);
  // Callbacks read the graph from here, so a request still streaming into a just-created graph
  // counts that graph and not the one from the previous render.
  const graphRef = useRef(graph);
  graphRef.current = graph;
  const [counts, setCounts] = useState({ nodes: 0, edges: 0 });
  const [selectedId, setSelectedId] = useState("");
  const [selectedVersion, setSelectedVersion] = useState(0);
  const [running, setRunning] = useState(null); // { label, done, total } while a request streams
  const [messages, setMessages] = useState([]);
  const [lastRun, setLastRun] = useState(null);
  const [layoutOn, setLayoutOn] = useState(false);
  const [layoutPinned, setLayoutPinned] = useState(false); // the analyst paused it
  const [search, setSearch] = useState("");
  const abortRef = useRef(null);
  const layoutTimer = useRef(null);
  const countFrame = useRef(0);

  const maxCap = status.data?.max_nodes ?? 20000;
  const unit = CHAINS.find((c) => c.id === graphChain)?.unit ?? "";
  const nearCap = counts.nodes >= cap * WARN_AT;
  const atCap = counts.nodes >= cap;

  // Re-count at most once a frame, however many nodes a batch adds.
  const recount = useCallback(() => {
    if (countFrame.current) return;
    countFrame.current = requestAnimationFrame(() => {
      countFrame.current = 0;
      setCounts({ nodes: graphRef.current.order, edges: graphRef.current.size });
      setSelectedVersion((v) => v + 1);
    });
  }, []);

  // Run the layout for a few seconds after each change, unless the analyst paused it.
  const nudgeLayout = useCallback(() => {
    if (layoutPinned) return;
    setLayoutOn(true);
    clearTimeout(layoutTimer.current);
    const seconds = LAYOUT_SECONDS + Math.min(10, graphRef.current.order / 1000);
    layoutTimer.current = setTimeout(() => setLayoutOn(false), seconds * 1000);
  }, [layoutPinned]);
  useEffect(() => () => clearTimeout(layoutTimer.current), []);

  const note = (kind, text) => setMessages((old) => [{ kind, text, at: Date.now() }, ...old].slice(0, 6));

  // Stream one expand request into the graph.
  const expand = useCallback(async (targets, hopCount, label, onGraph = graph, onChain = graphChain) => {
    const room = cap - onGraph.order;
    if (room <= 0) {
      note("warn", `The graph is at its ${cap.toLocaleString()}-node cap. Raise the cap or clear the graph first.`);
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setRunning({ label, done: 0, total: targets.length });
    let summary = null;
    try {
      await streamGraphExpand(
        { chain: onChain, addresses: targets, hops: hopCount, max_new_nodes: room, per_node_limit: perNode },
        {
          signal: controller.signal,
          onEvent: (event) => {
            if (event.event === "progress") setRunning((r) => r && { ...r, label: `${label}: hop ${event.hop}`, done: 0, total: event.fetching });
            if (event.event === "batch") {
              const center = event.expanded;
              upsertNode(onGraph, center, null);
              for (const node of event.nodes) {
                if (onGraph.order >= cap && !onGraph.hasNode(node.id)) break; // hard stop at the cap
                upsertNode(onGraph, node, node.parent ?? center.id);
              }
              for (const edge of event.edges) upsertEdge(onGraph, edge);
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
        if (summary.stopped === "node_limit") note("warn", `Stopped at the ${cap.toLocaleString()}-node cap.`);
      }
    } catch (err) {
      if (err.name !== "AbortError") note("error", err.message);
      else note("info", "Stopped.");
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        setRunning(null);
      }
      status.reload();
    }
  }, [cap, graph, graphChain, perNode, recount, nudgeLayout, status]);

  // Start a new exploration from the address in the form.
  function load(event) {
    event?.preventDefault();
    const value = address.trim();
    if (!value) return;
    abortRef.current?.abort();
    const fresh = new Graph({ type: "directed" });
    graphRef.current = fresh;
    setGraph(fresh);
    setGraphChain(chain);
    setSelectedId("");
    setMessages([]);
    setLastRun(null);
    setCounts({ nodes: 0, edges: 0 });
    // Remember the start in the address bar, so a reload or a shared link opens the same graph.
    navigate(`/graph/${chain}/${encodeURIComponent(value)}`);
    expand([value], hops, `Loading ${hops} hop${hops > 1 ? "s" : ""}`, fresh, chain).then(() => {
      const seedId = [...fresh.nodes()].find((id) => fresh.getNodeAttribute(id, "address")?.toLowerCase() === value.toLowerCase());
      if (seedId) {
        fresh.mergeNodeAttributes(seedId, { seed: true });
        fresh.replaceNodeAttributes(seedId, drawAttrs(fresh.getNodeAttributes(seedId)));
        setSelectedId(seedId);
      }
    });
  }

  // Open a link like #/graph/bitcoin/<address> straight away (once).
  const autoLoaded = useRef(false);
  useEffect(() => {
    if (autoLoaded.current || !route.params[1]) return;
    autoLoaded.current = true;
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function expandNode(id, hopCount = 1) {
    if (!graph.hasNode(id)) return;
    const attrs = graph.getNodeAttributes(id);
    if (attrs.kind === "dust" || attrs.kind === "more") return unpack(id);
    if (!attrs.address) return;
    expand([attrs.address], hopCount, `Expanding ${attrs.label}`);
  }

  // Expand every address that has not been expanded yet (hubs and groups excepted).
  function expandFrontier() {
    const frontier = graph.filterNodes((_, a) => a.kind === "address" && !a.expanded && !a.listed?.length);
    const listed = graph.filterNodes((_, a) => a.kind === "address" && !a.expanded && a.listed?.length);
    const targets = [...listed, ...frontier].slice(0, 100).map((id) => graph.getNodeAttribute(id, "address"));
    if (!targets.length) {
      note("info", "Every address in the graph is already expanded.");
      return;
    }
    expand(targets, 1, `Expanding ${targets.length} addresses`);
  }

  // Open a summary node: its members become ordinary nodes, UNPACK_STEP at a time. No API call.
  function unpack(id) {
    const s = graph.getNodeAttributes(id);
    const room = cap - graph.order + 1;
    const take = s.members.slice(0, Math.min(UNPACK_STEP, room));
    if (!take.length) {
      note("warn", `The graph is at its ${cap.toLocaleString()}-node cap.`);
      return;
    }
    for (const m of take) {
      const mid = `${graphChain}:mainnet:address:${m.address}`;
      upsertNode(graph, { id: mid, kind: "address", chain: graphChain, address: m.address,
        label: `${m.address.slice(0, 6)}...${m.address.slice(-6)}`, expanded: false, listed: [] }, id);
      const tokens = Object.entries(m.tokens ?? {}).map(([k, n]) => `${k} x${n}`).join(", ");
      if (m.sent) upsertEdge(graph, { id: `${s.parent}>${mid}`, source: s.parent, target: mid, value: m.sent, tx_count: m.tx_count, unit, tokens });
      if (m.received || !m.sent) upsertEdge(graph, { id: `${mid}>${s.parent}`, source: mid, target: s.parent, value: m.received, tx_count: m.tx_count, unit, tokens });
    }
    const rest = s.members.slice(take.length);
    if (rest.length) {
      graph.replaceNodeAttributes(id, drawAttrs({ ...s, members: rest, count: s.count - take.length,
        label: `${(s.count - take.length).toLocaleString()} ${s.kind === "dust" ? "dust or zero-value" : "more"}` }));
    } else {
      graph.dropNode(id);
      setSelectedId(s.parent);
    }
    recount();
    nudgeLayout();
  }

  function stop() {
    abortRef.current?.abort();
  }

  function clear() {
    stop();
    const fresh = new Graph({ type: "directed" });
    graphRef.current = fresh;
    setGraph(fresh);
    setSelectedId("");
    setCounts({ nodes: 0, edges: 0 });
    setMessages([]);
    setLastRun(null);
  }

  function find(event) {
    event.preventDefault();
    const text = search.trim().toLowerCase();
    if (!text) return;
    const id = graph.findNode((_, a) => a.address?.toLowerCase().includes(text) || a.label?.toLowerCase().includes(text));
    if (!id) {
      note("info", `No node matches "${search.trim()}".`);
      return;
    }
    setSelectedId(id);
    focusNode(sigmaRef.current, id);
  }

  function downloadJson() {
    const blob = new Blob([JSON.stringify(graph.export())], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `graph-${graphChain}-${new Date().toISOString().slice(0, 19).replace(/:/g, "")}.json`;
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
  const chainOff = status.data && status.data.chains?.[chain] === false;

  return (
    <>
      <PageHeader
        icon="graph"
        title="Graph explorer"
        subtitle="Advanced graph mode: grow a graph of thousands of addresses. Load 1 or 2 hops, then double-click a node to expand it."
      >
        {budget && (
          <span className="data-badge" title="Graph mode's own daily share of the Alchemy free plan. Bitcoin and Etherscan lookups do not use it.">
            Alchemy today: {budget.alchemy_cu_used_today.toLocaleString()} / {budget.alchemy_cu_daily_limit.toLocaleString()} CU
          </span>
        )}
      </PageHeader>

      <div className="explorer-layout">
        <form className="card controls" onSubmit={load} aria-label="Start a graph">
          <label className="field">
            <span className="field-label">Chain</span>
            <select value={chain} onChange={(e) => setChain(e.target.value)}>
              {CHAINS.map((c) => (
                <option key={c.id} value={c.id} disabled={status.data?.chains?.[c.id] === false}>
                  {c.label}{status.data?.chains?.[c.id] === false ? " (not set up)" : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Starting address</span>
            <input value={address} onChange={(e) => setAddress(e.target.value)} placeholder={CHAINS.find((c) => c.id === chain)?.placeholder} spellCheck={false} />
          </label>
          <div className="field-row">
            <label className="field">
              <span className="field-label">Hops to load</span>
              <select value={hops} onChange={(e) => setHops(Number(e.target.value))}>
                <option value={1}>1 hop</option>
                <option value={2}>2 hops</option>
              </select>
            </label>
            <label className="field">
              <span className="field-label">Node cap</span>
              <input type="number" min={100} max={maxCap} step={500} value={cap}
                onChange={(e) => setCap(Math.max(100, Math.min(maxCap, Number(e.target.value) || DEFAULT_CAP)))} />
            </label>
          </div>
          <label className="field">
            <span className="field-label" title="Counterparties drawn per address before the rest are grouped">Shown per address: {perNode}</span>
            <input type="range" min={10} max={200} step={10} value={perNode} onChange={(e) => setPerNode(Number(e.target.value))} />
          </label>
          {chainOff && <p className="small error-text">This chain is not set up on the backend (missing API key).</p>}
          <div className="form-actions">
            {running ? (
              <button type="button" className="secondary" onClick={stop}>Stop</button>
            ) : (
              <button type="submit" className="primary" disabled={!address.trim() || chainOff}>Load graph</button>
            )}
          </div>
          <p className="small muted">
            {chain === "bitcoin" && "Bitcoin comes from mempool.space (blockstream.info as backup): free, at most 60 addresses per request."}
            {chain === "ethereum" && "Ethereum comes from Etherscan first (free key); Alchemy is the backup. At most 40 addresses per request."}
            {chain === "base" && "Base comes from Alchemy only: about 480 compute units per address, at most 15 per request."}
            {chain === "solana" && "Solana comes from Alchemy only: about 640 compute units per address, at most 8 per request."}
          </p>
        </form>

        <section className="card explorer-main" aria-label="Graph">
          <div className="graph-header">
            <h2>
              {counts.nodes.toLocaleString()} nodes, {counts.edges.toLocaleString()} links
              <span className="small muted"> · cap {cap.toLocaleString()}</span>
            </h2>
            {atCap ? (
              <span className="pill pill-down">Node cap reached</span>
            ) : nearCap ? (
              <span className="pill pill-warn">Near the node cap ({Math.round((counts.nodes / cap) * 100)}%)</span>
            ) : null}
          </div>
          {counts.nodes > 0 && (
            <div className="cap-meter" aria-hidden="true">
              <span style={{ width: `${Math.min(100, (counts.nodes / cap) * 100)}%` }} className={atCap ? "full-bar" : nearCap ? "warn-bar" : ""} />
            </div>
          )}

          <div className="graph-toolbar">
            <form className="grow explorer-search" onSubmit={find}>
              <input type="search" className="graph-search" placeholder="Find an address in the graph" value={search}
                onChange={(e) => setSearch(e.target.value)} aria-label="Find an address" />
            </form>
            <div className="button-group">
              <button type="button" className="secondary" onClick={expandFrontier} disabled={!counts.nodes || Boolean(running) || atCap}
                title="Expand every address not expanded yet (up to the per-request limit)">Expand all</button>
              <button type="button" className="secondary" onClick={() => sigmaRef.current?.getCamera().animatedReset()} disabled={!counts.nodes}>Fit</button>
              <button type="button" className="secondary" onClick={() => { setLayoutPinned(layoutOn); setLayoutOn(!layoutOn); }} disabled={!counts.nodes}
                title="ForceAtlas2 runs in a background thread">{layoutOn ? "Pause layout" : "Run layout"}</button>
              <button type="button" className="secondary" onClick={downloadJson} disabled={!counts.nodes} title="Download the graph (graphology JSON)">JSON</button>
              <button type="button" className="secondary" onClick={clear} disabled={!counts.nodes}>Clear</button>
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
            {counts.nodes === 0 && !running && (
              <div className="sigma-empty muted">Enter an address and press Load graph.</div>
            )}
            <SigmaGraph graph={graph} selectedId={selectedId} onSelect={setSelectedId}
              onExpand={(id) => expandNode(id)} layoutOn={layoutOn} sigmaRef={sigmaRef} />
          </div>

          <div className="legend" aria-label="Legend">
            {Object.entries(KIND_STYLE).filter(([key]) => kindCounts[key]).map(([key, s]) => (
              <span key={key} className="chip">
                <span className="swatch" style={{ background: s.color }} />
                {s.label} ({kindCounts[key].toLocaleString()})
              </span>
            ))}
          </div>
          <p className="small muted hint">
            Scroll to zoom, drag to pan. Click a node for details, double-click to expand it (or open a group).
            {lastRun && ` Last request: ${lastRun.fetched} fetched, ${lastRun.from_cache} from cache, ${lastRun.seconds} s` +
              (lastRun.alchemy_cu ? `, ${lastRun.alchemy_cu.toLocaleString()} Alchemy CU.` : ".")}
          </p>
        </section>

        <NodeDetails key={selectedId + selectedVersion} graph={graph} id={selectedId} unit={unit} running={Boolean(running)} atCap={atCap} labelCount={status.data?.exchange_labels?.addresses ?? 0}
          onExpand={expandNode} onSelect={(id) => { setSelectedId(id); focusNode(sigmaRef.current, id); }} />
      </div>
    </>
  );
}

function NodeDetails({ graph, id, unit, running, atCap, onExpand, onSelect, labelCount }) {
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
        </>
      )}
    </aside>
  );
}
