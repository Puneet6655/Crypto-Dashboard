// "Expand node" on the Investigate graph: fetch an address's counterparties with the same
// streamed endpoint advanced graph mode uses (POST /api/graph/expand, one hop) and merge
// them into the investigation result, so the graph, details panel and evidence tab all see them.
//
// The backend groups counterparties the same way as graph mode: sanctioned or scam-listed and
// exchange addresses always on their own, the biggest counterparties next, and the rest folded
// into one "+N more" group (and tiny transfers into a "dust" group) so one click cannot flood the graph.

export const EXPAND_CHAINS = ["bitcoin", "ethereum", "base", "solana"];
export const EXPAND_NODE_CAP = 1500; // Cytoscape stays smooth up to about this many nodes
export const EXPAND_PER_NODE = 40; // counterparties drawn per expanded address before "+N more"

// { chain, address, prefix } when the node is a live address that can be expanded, else null.
export function expandableAddress(result, nodeId) {
  if (!result || result.data_mode !== "live" || !nodeId) return null;
  const node = result.graph.nodes.find((n) => n.id === nodeId);
  if (!node || node.type !== "address") return null;
  const match = /^([a-z0-9]+):([^:]+):address:(.+)$/.exec(node.id);
  if (!match || !EXPAND_CHAINS.includes(match[1])) return null;
  return { chain: match[1], address: match[3], prefix: `${match[1]}:${match[2]}:address:` };
}

// Graph mode names nodes "<chain>:mainnet:address:<addr>"; use the investigation's own prefix
// so a counterparty already in the graph is recognised instead of drawn twice.
function remapper(target) {
  const graphPrefix = `${target.chain}:mainnet:address:`;
  return (id) => (id.startsWith(graphPrefix) ? target.prefix + id.slice(graphPrefix.length) : id);
}

const short = (text) => (text.length > 15 ? `${text.slice(0, 6)}...${text.slice(-6)}` : text);
const amount = (value) => Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: 8 });

function edgeLabel(edge) {
  const txs = `${edge.tx_count} tx${edge.tx_count === 1 ? "" : "s"}`;
  return `paid ${amount(edge.value)} ${edge.unit}, ${txs}${edge.tokens ? `, ${edge.tokens}` : ""}`;
}

function convertNode(node, mapId, expandedLabel) {
  if (node.kind === "dust" || node.kind === "more") {
    // A group of counterparties that were not drawn one by one.
    const sample = (node.members ?? []).slice(0, 10)
      .map((m) => `${short(m.address)} (sent ${amount(m.sent)}, received ${amount(m.received)}, ${m.tx_count} tx)`);
    return {
      id: mapId(node.id),
      type: "service",
      label: node.kind === "dust" ? `${node.count.toLocaleString()} dust / zero-value` : `+${node.count.toLocaleString()} more`,
      details: {
        group: node.kind === "dust" ? "tiny or zero-value counterparties (often spam or address poisoning)"
          : "smaller counterparties not drawn one by one",
        counterparties: node.count,
        of_address: expandedLabel,
        largest: sample,
        open_in: "Advanced graph mode can open this group",
      },
    };
  }
  const details = { found_by: `expanding ${expandedLabel}` };
  if (node.exchange) details.exchange = node.exchange.name || node.exchange.exchange;
  // listed_in is the lookup's own key (the AI analyst reads it); listed_on kept for older code.
  if (node.listed?.length) { details.listed_on = node.listed; details.listed_in = node.listed; }
  return { id: mapId(node.id), type: "address", label: node.label, details };
}

