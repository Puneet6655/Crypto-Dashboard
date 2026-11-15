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
  if (node.listed?.length) details.listed_on = node.listed;
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

  if (position.has(centerId)) {
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
    edges.push({ id, source, target: targetId, relation: "observed", label: edgeLabel(edge), evidence_ids: [evidenceId] });
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
