// Right column: details for the node the analyst clicked.
export default function EntityDetails({ result, selectedId }) {
  if (!result) {
    return <aside className="card details muted">Select a node in the graph to see its details.</aside>;
  }
  const node = result.graph.nodes.find((n) => n.id === selectedId);
  if (!node) {
    return <aside className="card details muted">Select a node in the graph to see its details.</aside>;
  }
  const links = result.graph.edges.filter((e) => e.source === node.id || e.target === node.id);
  const findings = result.findings.filter((f) => f.entity_ids.includes(node.id));

  return (
    <aside className="card details" aria-label="Selected entity">
      <h2>Selected entity</h2>
      <p className="mono small">{node.id}</p>
      <dl>
        <dt>Type</dt>
        <dd>{node.type}</dd>
        <dt>Links</dt>
        <dd>{links.length}</dd>
        <dt>Findings</dt>
        <dd>{findings.length ? findings.map((f) => f.title).join("; ") : "None"}</dd>
        <dt>Data</dt>
        <dd>{result.data_mode}</dd>
      </dl>
    </aside>
  );
}
