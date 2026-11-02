"""Graph measures (Milestone 8): NetworkX measures on the graph an investigation returns.

Method "graph_measures" runs the same live lookup as "live_lookup", then measures the graph it drew:

* betweenness centrality: how often a node lies on the shortest path between two other nodes. A
  high value marks a bridge, e.g. the one transaction or address that links two groups of wallets.
* PageRank on the directed money flow (input address -> transaction -> output address): where value
  tends to end up in this graph.
* degree: how many links a node has in the graph shown.
* connected components: groups of nodes with no link between them.

Each node gets betweenness, pagerank and degree in its details (the graph sizes nodes by
betweenness). The measures describe only the graph shown (the first page of transactions, capped at
MAX_GRAPH_NODES), never the whole blockchain, which the findings and limitations say.
"""
from __future__ import annotations

from datetime import datetime, timezone

import networkx as nx

from app.schemas.investigation import Evidence, Finding, InvestigationResult

METHOD_VERSION = f"graph_measures_v1 (NetworkX {nx.__version__})"
TOP = 3
# Exact betweenness is O(nodes x edges); above this many nodes we sample this many source nodes.
EXACT_BETWEENNESS_UP_TO = 400


def build(result: InvestigationResult) -> nx.DiGraph:
    g = nx.DiGraph()
    for node in result.graph.nodes:
        g.add_node(node.id)
    for edge in result.graph.edges:
        if edge.source != edge.target:
            g.add_edge(edge.source, edge.target)
    return g


def measures(g: nx.DiGraph) -> dict[str, dict[str, float]]:
    """node id -> {betweenness, pagerank, degree}. Empty for an empty graph."""
    if g.number_of_nodes() == 0:
        return {}
    undirected = g.to_undirected()
    k = None if g.number_of_nodes() <= EXACT_BETWEENNESS_UP_TO else EXACT_BETWEENNESS_UP_TO
    betweenness = nx.betweenness_centrality(undirected, k=k, normalized=True, seed=0)
    pagerank = nx.pagerank(g, alpha=0.85) if g.number_of_edges() else {n: 1 / g.number_of_nodes() for n in g}
    return {n: {"betweenness": round(betweenness[n], 4), "pagerank": round(pagerank[n], 4),
                "degree": int(undirected.degree(n))} for n in g}


def apply(result: InvestigationResult, method: str = "graph_measures") -> InvestigationResult:
    """Return a copy of a finished investigation with graph measures on every node, plus findings."""
    g = build(result)
    values = measures(g)
    labels = {n.id: n.label for n in result.graph.nodes}
    nodes = [n.model_copy(update={"details": {**n.details, **values.get(n.id, {})}}) for n in result.graph.nodes]
    graph = result.graph.model_copy(update={"nodes": nodes})

    evidence_id = f"e{len(result.evidence) + 1}"
    next_finding = len(result.findings) + 1
    components = nx.number_connected_components(g.to_undirected()) if g.number_of_nodes() else 0
    findings = [Finding(
        id=f"f{next_finding}",
        title=(f"Graph shown: {g.number_of_nodes()} nodes, {g.number_of_edges()} links, "
               f"{components} separate group{'s' if components != 1 else ''}"),
        kind="observation", entity_ids=[], evidence_ids=[evidence_id])]
    ranked = sorted((v["betweenness"], n) for n, v in values.items() if v["betweenness"] > 0)[::-1][:TOP]
    if ranked:
        findings.append(Finding(
            id=f"f{next_finding + 1}",
            title="Bridges (highest betweenness): " + "; ".join(f"{labels[n]} ({b:.2f})" for b, n in ranked),
            kind="observation", entity_ids=[n for _, n in ranked], evidence_ids=[evidence_id]))
    sinks = sorted(((v["pagerank"], n) for n, v in values.items()), reverse=True)[:TOP]
    if sinks and g.number_of_edges():
        findings.append(Finding(
            id=f"f{next_finding + 2}",
            title="Where value flows to (highest PageRank): " + "; ".join(f"{labels[n]} ({p:.2f})" for p, n in sinks),
            kind="observation", entity_ids=[n for _, n in sinks], evidence_ids=[evidence_id]))

    sampled = g.number_of_nodes() > EXACT_BETWEENNESS_UP_TO
    evidence = Evidence(
        id=evidence_id, source="NetworkX measures on the graph shown", source_url="https://networkx.org/",
        retrieved_at=datetime.now(timezone.utc), method_version=METHOD_VERSION,
        excerpt=(f"betweenness ({'sampled from ' + str(EXACT_BETWEENNESS_UP_TO) + ' nodes' if sampled else 'exact'},"
                 f" undirected), PageRank (alpha 0.85, directed), degree; {g.number_of_nodes()} nodes"))
    return result.model_copy(update={
        "method": method,
        "graph": graph,
        "findings": result.findings + findings,
        "evidence": result.evidence + [evidence],
        "limitations": result.limitations + [
            "Graph measures describe only the graph shown (first page of transactions, node limit), "
            "not the whole blockchain; a bridge here may be an ordinary exchange or service"],
        "uncertainty": {**result.uncertainty, "betweenness_sampled": sampled},
    })
