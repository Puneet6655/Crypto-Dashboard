import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiFetch } from "../api/client.js";
import { useCases } from "../cases/CasesContext.jsx";
import { itemFromNode } from "../cases/caseItems.js";
import { useToast } from "../components/Toasts.jsx";
import { W3_EDGE_IN, W3_EDGE_OUT, W3_KIND_COLORS } from "./sigmaTheme.js";

// Graph explorer extras for Web3 mode: right-click menu, live search suggestions, filters,
// and "money flow" particles drawn over the WebGL graph.

/* ---------------- Right-click menu ---------------- */
export function W3NodeMenu({ menu, graph, chain, onClose, onExpand, onSelectFocus, onHide, onTogglePin }) {
  const ref = useRef(null);
  const { openPicker, canEdit } = useCases();
  const toast = useToast();

  useEffect(() => {
    if (!menu) return undefined;
    const close = (e) => { if (!ref.current?.contains(e.target)) onClose(); };
    const key = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("pointerdown", close, true);
    window.addEventListener("keydown", key);
    window.addEventListener("wheel", onClose, { passive: true });
    return () => {
      window.removeEventListener("pointerdown", close, true);
      window.removeEventListener("keydown", key);
      window.removeEventListener("wheel", onClose);
    };
  }, [menu, onClose]);

  if (!menu || !graph.hasNode(menu.node)) return null;
  const a = graph.getNodeAttributes(menu.node);
  const isGroup = a.kind === "dust" || a.kind === "more";
  const run = (fn) => () => { onClose(); fn(); };
  // Keep the menu on screen.
  const x = Math.min(menu.x, window.innerWidth - 240);
  const y = Math.min(menu.y, window.innerHeight - 340);

  async function watch() {
    try {
      await apiFetch("/api/alerts/watchlist", { method: "POST", body: { chain, address: a.address, label: a.exchange?.exchange ?? null } });
      toast(`Watching ${a.label} live. New transactions show on the Live alerts page.`, { tone: "success" });
    } catch (err) {
      toast(err.message, { tone: "error" });
    }
  }

  // Rendered into <body>: the glass cards create their own positioning context, which would shift a fixed menu.
  return createPortal(
    <div ref={ref} className="w3-menu" style={{ left: x, top: y }} role="menu" aria-label={`Actions for ${a.label}`}>
      <div className="w3-menu-head">
        <span className="swatch" style={{ background: W3_KIND_COLORS[a.styleKey] }} />
        <span className="mono">{a.label}</span>
      </div>
      {isGroup ? (
        <button type="button" role="menuitem" onClick={run(() => onExpand(menu.node))}>Open this group</button>
      ) : (
        <>
          <button type="button" role="menuitem" onClick={run(() => onExpand(menu.node, 1))}>{a.expanded ? "Expand again" : "Expand 1 hop"}</button>
          <button type="button" role="menuitem" onClick={run(() => onExpand(menu.node, 2))}>Expand 2 hops</button>
          <button type="button" role="menuitem" onClick={run(() => onSelectFocus(menu.node))}>Focus on it</button>
          <hr />
          {canEdit && (
            <button type="button" role="menuitem" onClick={run(() => openPicker(
              itemFromNode({ id: menu.node, type: "address", label: a.label }), { x, y }))}>Add to a case</button>
          )}
          <button type="button" role="menuitem" onClick={run(watch)}>Watch live</button>
          <button type="button" role="menuitem" onClick={run(() => navigator.clipboard?.writeText(a.address)
            .then(() => toast("Address copied", { tone: "success", timeout: 2500 })))}>Copy address</button>
        </>
      )}
      <hr />
      <button type="button" role="menuitem" onClick={run(() => onTogglePin(menu.node))}>{a.fixed ? "Unpin (let the layout move it)" : "Pin in place"}</button>
      <button type="button" role="menuitem" onClick={run(() => onHide(menu.node))}>Hide from view</button>
    </div>,
    document.body,
  );
}

/* ---------------- Search with suggestions ---------------- */
export function W3GraphSearch({ graph, version, onPick }) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const q = text.trim().toLowerCase();
  const matches = [];
  if (q && open) {
    graph.forEachNode((id, a) => {
      if (matches.length >= 8) return;
      if (a.address?.toLowerCase().includes(q) || a.label?.toLowerCase().includes(q) || a.exchange?.exchange?.toLowerCase().includes(q)) {
        matches.push({ id, a });
      }
    });
  }
  void version; // re-render when the graph grows

  function pick(id) {
    setOpen(false);
    onPick(id);
  }

  return (
    <div className="w3-search grow">
      <input type="search" className="graph-search" placeholder="Find an address or exchange in the graph" value={text}
        onChange={(e) => { setText(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => { if (e.key === "Enter" && matches[0]) { e.preventDefault(); pick(matches[0].id); } }}
        aria-label="Find an address in the graph" />
      {open && q && (
        <ul className="w3-search-list" role="listbox">
          {matches.length ? matches.map(({ id, a }) => (
            <li key={id}>
              <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => pick(id)}>
                <span className="swatch" style={{ background: W3_KIND_COLORS[a.styleKey] }} />
                <span className="mono grow">{a.address ?? a.label}</span>
                {a.exchange && <span className="small muted">{a.exchange.exchange}</span>}
              </button>
            </li>
          )) : <li className="muted small w3-search-none">No match in the graph</li>}
        </ul>
      )}
    </div>
  );
}

