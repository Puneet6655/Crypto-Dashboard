import { useEffect, useMemo, useRef, useState } from "react";
import GraphCanvas from "./GraphCanvas.jsx";
import { LAYOUTS, NODE_TYPES, RELATIONS, nodeTypeInfo, relationInfo } from "../config/graphStyle.js";
import { WEB3_DESIGN } from "../config/w3Design.js";
import W3Scanner, { W3Idle } from "../w3/W3Scanner.jsx";

// Centre column. Shows the idle, loading, error, cancelled and empty states,
// or the interactive graph once a result has arrived.
export default function GraphView({ status, error, result, selectedId, onSelect, onCancel, startedAt, methodLabel }) {
  if (status === "idle") {
    return (
      <section className="card graph state">
        {WEB3_DESIGN && <W3Idle />}
        {WEB3_DESIGN ? "Paste an address, transaction or website on the left, then press Investigate." : "Choose a mode and a case, then press Start Investigation."}
      </section>
    );
  }
  if (status === "loading") {
    return <LoadingState startedAt={startedAt} methodLabel={methodLabel} onCancel={onCancel} />;
  }
  if (status === "cancelled") {
    return <section className="card graph state">Investigation cancelled. Press Start Investigation to try again.</section>;
  }
  if (status === "error") {
    return <section className="card graph state error" role="alert">{error}</section>;
  }
  if (status === "empty") {
    return (
      <section className="card graph state">
        No demo data exists for this input. For a real Bitcoin address or transaction, choose
        Crypto Investigation and the "Live lookup" method.
      </section>
    );
  }
  // The key makes React start GraphExplorer fresh (filters, search, layout)
  // for every new investigation, instead of carrying the old choices over.
  return (
    <GraphExplorer key={result.investigation_id} result={result} selectedId={selectedId} onSelect={onSelect} />
  );
}

// A spinner with a live timer, so a slow live lookup does not look frozen.
function LoadingState({ startedAt, methodLabel, onCancel }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(timer); // stop ticking when loading ends
  }, []);
  const seconds = Math.max(0, (now - startedAt) / 1000);

  return (
    <section className="card graph state" role="status" aria-live="polite">
      <div className="loading">
        {WEB3_DESIGN ? <W3Scanner /> : <div className="spinner" aria-hidden="true" />}
        <div>Running {methodLabel}...</div>
        <div className="small muted">{seconds.toFixed(1)} s</div>
        {seconds > 5 && <div className="small muted">Live lookups can take a while when mempool.space is busy.</div>}
        <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
      </div>
    </section>
  );
}

