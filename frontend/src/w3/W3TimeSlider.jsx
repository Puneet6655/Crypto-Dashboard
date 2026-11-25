import { useMemo } from "react";
import { edgeTimes } from "./timeFilter.js";

// Timeline slider for the graph explorer: a bar chart of when the loaded links last moved money,
// with two handles to keep only one period. Links outside it (and addresses left with no link)
// are hidden in the graph. Works for every chain, since each link carries its first and last transfer time.
const STEPS = 1000;
const BARS = 48;

export default function W3TimeSlider({ graph, version, range, onRange }) {
  const data = useMemo(() => {
    const spans = [];
    graph.forEachEdge((_, a) => { const t = edgeTimes(a); if (t) spans.push(t); });
    if (spans.length < 2) return null;
    const min = Math.min(...spans.map((t) => t.first));
    const max = Math.max(...spans.map((t) => t.last));
    if (!(max > min)) return null;
    const bars = new Array(BARS).fill(0);
    for (const t of spans) bars[Math.min(BARS - 1, Math.floor(((t.last - min) / (max - min)) * BARS))] += 1;
    return { min, max, bars, peak: Math.max(...bars), total: spans.length };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, version]);

  if (!data) {
    return (
      <div className="w3-timeline w3-timeline-empty small muted">
        Timeline: appears once the graph has links with transfer times.
      </div>
    );
  }

  const { min, max, bars, peak, total } = data;
  const toPos = (t) => Math.round(((Math.max(min, Math.min(max, t)) - min) / (max - min)) * STEPS);
  const toTime = (p) => min + (p / STEPS) * (max - min);
  const lo = range ? toPos(range.from) : 0;
  const hi = range ? toPos(range.to) : STEPS;
  const set = (a, b) => onRange(a <= 0 && b >= STEPS ? null : { from: toTime(a), to: toTime(b) });
  const short = max - min < 3 * 86400000;
  const fmt = (t) => new Date(t).toLocaleString(undefined, short
    ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }
    : { day: "numeric", month: "short", year: "numeric" });
  const shown = range ? countIn(graph, range) : total;

  return (
    <div className="w3-timeline">
      <div className="w3-timeline-head small">
        <strong>Timeline</strong>
        <span className="muted">{fmt(toTime(lo))} to {fmt(toTime(hi))}</span>
        <span className="muted grow">{range ? `${shown.toLocaleString()} of ${total.toLocaleString()} links in this period` : "Drag the handles to show one period"}</span>
        {range && <button type="button" className="chip chip-on" onClick={() => onRange(null)} title="Show every period again">All time ×</button>}
      </div>
      <div className="w3-timeline-track">
        <div className="w3-timeline-plot">
        <svg className="w3-timeline-bars" viewBox={`0 0 ${BARS} 40`} preserveAspectRatio="none" aria-hidden="true">
          {bars.map((n, i) => {
            const h = n ? 4 + (n / peak) * 36 : 0;
            const mid = ((i + 0.5) / BARS) * STEPS;
            return <rect key={i} x={i + 0.12} width={0.76} y={40 - h} height={h} className={mid >= lo && mid <= hi ? "in" : "out"} />;
          })}
        </svg>
        <div className="w3-timeline-sel" style={{ left: `${(lo / STEPS) * 100}%`, width: `${((hi - lo) / STEPS) * 100}%` }} />
        </div>
        <input type="range" min={0} max={STEPS} value={lo} aria-label="Start of the period"
          onChange={(e) => set(Math.min(Number(e.target.value), hi - 5), hi)} />
        <input type="range" min={0} max={STEPS} value={hi} aria-label="End of the period"
          onChange={(e) => set(lo, Math.max(Number(e.target.value), lo + 5))} />
      </div>
      <div className="w3-timeline-ends small muted">
        <span>{fmt(min)}</span><span>{fmt(max)}</span>
      </div>
    </div>
  );
}

function countIn(graph, range) {
  let n = 0;
  graph.forEachEdge((_, a) => {
    const t = edgeTimes(a);
    if (t && !(t.last < range.from || t.first > range.to)) n += 1;
  });
  return n;
}
