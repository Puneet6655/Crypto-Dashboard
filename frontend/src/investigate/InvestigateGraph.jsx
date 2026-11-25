import { Suspense, lazy } from "react";
import GraphView from "../components/GraphView.jsx";

// Investigate's graph area: the graph explorer, started from the investigation's own graph.
// While nothing is looked up yet, or a lookup is running or failed, GraphView shows that state.

// The explorer pulls in WebGL drawing (Sigma) and a layout worker: load it only once there is a result.
const GraphExplorer = lazy(() => import("./GraphExplorer.jsx"));

export default function InvestigateGraph(props) {
  const { status, result, onSelect, expand: inv } = props;
  if (status !== "done" || !result) return <GraphView {...props} />;
  // A node picked in the explorer is selected in Investigate too, so the risk and details panels follow.
  const pick = (id) => {
    if (id && result.graph.nodes.some((n) => n.id === id)) onSelect(id);
  };
  return (
    <div className="inv-graph-area">
      <Suspense fallback={<section className="card graph state">Loading the graph explorer...</section>}>
        <GraphExplorer key={result.investigation_id} inv={inv} onNodePick={pick} />
      </Suspense>
    </div>
  );
}
