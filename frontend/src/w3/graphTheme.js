// Web3 mode look for the Investigate graph (Cytoscape): light labels on dark tags, a soft glow under every node,
// a brighter glow on the selected one. Added after the normal stylesheet, so later rules win.
export function w3Stylesheet(base) {
  return [
    ...base,
    {
      selector: "node",
      style: {
        color: "#ede9fe",
        "text-background-color": "#140c2c",
        "text-background-opacity": 0.85,
        "text-background-shape": "roundrectangle",
        "text-background-padding": 3,
        "border-color": "#0b0718",
        "underlay-color": "#a78bfa",
        "underlay-opacity": 0.14,
        "underlay-padding": 7,
        "underlay-shape": "ellipse",
        "transition-property": "opacity, width, height, border-width, underlay-opacity, underlay-padding",
        "transition-duration": 220,
      },
    },
    { selector: 'edge[relation = "observed"], edge[relation = "reported"]', style: { "line-color": "#7d75ab", "target-arrow-color": "#7d75ab" } },
    { selector: "edge.labelled", style: { color: "#c4b5fd", "text-background-color": "#140c2c" } },
    { selector: "node:active", style: { "overlay-opacity": 0 } },
    { selector: "node.selected", style: { "border-color": "#f0abfc", "underlay-color": "#f0abfc", "underlay-opacity": 0.28, "underlay-padding": 10 } },
    { selector: "edge.selected", style: { "line-color": "#f0abfc", "target-arrow-color": "#f0abfc" } },
    { selector: ".match", style: { "border-color": "#fde047", "underlay-color": "#fde047", "underlay-opacity": 0.35 } },
    { selector: ".faded", style: { opacity: 0.12 } },
    { selector: ".filtered", style: { display: "none" } },
  ];
}

// Entrance: the layout is worked out first without drawing, then every node flies out from the middle to its
// place while fading in one after another, and the links fade in last.
export const w3StartLayout = { name: "grid" };

export function w3Hide(cy) {
  cy.elements().style("opacity", 0);
}

export function w3Entrance(cy, layoutOptions) {
  const layout = cy.elements(":visible").layout({ ...layoutOptions, animate: false, fit: true, padding: 40 });
  layout.one("layoutstop", () => {
    const nodes = cy.nodes(":visible");
    const final = new Map(nodes.map((n) => [n.id(), { ...n.position() }]));
    const ext = cy.extent();
    const centre = { x: (ext.x1 + ext.x2) / 2, y: (ext.y1 + ext.y2) / 2 };
    const step = Math.max(6, Math.min(40, 700 / Math.max(1, nodes.length)));
    nodes.positions(() => ({ x: centre.x + (Math.random() - 0.5) * 24, y: centre.y + (Math.random() - 0.5) * 24 }));
    nodes.forEach((n, i) => {
      n.delay(i * step).animate(
        { position: final.get(n.id()), style: { opacity: 1 } },
        { duration: 900, easing: "ease-out-cubic", complete: () => n.removeStyle("opacity") },
      );
    });
    const edgesAt = Math.min(900, nodes.length * step) + 450;
    cy.edges().delay(edgesAt).animate({ style: { opacity: 1 } }, { duration: 500 });
    // A selection made during the entrance changes classes, which can cut a node's fade short;
    // so once the show is over, make sure everything is fully visible whatever happened.
    setTimeout(() => {
      if (cy.destroyed()) return;
      cy.elements().stop(false, true);
      cy.elements().removeStyle("opacity");
    }, edgesAt + 600);
  });
  layout.run();
  return layout;
}
