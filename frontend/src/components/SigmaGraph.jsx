import { useEffect, useRef } from "react";
import Sigma from "sigma";
import { EdgeArrowProgram } from "sigma/rendering";
import FA2Layout from "graphology-layout-forceatlas2/worker";
import { WEB3_DESIGN } from "../config/w3Design.js";
import { W3_EDGE, W3_EDGE_IN, W3_EDGE_OUT, W3_FADED, W3_KIND_COLORS, W3_SIGMA_SETTINGS } from "../w3/sigmaTheme.js";

// The WebGL drawing for advanced graph mode. Sigma draws with WebGL, so it stays smooth with
// thousands of nodes where the canvas-based Investigate graph slows down. The graph data itself
// lives in a graphology Graph owned by the page; Sigma redraws by itself whenever it changes.
//
// ForceAtlas2 arranges the nodes in a web worker (a background thread), so the page never
// freezes while it runs. It runs while `layoutOn` is true.

const FADED = "#e5e7eb";
const FA2_SETTINGS = {
  barnesHutOptimize: true, // approximates far-away forces: fast enough for 10,000+ nodes
  barnesHutTheta: 0.6,
  gravity: 1,
  scalingRatio: 8,
  slowDown: 6,
  strongGravityMode: false,
};

// view (Web3 mode only): { hiddenKinds: Set, hiddenNodes: Set, minValue: number, onContext(node, x, y) }.
// In Web3 mode hovering a node also lights up its neighbours, and right-click opens a menu.
export default function SigmaGraph({ graph, selectedId, onSelect, onExpand, layoutOn, sigmaRef, view = null }) {
  const containerRef = useRef(null);
  const selectedRef = useRef(selectedId);
  const hoveredRef = useRef("");
  const viewRef = useRef(view);
  viewRef.current = view;
  const handlers = useRef({ onSelect, onExpand });
  handlers.current = { onSelect, onExpand };

  // 1. Create Sigma once for this graph object.
  useEffect(() => {
    const renderer = new Sigma(graph, containerRef.current, {
      allowInvalidContainer: true, // the page may be hidden for a moment while it lays out
      defaultEdgeType: "arrow",
      edgeProgramClasses: { arrow: EdgeArrowProgram },
      renderEdgeLabels: false,
      labelRenderedSizeThreshold: 9, // only label nodes drawn big enough to read
      labelDensity: 0.4,
      zIndex: true,
      minCameraRatio: 0.02,
      maxCameraRatio: 20,
      ...(WEB3_DESIGN ? W3_SIGMA_SETTINGS : {}),
      // Dim everything that is not the selected node or its neighbours.
      nodeReducer: WEB3_DESIGN ? (node, data) => w3NodeReducer(graph, node, data, hoveredRef.current || selectedRef.current, viewRef.current) : (node, data) => {
        const sel = selectedRef.current;
        if (!sel || !graph.hasNode(sel)) return data;
        if (node === sel) return { ...data, highlighted: true, zIndex: 2 };
        if (graph.areNeighbors(node, sel)) return { ...data, zIndex: 1 };
        return { ...data, color: FADED, label: "", zIndex: 0 };
      },
      edgeReducer: WEB3_DESIGN ? (edge, data) => w3EdgeReducer(graph, edge, data, hoveredRef.current || selectedRef.current, viewRef.current) : (edge, data) => {
        const sel = selectedRef.current;
        if (!sel || !graph.hasNode(sel)) return data;
        return graph.hasExtremity(edge, sel) ? { ...data, size: Math.max(data.size ?? 1, 2), zIndex: 1 } : { ...data, hidden: true };
      },
    });
    sigmaRef.current = renderer;

    renderer.on("clickNode", ({ node }) => handlers.current.onSelect(node));
    renderer.on("clickStage", () => handlers.current.onSelect(""));
    renderer.on("doubleClickNode", (event) => {
      event.preventSigmaDefault(); // no zoom: double-click means "expand"
      handlers.current.onExpand(event.node);
    });
    renderer.on("enterNode", ({ node }) => {
      containerRef.current.style.cursor = "pointer";
      if (WEB3_DESIGN) { hoveredRef.current = node; renderer.refresh({ skipIndexation: true }); }
    });
    renderer.on("leaveNode", () => {
      containerRef.current.style.cursor = "";
      if (WEB3_DESIGN) { hoveredRef.current = ""; renderer.refresh({ skipIndexation: true }); }
    });
    if (WEB3_DESIGN) {
      renderer.on("rightClickNode", (event) => {
        event.preventSigmaDefault();
        event.event.original.preventDefault();
        const box = containerRef.current.getBoundingClientRect();
        viewRef.current?.onContext?.(event.node, box.left + event.event.x, box.top + event.event.y);
      });
    }

    return () => {
      renderer.kill();
      sigmaRef.current = null;
    };
  }, [graph, sigmaRef]);

  // 2. Redraw when the selection (or, in Web3 mode, a filter) changes; the reducers read them from refs.
  useEffect(() => {
    selectedRef.current = selectedId;
    sigmaRef.current?.refresh({ skipIndexation: true });
  }, [selectedId, sigmaRef, view?.hiddenKinds, view?.hiddenNodes, view?.minValue]);

  // 3. The background layout. Killed when switched off or when the graph object changes.
  useEffect(() => {
    if (!layoutOn) return undefined;
    const layout = new FA2Layout(graph, { settings: FA2_SETTINGS, getEdgeWeight: null });
    layout.start();
    return () => layout.kill();
  }, [graph, layoutOn]);

  return (
    <div
      ref={containerRef}
      className="sigma-canvas"
      tabIndex={0}
      aria-label="Large relationship graph drawn with WebGL. Use the details panel and search for keyboard access."
      onKeyDown={(event) => {
        if (event.key === "Escape") onSelect("");
      }}
      onContextMenu={WEB3_DESIGN ? (event) => event.preventDefault() : undefined}
    />
  );
}

