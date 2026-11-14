import { useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import { LAYOUTS, buildStylesheet, nodeTypeInfo, relationInfo } from "../config/graphStyle.js";
import { useCases } from "../cases/CasesContext.jsx";
import { itemFromNode } from "../cases/caseItems.js";
import { formatBtc } from "../utils/bitcoinAmount.js";
import { WEB3_DESIGN } from "../config/w3Design.js";
import { w3Entrance, w3Hide, w3StartLayout, w3Stylesheet } from "../w3/graphTheme.js";

// Turn our API's { nodes, edges } into the list Cytoscape expects:
// every element is { data: {...} }, and an edge needs "source" and "target".
//
// A transaction can spend 100+ inputs from the same address. Drawn one by
// one, those links pile up into an unreadable fan, so links with the same
// source, target and relation are bundled into one thicker line.
// "bundleOf" remembers which bundle each original link ended up in.
function toElements(nodes, edges) {
  const ids = new Set(nodes.map((n) => n.id));
  const groups = new Map();
  for (const e of edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) continue; // Cytoscape throws on missing ends
    const key = `${e.source}|${e.target}|${e.relation}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(e);
  }

  const bundleOf = new Map();
  const edgeElements = [];
  for (const group of groups.values()) {
    const first = group[0];
    const id = group.length === 1 ? first.id : `bundle:${first.source}->${first.target}:${first.relation}`;
    group.forEach((e) => bundleOf.set(e.id, id));
    edgeElements.push({
      data: {
        id,
        source: first.source,
        target: first.target,
        relation: first.relation,
        count: group.length,
        memberIds: group.map((e) => e.id),
        label: group.length === 1 ? first.label : bundleLabel(group),
      },
    });
  }

  const elements = [
    ...nodes.map((n) => ({ data: { ...n.details, id: n.id, label: n.label, type: n.type } })),
    ...edgeElements,
  ];
  return { elements, bundleOf };
}

// "131 × input" when every label starts with the same word, else "131 links".
function bundleLabel(group) {
  const kinds = new Set(group.map((e) => e.label.split(",")[0]));
  return kinds.size === 1 ? `${group.length} × ${[...kinds][0]}` : `${group.length} links`;
}

// The interactive drawing. Cytoscape draws on a <canvas> and manages its own
// state, so React only gives it an empty <div> and then talks to it through
// effects: one to create it, and one for each thing that can change.
export default function GraphCanvas({
  nodes,
  edges,
  selectedId,
  onSelect,
  layoutName,
  layoutRun,
  hiddenTypes,
  hiddenRelations,
  matchIds,
  showEdgeLabels,
  cyRef,
}) {
  const containerRef = useRef(null);
  const bundleOfRef = useRef(new Map());
  const layoutRef = useRef(null); // the layout currently animating, if any
  const firstLayout = useRef(true); // Web3 mode: the first layout of a graph plays the entrance animation
  const [tooltip, setTooltip] = useState(null);

  // Cytoscape's event handlers are attached once, so they would keep calling
  // the first onSelect they saw. Reading it from a ref always gets the latest.
  const onSelectRef = useRef(onSelect);
  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  // Right-click a node to add it to a case (same ref trick as onSelect).
  const { openPicker, canEdit } = useCases();
  const openPickerRef = useRef(null);
  useEffect(() => {
    openPickerRef.current = canEdit ? openPicker : null;
  }, [openPicker, canEdit]);

  // 1. Create the graph when the data arrives, and destroy it when it leaves.
  useEffect(() => {
    const { elements, bundleOf } = toElements(nodes, edges);
    bundleOfRef.current = bundleOf;
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: WEB3_DESIGN ? w3Stylesheet(buildStylesheet()) : buildStylesheet(),
      layout: WEB3_DESIGN ? w3StartLayout : { name: "grid" }, // placeholder; effect 2 runs the real layout
      minZoom: 0.05, // low enough that "fit" can always show a 50-node graph
      maxZoom: 4,
      boxSelectionEnabled: false,
      autounselectify: true, // we track selection ourselves in React state
    });
    cyRef.current = cy;
    firstLayout.current = true;
    if (WEB3_DESIGN) w3Hide(cy);

    // Click (or tap on a touch screen) a node or a link to select it.
    // A bundle selects its first link; the details panel lists the rest.
    cy.on("tap", "node, edge", (event) => {
      const el = event.target;
      onSelectRef.current(el.isEdge() ? el.data("memberIds")[0] : el.id());
    });
    // Click empty space to clear the selection.
    cy.on("tap", (event) => {
      if (event.target === cy) onSelectRef.current("");
    });
    // Right-click (or long-press on a touch screen) a node: select it and open "Add to case".
    cy.on("cxttap", "node", (event) => {
      const open = openPickerRef.current;
      if (!open) return;
      const node = event.target;
      onSelectRef.current(node.id());
      const box = containerRef.current.getBoundingClientRect();
      const item = itemFromNode({ id: node.id(), type: node.data("type"), label: node.data("label") });
      open(item, { x: box.left + event.renderedPosition.x, y: box.top + event.renderedPosition.y });
    });
    // Double-click a node to zoom in on it and its neighbours.
    cy.on("dbltap", "node", (event) => {
      cy.animate({ fit: { eles: event.target.closedNeighborhood(), padding: 60 } }, { duration: 300 });
    });

    // Hover tooltip. renderedPosition is in screen pixels inside the container.
    cy.on("mouseover", "node, edge", (event) => {
      const el = event.target;
      containerRef.current.style.cursor = "pointer";
      setTooltip({ x: event.renderedPosition.x, y: event.renderedPosition.y, ...describe(el) });
    });
    cy.on("mouseout", "node, edge", () => {
      containerRef.current.style.cursor = "";
      setTooltip(null);
    });
    cy.on("viewport", () => setTooltip(null)); // hide it while panning or zooming

    return () => {
      // Stop a running layout animation first. A stopped "force" layout still finishes on its
      // next animation frame, so destroy the graph two frames later (leaving the page mid-layout
      // used to throw "Cannot read properties of null").
      layoutRef.current?.stop();
      cy.stop();
      cyRef.current = null;
      requestAnimationFrame(() => requestAnimationFrame(() => cy.destroy()));
    };
  }, [nodes, edges, cyRef]);

  // 2. Arrange the nodes. Runs on first load, when the layout menu changes,
  //    and when "Re-arrange" bumps layoutRun.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    // Web3 mode: the first layout of a new graph plays the "fly out from the middle" entrance.
    if (WEB3_DESIGN && firstLayout.current) {
      firstLayout.current = false;
      const layout = w3Entrance(cy, LAYOUTS[layoutName].options);
      layoutRef.current = layout;
      return () => layout.stop();
    }
    const layout = cy
      .elements(":visible")
      .layout({ ...LAYOUTS[layoutName].options, animate: true, animationDuration: 400, fit: true, padding: 40 });
    layoutRef.current = layout;
    layout.run();
    return () => layout.stop();
  }, [layoutName, layoutRun, nodes, edges, cyRef]);

  // 3. Hide filtered types and relations. Edges of a hidden node hide with it.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().removeClass("filtered");
      cy.nodes().filter((n) => hiddenTypes.has(n.data("type"))).addClass("filtered");
      cy.edges().filter((e) => hiddenRelations.has(e.data("relation"))).addClass("filtered");
    });
  }, [hiddenTypes, hiddenRelations, nodes, edges, cyRef]);

  // 4. Show the selection: highlight it and its neighbours, fade the rest.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().removeClass("selected faded");
      const el = selectedId ? cy.getElementById(bundleOfRef.current.get(selectedId) ?? selectedId) : cy.collection();
      if (el.empty()) return;
      const neighbourhood = el.isNode() ? el.closedNeighborhood() : el.union(el.connectedNodes());
      cy.elements().not(neighbourhood).addClass("faded");
      el.addClass("selected");
    });
    // If the selection came from outside the graph (search, details panel)
    // and is off screen, glide to it.
    const el = selectedId ? cy.getElementById(bundleOfRef.current.get(selectedId) ?? selectedId) : null;
    if (el && el.nonempty() && el.visible()) {
      const box = el.renderedBoundingBox();
      const offScreen = box.x1 < 0 || box.y1 < 0 || box.x2 > cy.width() || box.y2 > cy.height();
      if (offScreen) cy.animate({ center: { eles: el } }, { duration: 300 });
    }
  }, [selectedId, nodes, edges, cyRef]);

  // 5. Outline search matches.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.nodes().removeClass("match");
      matchIds.forEach((id) => cy.getElementById(id).addClass("match"));
    });
  }, [matchIds, nodes, edges, cyRef]);

  // 6. Show or hide the text on every link.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    if (showEdgeLabels) cy.edges().addClass("labelled");
    else cy.edges().removeClass("labelled");
  }, [showEdgeLabels, nodes, edges, cyRef]);

  return (
    <div className="graph-canvas-wrap">
      <div
        ref={containerRef}
        className="graph-canvas"
        tabIndex={0}
        aria-label="Interactive relationship graph. Use the list below for keyboard access."
        onKeyDown={(event) => {
          if (event.key === "Escape") onSelect("");
        }}
        // The graph's own right-click menu replaces the browser's.
        onContextMenu={(event) => canEdit && event.preventDefault()}
      />
      {tooltip && (
        <div className="graph-tooltip" style={{ left: tooltip.x + 14, top: tooltip.y + 14 }} role="tooltip">
          <div className="tooltip-kind">{tooltip.kind}</div>
          <div className="tooltip-title">{tooltip.title}</div>
          {tooltip.lines.map((line) => (
            <div key={line} className="small muted">{line}</div>
          ))}
        </div>
      )}
    </div>
  );
}

// What the tooltip says about a node or an edge.
function describe(el) {
  if (el.isNode()) {
    const info = nodeTypeInfo(el.data("type"));
    const lines = [`${el.degree()} link(s)`];
    if (el.data("status")) lines.push(`Status: ${el.data("status")}`);
    if (el.data("balance_sats") != null) lines.push(`Balance: ${formatBtc(el.data("balance_sats"))}`);
    if (el.data("total_transferred_sats") != null) lines.push(`Transferred: ${formatBtc(el.data("total_transferred_sats"))}`);
    if (el.data("fee_sats") != null) lines.push(`Fee: ${formatBtc(el.data("fee_sats"))}`);
    lines.push("Click for details, double-click to zoom, right-click to add to a case");
    return { kind: info.label, title: el.data("label"), lines };
  }
  const info = relationInfo(el.data("relation"));
  const lines = [`${el.source().data("label")} → ${el.target().data("label")}`, info.help];
  if (el.data("count") > 1) lines.push(`${el.data("count")} separate links bundled into one line`);
  return { kind: `${info.label} link`, title: el.data("label"), lines };
}
