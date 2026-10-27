// One place for how the graph looks. The legend, the filters and the
// Cytoscape stylesheet all read from these two objects, so adding a new
// node type or relation here updates every part of the screen at once.

// Node types the backend and fixtures can send. "shape" is a Cytoscape shape name.
export const NODE_TYPES = {
  address: { label: "Address", color: "#2563eb", shape: "ellipse" },
  transaction: { label: "Transaction", color: "#7c3aed", shape: "round-rectangle" },
  domain: { label: "Website", color: "#ea580c", shape: "diamond" },
  service: { label: "Service", color: "#0d9488", shape: "hexagon" },
  payment: { label: "Payment", color: "#16a34a", shape: "tag" },
};
const UNKNOWN_TYPE = { label: "Other", color: "#6b7280", shape: "ellipse" };
export const nodeTypeInfo = (type) => NODE_TYPES[type] ?? { ...UNKNOWN_TYPE, label: type };

// How sure we are about a link. The line style carries the meaning, so the
// graph still reads correctly when printed in black and white.
export const RELATIONS = {
  observed: { label: "Observed", help: "seen directly in the source data", lineStyle: "solid", color: "#64748b" },
  reported: { label: "Reported", help: "stated by a log or a third party", lineStyle: "dotted", color: "#64748b" },
  inferred: { label: "Inferred", help: "our interpretation, not proof", lineStyle: "dashed", color: "#d97706" },
};
export const relationInfo = (relation) =>
  RELATIONS[relation] ?? { label: relation, help: "", lineStyle: "solid", color: "#64748b" };

// Layouts the analyst can pick. Each value is passed straight to cy.layout().
export const LAYOUTS = {
  cose: { label: "Force (spread out)", options: { name: "cose", nodeRepulsion: 9000, idealEdgeLength: 90 } },
  breadthfirst: { label: "Flow (top to bottom)", options: { name: "breadthfirst", directed: true, spacingFactor: 1.3 } },
  concentric: { label: "Rings (busiest in middle)", options: { name: "concentric", minNodeSpacing: 40 } },
  circle: { label: "Circle", options: { name: "circle" } },
  grid: { label: "Grid", options: { name: "grid" } },
};

const ACCENT = "#2563eb";
const MATCH = "#eab308";

// The Cytoscape stylesheet. It works like CSS: a selector, then style rules.
// Later rules win over earlier ones, so the state classes (.selected, .faded)
// come last. Docs: https://js.cytoscape.org/#style
export function buildStylesheet() {
  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        "font-size": 11,
        color: "#1c1f24",
        "text-valign": "bottom",
        "text-margin-y": 6,
        "text-background-color": "#ffffff",
        "text-background-opacity": 0.85,
        "text-background-padding": 2,
        width: 30,
        height: 30,
        "border-width": 2,
        "border-color": "#ffffff",
        "background-color": UNKNOWN_TYPE.color,
        "transition-property": "opacity, width, height, border-width",
        "transition-duration": 150,
      },
    },
    // One rule per node type, generated from NODE_TYPES above.
    ...Object.entries(NODE_TYPES).map(([type, info]) => ({
      selector: `node[type = "${type}"]`,
      style: { "background-color": info.color, shape: info.shape },
    })),
    {
      selector: "edge",
      style: {
        width: 2,
        "curve-style": "bezier",
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.9,
        "line-color": RELATIONS.observed.color,
        "target-arrow-color": RELATIONS.observed.color,
        "transition-property": "opacity, width",
        "transition-duration": 150,
      },
    },
    ...Object.entries(RELATIONS).map(([relation, info]) => ({
      selector: `edge[relation = "${relation}"]`,
      style: { "line-style": info.lineStyle, "line-color": info.color, "target-arrow-color": info.color },
    })),
    {
      // Bundled links: the more links inside, the thicker the line (3px to 10px).
      selector: "edge[count > 1]",
      style: { width: "mapData(count, 2, 60, 3, 10)" },
    },
    {
      // Added to every edge when "Show link labels" is ticked.
      selector: "edge.labelled",
      style: {
        label: "data(label)",
        "font-size": 9,
        color: "#475569",
        "text-rotation": "autorotate",
        "text-background-color": "#ffffff",
        "text-background-opacity": 0.9,
        "text-background-padding": 1,
      },
    },
    { selector: ".match", style: { "border-color": MATCH, "border-width": 5 } },
    { selector: "node.selected", style: { "border-color": ACCENT, "border-width": 5, width: 40, height: 40 } },
    { selector: "edge.selected", style: { "line-color": ACCENT, "target-arrow-color": ACCENT, width: 4 } },
    { selector: ".faded", style: { opacity: 0.15 } },
    { selector: ".filtered", style: { display: "none" } },
  ];
}