// A new result with one streamed batch merged in. Nodes and links already present are kept as they are.
// `added` ({ nodes: Set, links: Set }) collects what this expansion really added.
export function mergeExpansion(result, batch, target, evidenceId, added) {
  const mapId = remapper(target);
  const center = batch.expanded;
  const centerId = mapId(center.id);
  const nodes = [...result.graph.nodes];
  const position = new Map(nodes.map((n, i) => [n.id, i]));

  if (!position.has(centerId)) {
    // The expanded address was not in the graph yet (e.g. found by the explorer): add it, so its links are kept.
    position.set(centerId, nodes.length);
    nodes.push({ id: centerId, type: "address", label: center.label ?? short(center.address), details: {} });
    added.nodes.add(centerId);
  }
  {
    const i = position.get(centerId);
    nodes[i] = {
      ...nodes[i],
      details: {
        ...nodes[i].details,
        expanded: `${center.counterparties.toLocaleString()} counterparties in ${center.tx_seen.toLocaleString()} records`,
        ...(center.hub_reason ? { busy_address: center.hub_reason } : {}),
        ...(center.exchange ? { exchange: center.exchange.name || center.exchange.exchange } : {}),
        ...(center.note ? { history_note: center.note } : {}),
        expand_source: center.source,
        ...(center.listed?.length ? { listed_in: center.listed, listed_on: center.listed } : {}),
      },
    };
  }
  const centerLabel = nodes[position.get(centerId)]?.label ?? short(center.address);
  for (const node of batch.nodes) {
    const converted = convertNode(node, mapId, centerLabel);
    if (position.has(converted.id)) continue;
    position.set(converted.id, nodes.length);
    nodes.push(converted);
    added.nodes.add(converted.id);
  }

  const edges = [...result.graph.edges];
  const edgeIds = new Set(edges.map((e) => e.id));
  for (const edge of batch.edges) {
    const source = mapId(edge.source);
    const targetId = mapId(edge.target);
    const id = `expand:${source}>${targetId}`;
    if (edgeIds.has(id) || !position.has(source) || !position.has(targetId)) continue;
    edgeIds.add(id);
    // Direction is money flow: source paid target. The numbers and times ride along for the analyst and the timeline.
    edges.push({ id, source, target: targetId, relation: "observed", label: edgeLabel(edge), evidence_ids: [evidenceId],
      value: edge.value ?? 0, unit: edge.unit, tx_count: edge.tx_count, first_time: edge.first_time ?? null, last_time: edge.last_time ?? null });
    added.links.add(id);
  }

  const evidence = result.evidence.some((e) => e.id === evidenceId) ? result.evidence : [
    ...result.evidence,
    {
      id: evidenceId,
      source: `${center.source} (expanded ${centerLabel})`,
      source_url: `POST /api/graph/expand ${target.chain} ${target.address}`,
      retrieved_at: new Date().toISOString(),
      method_version: "graph-expand-1",
      excerpt: `${center.counterparties} counterparties found in the newest ${center.tx_seen} records`
        + (center.note ? `. ${center.note}` : ""),
    },
  ];
  return { ...result, graph: { ...result.graph, nodes, edges }, evidence };
}

// "Load older transactions" (address lookups on all four chains): merge the next older activity, which comes in
// the lookup's own shape (transaction nodes, sender -> tx -> receiver edges), and keep the limitation and counts honest.
export function mergeOlderTransactions(result, older) {
  const nodes = [...result.graph.nodes];
  const position = new Map(nodes.map((n, i) => [n.id, i]));
  let newTx = 0;
  for (const node of older.graph.nodes) {
    if (position.has(node.id)) {
      const listed = node.details?.listed_in;
      if (listed?.length) {
        const i = position.get(node.id);
        nodes[i] = { ...nodes[i], details: { ...nodes[i].details, listed_in: listed } };
      }
      continue;
    }
    position.set(node.id, nodes.length);
    nodes.push(node);
    if (node.type === "transaction") newTx += 1;
  }
  const edgeIds = new Set(result.graph.edges.map((e) => e.id));
  const edges = [...result.graph.edges, ...older.graph.edges.filter((e) => !edgeIds.has(e.id))];
  const loaded = (result.uncertainty?.older_transactions_loaded ?? 0) + newTx;
  const uncertainty = { ...result.uncertainty, older_transactions_loaded: loaded };
  if (result.uncertainty?.transactions_shown != null) uncertainty.transactions_shown = result.uncertainty.transactions_shown + newTx;
  delete uncertainty.older_after_txid;
  if (older.next_cursor) uncertainty.older_cursor = older.next_cursor;
  else delete uncertainty.older_cursor;
  const shown = uncertainty.transactions_shown;
  const total = result.uncertainty?.transactions_total;
  const line = older.complete
    ? `Load older transactions added ${loaded} older transactions and reached the start of the address's history`
    : shown != null
      ? `The graph shows the newest ${shown} transactions${total ? ` of ${total}` : ""}; use Load older transactions under the graph for more`
      : `Load older transactions added ${loaded} older transactions; older activity than that is not included yet`;
  const limitations = result.limitations.filter((l) => !l.startsWith("The graph shows the newest") && !l.startsWith("Load older transactions added"));
  limitations.push(line);
  const evidence = result.evidence.some((e) => e.id === older.evidence.id) ? result.evidence : [...result.evidence, older.evidence];
  return { ...result, graph: { ...result.graph, nodes, edges }, evidence, limitations, uncertainty };
}