// Move the camera to a node and zoom in a little.
export function focusNode(renderer, node) {
  if (!renderer || !renderer.getGraph().hasNode(node)) return;
  const pos = renderer.getNodeDisplayData(node);
  if (pos) renderer.getCamera().animate({ x: pos.x, y: pos.y, ratio: 0.25 }, { duration: 400 });
}

// Web3 mode: hide filtered nodes; around the focused (hovered or selected) node, keep it and its neighbours bright.
function nodeHidden(graph, node, view) {
  if (!view) return false;
  if (view.hiddenNodes?.has(node)) return true;
  return view.hiddenKinds?.has(graph.getNodeAttribute(node, "styleKey")) ?? false;
}

function w3NodeReducer(graph, node, data, focus, view) {
  if (nodeHidden(graph, node, view)) return { ...data, hidden: true };
  const out = { ...data, color: W3_KIND_COLORS[data.styleKey] ?? data.color };
  if (data.fixed) out.borderColor = "#fff";
  if (!focus || !graph.hasNode(focus)) return out;
  if (node === focus) return { ...out, highlighted: true, zIndex: 2, size: data.size * 1.25 };
  if (graph.areNeighbors(node, focus)) return { ...out, zIndex: 1, forceLabel: graph.degree(focus) <= 30 };
  return { ...out, color: W3_FADED, label: "", zIndex: 0 };
}

function w3EdgeReducer(graph, edge, data, focus, view) {
  const [source, target] = graph.extremities(edge);
  if (nodeHidden(graph, source, view) || nodeHidden(graph, target, view)) return { ...data, hidden: true };
  if (view?.minValue > 0 && (data.value ?? 0) < view.minValue) return { ...data, hidden: true };
  if (!focus || !graph.hasNode(focus)) return { ...data, color: W3_EDGE };
  if (source === focus) return { ...data, color: W3_EDGE_OUT, size: Math.max(data.size ?? 1, 2), zIndex: 1 };
  if (target === focus) return { ...data, color: W3_EDGE_IN, size: Math.max(data.size ?? 1, 2), zIndex: 1 };
  return { ...data, hidden: true };
}