/* ---------------- Filters ---------------- */
// Amount steps for the "hide small links" slider (in the chain's own unit).
const MIN_STEPS = [0, 0.0001, 0.001, 0.01, 0.1, 1, 10, 100];

export function W3GraphFilters({ kinds, counts, hiddenKinds, onToggleKind, minIndex, onMinIndex, unit, hiddenNodes, onShowHidden }) {
  return (
    <div className="w3-filters">
      <div className="legend" aria-label="Show or hide node kinds">
        {Object.entries(kinds).filter(([key]) => counts[key]).map(([key, s]) => {
          const off = hiddenKinds.has(key);
          return (
            <button key={key} type="button" className={`chip ${off ? "chip-off" : ""}`} aria-pressed={!off} onClick={() => onToggleKind(key)}
              title={off ? "Show these" : "Hide these"}>
              <span className="swatch" style={{ background: W3_KIND_COLORS[key] ?? s.color }} />
              {s.label} ({counts[key].toLocaleString()})
            </button>
          );
        })}
      </div>
      <label className="w3-range">
        <span className="small muted">Hide links under</span>
        <input type="range" min={0} max={MIN_STEPS.length - 1} value={minIndex} onChange={(e) => onMinIndex(Number(e.target.value))} />
        <strong className="small">{MIN_STEPS[minIndex] ? `${MIN_STEPS[minIndex]} ${unit}` : "show all"}</strong>
      </label>
      {hiddenNodes.size > 0 && (
        <button type="button" className="secondary compact" onClick={onShowHidden}>Show {hiddenNodes.size} hidden node{hiddenNodes.size === 1 ? "" : "s"}</button>
      )}
    </div>
  );
}
export const minValueAt = (i) => MIN_STEPS[i] ?? 0;

/* ---------------- Money flow particles ---------------- */
// A canvas over the WebGL graph. Dots travel along links from sender to receiver.
// Around the selected node: its links only (pink out, teal in). Otherwise every visible link, up to a limit.
const MAX_FLOW_EDGES = 900;

export function W3FlowOverlay({ sigmaRef, graph, focusId, view, on }) {
  const canvasRef = useRef(null);
  const state = useRef({ focusId, view });
  state.current = { focusId, view };

  useEffect(() => {
    if (!on) return undefined;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return undefined;
    let frame = 0;
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;

    function draw(now) {
      frame = requestAnimationFrame(draw);
      const renderer = sigmaRef.current;
      if (!renderer) return;
      const w = canvas.clientWidth, h = canvas.clientHeight;
      if (canvas.width !== w * dpr || canvas.height !== h * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const { focusId: focus, view: v } = state.current;
      const hasFocus = focus && graph.hasNode(focus);
      const edges = hasFocus ? graph.edges(focus) : graph.size <= MAX_FLOW_EDGES ? graph.edges() : [];
      for (const edge of edges) {
        const [s, t] = graph.extremities(edge);
        if (v?.hiddenNodes?.has(s) || v?.hiddenNodes?.has(t)) continue;
        const ks = graph.getNodeAttribute(s, "styleKey"), kt = graph.getNodeAttribute(t, "styleKey");
        if (v?.hiddenKinds?.has(ks) || v?.hiddenKinds?.has(kt)) continue;
        const value = graph.getEdgeAttribute(edge, "value") ?? 0;
        if (v?.minValue > 0 && value < v.minValue) continue;
        const a = renderer.getNodeDisplayData(s), b = renderer.getNodeDisplayData(t);
        if (!a || !b) continue;
        const pa = renderer.framedGraphToViewport(a), pb = renderer.framedGraphToViewport(b);
        const color = !hasFocus ? "rgba(196,181,253,0.85)" : s === focus ? W3_EDGE_OUT : W3_EDGE_IN;
        const len = Math.hypot(pb.x - pa.x, pb.y - pa.y);
        if (len < 6) continue;
        const speed = 0.00018 * Math.max(0.6, Math.min(2.2, 160 / len)) * 1.6; // similar on-screen speed for short and long links
        const dots = hasFocus ? 3 : 1;
        const seed = hash(edge);
        ctx.fillStyle = color;
        ctx.shadowColor = color;
        ctx.shadowBlur = hasFocus ? 10 : 6;
        for (let i = 0; i < dots; i++) {
          const p = (now * speed + seed + i / dots) % 1;
          const r = hasFocus ? 2.6 : 1.8;
          ctx.beginPath();
          ctx.arc(pa.x + (pb.x - pa.x) * p, pa.y + (pb.y - pa.y) * p, r, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }
    frame = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(frame); ctx.clearRect(0, 0, canvas.width, canvas.height); };
  }, [on, graph, sigmaRef]);

  return <canvas ref={canvasRef} className="w3-flow" aria-hidden="true" />;
}

function hash(text) {
  let h = 0;
  for (let i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) % 1000;
  return h / 1000;
}
