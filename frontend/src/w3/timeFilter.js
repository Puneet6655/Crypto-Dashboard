// Time slider for the graph explorer: each link carries the oldest and newest transfer it stands for
// (first_time, last_time from the backend). A link is shown when that span overlaps the chosen period.
// Links without times (older cached data) are always shown.

export function edgeTimes(attrs) {
  const last = attrs.last_time ? Date.parse(attrs.last_time) : NaN;
  if (Number.isNaN(last)) return null;
  const first = attrs.first_time ? Date.parse(attrs.first_time) : NaN;
  return { first: Number.isNaN(first) ? last : first, last };
}

export function edgeOutOfTime(attrs, range) {
  if (!range) return false;
  const t = edgeTimes(attrs);
  if (!t) return false;
  return t.last < range.from || t.first > range.to;
}

// Nodes left with no link inside the period (starting addresses always stay).
export function nodesOutOfTime(graph, range) {
  if (!range) return null;
  const hidden = new Set();
  graph.forEachNode((node, a) => {
    if (a.seed) return;
    let any = false;
    let timed = false;
    graph.forEachEdge(node, (_, e) => {
      if (any) return;
      if (!edgeTimes(e)) { any = true; return; }
      timed = true;
      if (!edgeOutOfTime(e, range)) any = true;
    });
    if (timed && !any) hidden.add(node);
  });
  return hidden.size ? hidden : null;
}
