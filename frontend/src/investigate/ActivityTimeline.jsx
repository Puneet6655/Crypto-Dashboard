import { useEffect, useMemo, useRef, useState } from "react";
import { formatBtc } from "../utils/bitcoinAmount.js";

// Investigate graph: the transactions on a time line. Each dot is one transaction (higher = more value moved),
// coloured by direction for the looked-up address. Click a dot to select it in the graph; drag across the line
// to show only that period in the graph.
const H = 132;
const PAD = { left: 12, right: 12, top: 14, bottom: 26 };

export function timelinePoints(result) {
  const { nodes, edges } = result.graph;
  const subject = result.input?.type?.endsWith("_address") ? nodes[0]?.id : null;
  const dir = new Map();
  if (subject) {
    for (const e of edges) {
      if (e.source === subject) dir.set(e.target, dir.get(e.target) === "in" ? "self" : "out");
      if (e.target === subject) dir.set(e.source, dir.get(e.source) === "out" ? "self" : "in");
    }
  }
  return nodes
    .filter((n) => n.type === "transaction" && (n.details?.block_time || n.details?.block_height || n.details?.status === "unconfirmed"))
    .map((n) => ({
      id: n.id,
      label: n.label,
      time: n.details.block_time ? Date.parse(n.details.block_time) : null,
      height: n.details.block_height ?? null,
      sats: n.details.total_transferred_sats ?? 0,
      pending: n.details.status === "unconfirmed" || (!n.details.block_time && !n.details.block_height),
      dir: dir.get(n.id) ?? "other",
    }));
}

