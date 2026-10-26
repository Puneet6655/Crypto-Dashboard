// Centre column. Shows the loading, error and empty states, or the graph.
// Milestone 8 replaces the simple list below with an interactive Cytoscape.js graph.
const RELATION_HELP = {
  observed: "seen directly in the source data",
  reported: "stated by a log or a third party",
  inferred: "our interpretation, not proof",
};

export default function GraphView({ status, error, result, selectedId, onSelect }) {
  if (status === "idle") {
    return <section className="card graph state">Choose a mode and a case, then press Start Investigation.</section>;
  }
  if (status === "loading") {
    return <section className="card graph state" role="status">Loading investigation...</section>;
  }
  if (status === "error") {
    return <section className="card graph state error" role="alert">{error}</section>;
  }
  if (status === "empty") {
    return (
      <section className="card graph state">
        No demo data exists for this input. Live lookups arrive in Milestone 3.
      </section>
    );
  }

  const { nodes, edges } = result.graph;
  const labelOf = (id) => nodes.find((n) => n.id === id)?.label ?? id;

  return (
    <section className="card graph" aria-label="Relationship graph">
      <h2>Graph ({nodes.length} nodes, {edges.length} links)</h2>
      <div className="node-list">
        {nodes.map((node) => (
          <button
            key={node.id}
            type="button"
            className={`node node-${node.type} ${node.id === selectedId ? "selected" : ""}`}
            onClick={() => onSelect(node.id)}
          >
            <span className="node-type">{node.type}</span>
            {node.label}
          </button>
        ))}
      </div>
      <ul className="edge-list">
        {edges.map((edge) => (
          <li key={edge.id}>
            {labelOf(edge.source)} &rarr; {labelOf(edge.target)}: {edge.label}{" "}
            <span className={`relation relation-${edge.relation}`} title={RELATION_HELP[edge.relation]}>
              {edge.relation}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