function GraphExplorer({ result, selectedId, onSelect }) {
  const { nodes, edges } = result.graph;
  const cyRef = useRef(null); // GraphCanvas puts the Cytoscape instance here
  // Small transaction graphs read best as a top-to-bottom flow; bigger ones
  // get too wide for that, so they start with the force layout.
  const [layoutName, setLayoutName] = useState(() =>
    nodes.length <= 12 && nodes.some((n) => n.type === "transaction") ? "breadthfirst" : "cose"
  );
  const [layoutRun, setLayoutRun] = useState(0);
  const [hiddenTypes, setHiddenTypes] = useState(() => new Set());
  const [hiddenRelations, setHiddenRelations] = useState(() => new Set());
  const [search, setSearch] = useState("");
  const [showEdgeLabels, setShowEdgeLabels] = useState(false);

  // Counts for the legend. useMemo skips the work unless the graph changes.
  const typeCounts = useMemo(() => countBy(nodes, "type"), [nodes]);
  const relationCounts = useMemo(() => countBy(edges, "relation"), [edges]);

  // Nodes whose label or ID contains the search text.
  const matchIds = useMemo(() => {
    const text = search.trim().toLowerCase();
    if (!text) return [];
    return nodes
      .filter((n) => !hiddenTypes.has(n.type))
      .filter((n) => n.label.toLowerCase().includes(text) || n.id.toLowerCase().includes(text))
      .map((n) => n.id);
  }, [search, nodes, hiddenTypes]);

  // Sets are copied, never changed in place, so React sees a new value.
  const toggle = (setter) => (key) =>
    setter((old) => {
      const next = new Set(old);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  const cy = () => cyRef.current;
  const zoomBy = (factor) => {
    const c = cy();
    if (c) c.animate({ zoom: { level: c.zoom() * factor, renderedPosition: { x: c.width() / 2, y: c.height() / 2 } } }, { duration: 150 });
  };
  const fit = () => cy()?.animate({ fit: { eles: cy().elements(":visible"), padding: 40 } }, { duration: 300 });
  const savePng = () => {
    const c = cy();
    if (!c) return;
    const link = document.createElement("a");
    link.href = c.png({ full: true, scale: 2, bg: "#ffffff" });
    link.download = `${result.investigation_id}-graph.png`;
    link.click();
  };

  const labelOf = (id) => nodes.find((n) => n.id === id)?.label ?? id;
  const hiddenCount = nodes.filter((n) => hiddenTypes.has(n.type)).length;

  return (
    <section className="card graph" aria-label="Relationship graph">
      <div className="graph-header">
        <h2>
          Graph ({nodes.length} nodes, {edges.length} links)
          {result.graph.truncated && (
            <span className="small muted"> limited to {result.graph.limits.max_nodes} nodes</span>
          )}
        </h2>
        {hiddenCount > 0 && <span className="small muted">{hiddenCount} hidden by filters</span>}
      </div>

      <div className="graph-toolbar">
        <input
          type="search"
          className="graph-search"
          placeholder="Find a node by label or ID"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          onKeyDown={(event) => {
            // Enter jumps to the first match.
            if (event.key === "Enter" && matchIds.length) onSelect(matchIds[0]);
          }}
          aria-label="Search nodes"
        />
        <select value={layoutName} onChange={(event) => setLayoutName(event.target.value)} aria-label="Layout">
          {Object.entries(LAYOUTS).map(([id, layout]) => (
            <option key={id} value={id}>{layout.label}</option>
          ))}
        </select>
        <div className="button-group">
          <button type="button" className="icon-button" onClick={() => zoomBy(1.25)} title="Zoom in" aria-label="Zoom in">+</button>
          <button type="button" className="icon-button" onClick={() => zoomBy(0.8)} title="Zoom out" aria-label="Zoom out">−</button>
          <button type="button" className="secondary" onClick={fit} title="Fit the whole graph on screen">Fit</button>
          <button type="button" className="secondary" onClick={() => setLayoutRun((n) => n + 1)} title="Run the layout again">Re-arrange</button>
          <button type="button" className="secondary" onClick={savePng} title="Download the graph as an image">PNG</button>
        </div>
      </div>
      {search.trim() && (
        <div className="small muted search-result">
          {matchIds.length ? `${matchIds.length} match(es), press Enter to jump to the first` : "No matching nodes"}
        </div>
      )}

      <GraphCanvas
        nodes={nodes}
        edges={edges}
        selectedId={selectedId}
        onSelect={onSelect}
        layoutName={layoutName}
        layoutRun={layoutRun}
        hiddenTypes={hiddenTypes}
        hiddenRelations={hiddenRelations}
        matchIds={matchIds}
        showEdgeLabels={showEdgeLabels}
        cyRef={cyRef}
      />

      {/* The legend doubles as the filter: click a chip to hide or show that kind. */}
      <div className="legend" aria-label="Legend and filters">
        {Object.keys(typeCounts).map((type) => {
          const info = nodeTypeInfo(type);
          const hidden = hiddenTypes.has(type);
          return (
            <button
              key={type}
              type="button"
              className={`chip ${hidden ? "chip-off" : ""}`}
              aria-pressed={!hidden}
              onClick={() => toggle(setHiddenTypes)(type)}
              title={hidden ? "Show these nodes" : "Hide these nodes"}
            >
              <span className="swatch" style={{ background: info.color }} />
              {info.label} ({typeCounts[type]})
            </button>
          );
        })}
        {Object.keys(relationCounts).map((relation) => {
          const info = relationInfo(relation);
          const hidden = hiddenRelations.has(relation);
          return (
            <button
              key={relation}
              type="button"
              className={`chip ${hidden ? "chip-off" : ""}`}
              aria-pressed={!hidden}
              onClick={() => toggle(setHiddenRelations)(relation)}
              title={info.help}
            >
              <span className="line-swatch" style={{ borderTopStyle: info.lineStyle, borderTopColor: info.color }} />
              {info.label} ({relationCounts[relation]})
            </button>
          );
        })}
        <label className="chip checkbox-chip">
          <input type="checkbox" checked={showEdgeLabels} onChange={(event) => setShowEdgeLabels(event.target.checked)} />
          Link labels
        </label>
      </div>
      <p className="small muted hint">
        Scroll to zoom, drag the background to pan, drag a node to move it, double-click a node to zoom in on it,
        Esc to clear the selection.
      </p>

      {/* The same links as a plain list, for keyboard and screen reader users. */}
      <details className="edge-details">
        <summary>Links as a list ({edges.length})</summary>
        <ul className="edge-list">
          {edges.map((edge) => (
            <li key={edge.id} className={edge.id === selectedId ? "selected-row" : ""}>
              <button type="button" className="link" onClick={() => onSelect(edge.id)}>
                {labelOf(edge.source)} &rarr; {labelOf(edge.target)}
              </button>
              : {edge.label}{" "}
              <span className={`relation relation-${edge.relation}`} title={relationInfo(edge.relation).help}>
                {edge.relation}
              </span>
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}

function countBy(items, key) {
  const counts = {};
  for (const item of items) counts[item[key]] = (counts[item[key]] ?? 0) + 1;
  // Known kinds first, in the order they are declared in graphStyle.js.
  const order = [...Object.keys(NODE_TYPES), ...Object.keys(RELATIONS)];
  return Object.fromEntries(
    Object.entries(counts).sort(([a], [b]) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99))
  );
}