export default function ActivityTimeline({ points, selectedId, onSelect, range, onRange }) {
  const wrapRef = useRef(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState(null);
  const [drag, setDrag] = useState(null); // { x0, x1 }

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(240, entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Time when every confirmed point has one, else block height. Pending ones sit at the right edge.
  const useTime = points.filter((p) => !p.pending).every((p) => p.time != null);
  const xOf = (p) => (useTime ? p.time : p.height);
  const confirmed = points.filter((p) => !p.pending && xOf(p) != null);
  const xs = confirmed.map(xOf);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const hasPending = points.some((p) => p.pending);
  const plotRight = width - PAD.right - (hasPending ? 64 : 0);
  const span = maxX - minX || 1;
  const toPx = (x) => PAD.left + ((x - minX) / span) * (plotRight - PAD.left);
  const toX = (px) => minX + ((px - PAD.left) / (plotRight - PAD.left)) * span;
  const maxLog = Math.max(...points.map((p) => Math.log10(p.sats + 1)), 1);
  const yOf = (p) => H - PAD.bottom - (Math.log10(p.sats + 1) / maxLog) * (H - PAD.top - PAD.bottom - 6);

  const placed = useMemo(() => points.map((p) => ({
    ...p,
    cx: p.pending || xOf(p) == null ? width - PAD.right - 14 : confirmed.length === 1 ? (PAD.left + plotRight) / 2 : toPx(xOf(p)),
    cy: yOf(p),
    r: 3.5 + (Math.log10(p.sats + 1) / maxLog) * 4.5,
  })), [points, width, minX, maxX]); // eslint-disable-line react-hooks/exhaustive-deps

  const label = (x) => (useTime ? new Date(x).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "2-digit" }) : `block ${Math.round(x).toLocaleString()}`);
  const ticks = confirmed.length > 1 ? [minX, minX + span / 2, maxX] : confirmed.length ? [minX] : [];

  function pointerX(event) {
    const box = event.currentTarget.getBoundingClientRect();
    return Math.max(PAD.left, Math.min(plotRight, event.clientX - box.left));
  }
  function down(event) {
    if (event.target.tagName === "circle" || confirmed.length < 2) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const x = pointerX(event);
    setDrag({ x0: x, x1: x });
  }
  function move(event) {
    if (!drag) return;
    const x = pointerX(event); // read now: React clears currentTarget after the handler
    setDrag((d) => (d ? { ...d, x1: x } : d));
  }
  function up() {
    if (!drag) return;
    const [a, b] = [Math.min(drag.x0, drag.x1), Math.max(drag.x0, drag.x1)];
    setDrag(null);
    if (b - a < 6) return; // a click, not a drag
    onRange({ from: toX(a), to: toX(b), useTime });
  }

  const inRange = (p) => !range || p.pending || (xOf(p) >= range.from && xOf(p) <= range.to);
  const shown = points.filter(inRange).length;
  const rangePx = range && confirmed.length > 1 ? [toPx(range.from), toPx(range.to)] : null;

  return (
    <div className="inv-timeline">
      <div className="inv-timeline-head">
        <span className="small"><strong>Activity</strong> <span className="muted">{points.length} transactions{useTime ? "" : " by block"}</span></span>
        <span className="inv-timeline-key small muted">
          <i className="k-in" /> received <i className="k-out" /> sent <i className="k-other" /> other
        </span>
        {range ? (
          <button type="button" className="chip chip-on" onClick={() => onRange(null)} title="Show every transaction again">
            Showing {shown} of {points.length} · clear ×
          </button>
        ) : (
          confirmed.length > 1 && <span className="small muted">Drag across to filter the graph</span>
        )}
      </div>
      <div ref={wrapRef} className="inv-timeline-plot">
        <svg width={width} height={H} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerLeave={() => setHover(null)}
          role="img" aria-label="Transactions over time">
          <line x1={PAD.left} x2={plotRight} y1={H - PAD.bottom} y2={H - PAD.bottom} className="inv-tl-axis" />
          {ticks.map((t, i) => (
            <text key={i} x={toPx(t)} y={H - 8} className="inv-tl-tick" textAnchor={i === 0 ? "start" : i === ticks.length - 1 ? "end" : "middle"}>{label(t)}</text>
          ))}
          {hasPending && <text x={width - PAD.right - 14} y={H - 8} className="inv-tl-tick" textAnchor="middle">pending</text>}
          {rangePx && <rect x={rangePx[0]} y={PAD.top - 6} width={Math.max(2, rangePx[1] - rangePx[0])} height={H - PAD.top - PAD.bottom + 6} className="inv-tl-range" />}
          {drag && <rect x={Math.min(drag.x0, drag.x1)} y={PAD.top - 6} width={Math.abs(drag.x1 - drag.x0)} height={H - PAD.top - PAD.bottom + 6} className="inv-tl-brush" />}
          {placed.map((p) => (
            <g key={p.id} data-dot="1" className={`inv-tl-dot d-${p.dir} ${p.id === selectedId ? "sel" : ""} ${inRange(p) ? "" : "out"}`}
              onClick={() => onSelect(p.id)} onPointerEnter={() => setHover(p)} onPointerLeave={() => setHover(null)}>
              <line x1={p.cx} x2={p.cx} y1={H - PAD.bottom} y2={p.cy} className="inv-tl-stem" />
              <circle cx={p.cx} cy={p.cy} r={p.id === selectedId ? p.r + 2 : p.r} />
            </g>
          ))}
        </svg>
        {hover && (
          <div className="graph-tooltip inv-tl-tip" style={{ left: Math.min(hover.cx + 10, width - 200), top: Math.max(0, hover.cy - 52) }} role="tooltip">
            <div className="tooltip-kind">{hover.dir === "in" ? "Received" : hover.dir === "out" ? "Sent" : hover.dir === "self" ? "Sent and received" : "Transaction"}</div>
            <div className="tooltip-title">{formatBtc(hover.sats)}</div>
            <div className="small muted">
              {hover.pending ? "Not confirmed yet" : hover.time ? new Date(hover.time).toLocaleString() : `Block ${hover.height?.toLocaleString()}`}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
