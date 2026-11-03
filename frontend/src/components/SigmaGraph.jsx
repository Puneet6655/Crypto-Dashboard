import { useEffect, useRef } from "react";
import Sigma from "sigma";
import { EdgeArrowProgram } from "sigma/rendering";
import FA2Layout from "graphology-layout-forceatlas2/worker";

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

export default function SigmaGraph({ graph, selectedId, onSelect, onExpand, layoutOn, sigmaRef }) {
  const containerRef = useRef(null);
  const selectedRef = useRef(selectedId);
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
      // Dim everything that is not the selected node or its neighbours.
      nodeReducer: (node, data) => {
        const sel = selectedRef.current;
        if (!sel || !graph.hasNode(sel)) return data;
        if (node === sel) return { ...data, highlighted: true, zIndex: 2 };
        if (graph.areNeighbors(node, sel)) return { ...data, zIndex: 1 };
        return { ...data, color: FADED, label: "", zIndex: 0 };
      },
      edgeReducer: (edge, data) => {
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
    renderer.on("enterNode", () => { containerRef.current.style.cursor = "pointer"; });
    renderer.on("leaveNode", () => { containerRef.current.style.cursor = ""; });

    return () => {
      renderer.kill();
      sigmaRef.current = null;
    };
  }, [graph, sigmaRef]);

  // 2. Redraw when the selection changes (the reducers read it from the ref).
  useEffect(() => {
    selectedRef.current = selectedId;
    sigmaRef.current?.refresh({ skipIndexation: true });
  }, [selectedId, sigmaRef]);

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
    />
  );
}

// Move the camera to a node and zoom in a little.
export function focusNode(renderer, node) {
  if (!renderer || !renderer.getGraph().hasNode(node)) return;
  const pos = renderer.getNodeDisplayData(node);
  if (pos) renderer.getCamera().animate({ x: pos.x, y: pos.y, ratio: 0.25 }, { duration: 400 });
}
