import { useState } from "react";
import { nodeTypeInfo, relationInfo } from "../config/graphStyle.js";
import { caseItemAttrs, itemFromNode } from "../cases/caseItems.js";
import { AddToCaseButton } from "./AddToCaseMenu.jsx";

// Right column: details for whatever the analyst clicked, a node or a link.
// Every related item is a button, so you can walk the graph from here too.
export default function EntityDetails({ result, selectedId, onSelect, onEvidenceClick }) {
  const node = result?.graph.nodes.find((n) => n.id === selectedId);
  const edge = !node ? result?.graph.edges.find((e) => e.id === selectedId) : null;

  if (!node && !edge) {
    return <aside className="card details muted">Click a node or a link in the graph to see its details.</aside>;
  }
  return (
    <aside className="card details" aria-label="Selected entity">
      {node ? (
        <NodeDetails node={node} result={result} onSelect={onSelect} onEvidenceClick={onEvidenceClick} />
      ) : (
        <EdgeDetails edge={edge} result={result} onSelect={onSelect} onEvidenceClick={onEvidenceClick} />
      )}
    </aside>
  );
}

function NodeDetails({ node, result, onSelect, onEvidenceClick }) {
  const info = nodeTypeInfo(node.type);
  const { nodes, edges } = result.graph;
  const labelOf = (id) => nodes.find((n) => n.id === id)?.label ?? id;
  const links = edges.filter((e) => e.source === node.id || e.target === node.id);
  const findings = result.findings.filter((f) => f.entity_ids.includes(node.id));

  // Group links by the node at the other end and the direction, so 131
  // inputs from one address show as one row instead of 131.
  const neighbours = [];
  const byKey = new Map();
  for (const e of links) {
    const outgoing = e.source === node.id;
    const otherId = outgoing ? e.target : e.source;
    const key = `${outgoing ? "out" : "in"}|${otherId}`;
    if (!byKey.has(key)) {
      byKey.set(key, { key, otherId, outgoing, links: [] });
      neighbours.push(byKey.get(key));
    }
    byKey.get(key).links.push(e);
  }

  return (
    <>
      <h2>
        <span className="swatch" style={{ background: info.color }} /> {info.label}
      </h2>
      <p className="entity-label" {...caseItemAttrs(itemFromNode(node))}>{node.label}</p>
      <CopyableId id={node.id} />
      <div className="details-actions">
        <AddToCaseButton item={itemFromNode(node)} />
      </div>

      <dl>
        {Object.entries(node.details ?? {}).map(([key, value]) => (
          <div key={key} className="contents">
            <dt>{key.replaceAll("_", " ")}</dt>
            <dd>{value === null ? "none" : typeof value === "number" ? value.toLocaleString() : String(value)}</dd>
          </div>
        ))}
        <dt>Data</dt>
        <dd>{result.data_mode}</dd>
      </dl>

      <h3>Findings ({findings.length})</h3>
      {findings.length ? (
        <ul className="compact-list">
          {findings.map((f) => (
            <li key={f.id}>
              {f.title}{" "}
              {f.evidence_ids.map((id) => (
                <button key={id} type="button" className="link" onClick={() => onEvidenceClick(id)}>{id}</button>
              ))}
            </li>
          ))}
        </ul>
      ) : (
        <p className="small muted">None</p>
      )}

      <h3>Connected to ({neighbours.length})</h3>
      <ul className="compact-list">
        {neighbours.map(({ key, otherId, outgoing, links: group }) => (
          <li key={key}>
            <span className="muted">{outgoing ? "→" : "←"}</span>{" "}
            <button
              type="button"
              className="link"
              onClick={() => onSelect(otherId)}
              {...caseItemAttrs(itemFromNode(nodes.find((n) => n.id === otherId)))}
            >
              {labelOf(otherId)}
            </button>
            <div className="small muted">
              {group.length === 1 ? group[0].label : `${group.length} links, e.g. ${group[0].label}`}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

function EdgeDetails({ edge, result, onSelect, onEvidenceClick }) {
  const info = relationInfo(edge.relation);
  const labelOf = (id) => result.graph.nodes.find((n) => n.id === id)?.label ?? id;
  // Other links between the same two nodes (drawn as one bundled line).
  const siblings = result.graph.edges.filter(
    (e) => e.source === edge.source && e.target === edge.target && e.relation === edge.relation
  );
  return (
    <>
      <h2>Link</h2>
      <p className="entity-label">{edge.label}</p>
      <dl>
        <dt>From</dt>
        <dd><button type="button" className="link" onClick={() => onSelect(edge.source)}>{labelOf(edge.source)}</button></dd>
        <dt>To</dt>
        <dd><button type="button" className="link" onClick={() => onSelect(edge.target)}>{labelOf(edge.target)}</button></dd>
        <dt>Relation</dt>
        <dd>{info.label}: {info.help}</dd>
        <dt>Evidence</dt>
        <dd>
          {edge.evidence_ids?.length
            ? edge.evidence_ids.map((id) => (
                <button key={id} type="button" className="link" onClick={() => onEvidenceClick(id)}>{id}</button>
              ))
            : "none"}
        </dd>
      </dl>
      {siblings.length > 1 && (
        <>
          <h3>All {siblings.length} links on this line</h3>
          <ul className="compact-list scroll-list">
            {siblings.map((e) => (
              <li key={e.id} className={e.id === edge.id ? "selected-row" : ""}>
                <button type="button" className="link" onClick={() => onSelect(e.id)}>{e.label}</button>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

// Long IDs are hard to select by hand, so offer a one-click copy.
function CopyableId({ id }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(id);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be blocked (for example on plain http); the ID is still selectable.
    }
  }
  return (
    <div className="copy-row">
      <span className="mono small">{id}</span>
      <button type="button" className="secondary small-button" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
    </div>
  );
}
