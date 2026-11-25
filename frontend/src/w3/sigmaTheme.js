// Soft (neumorphism) look for the graph explorer (Sigma, WebGL): light hover cards, dim links and
// calm, saturated node colours on a light surface.

export const W3_FADED = "#d3dae5";
export const W3_EDGE = "#b4bfd0";
export const W3_EDGE_IN = "#0f9f8f";   // money coming into the focused node
export const W3_EDGE_OUT = "#d94fa8";  // money leaving it

// Brighter versions of the explorer's node colours, so they glow on dark (Soft: calm, saturated tones on light).
export const W3_KIND_COLORS = {
  seed: "#6d5dfc",
  address: "#3d7bf2",
  frontier: "#9fb6e8",
  exchange: "#d94fa8",
  hub: "#0f9f8f",
  listed: "#e5484d",
  dust: "#a0a9b8",
  more: "#e09b1a",
  transaction: "#8b5cf6",
  other: "#64748b",
};

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

// Hovered node: a glowing ring plus a dark glass label card (Sigma's default is a white box).
function drawHover(ctx, data, settings) {
  const size = settings.labelSize;
  ctx.font = `600 ${size}px ${settings.labelFont}`;
  const label = data.label ?? "";
  const w = label ? ctx.measureText(label).width + 16 : 0;
  ctx.save();
  ctx.shadowColor = data.color;
  ctx.shadowBlur = 18;
  ctx.beginPath();
  ctx.arc(data.x, data.y, data.size + 4, 0, Math.PI * 2);
  ctx.strokeStyle = data.color;
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.restore();
  if (!label) return;
  const x = data.x + data.size + 8;
  const y = data.y - size / 2 - 6;
  ctx.save();
  ctx.shadowColor = "rgba(163, 177, 198, 0.7)";
  ctx.shadowBlur = 12;
  roundRect(ctx, x, y, w, size + 12, 7);
  ctx.fillStyle = "rgba(236, 240, 246, 0.97)";
  ctx.fill();
  ctx.restore();
  ctx.strokeStyle = "rgba(255, 255, 255, 0.9)";
  ctx.lineWidth = 1;
  roundRect(ctx, x, y, w, size + 12, 7);
  ctx.stroke();
  ctx.fillStyle = "#2f3747";
  ctx.fillText(label, x + 8, data.y + size / 3);
}

export const W3_SIGMA_SETTINGS = {
  labelColor: { color: "#3b4458" },
  labelFont: "Inter, system-ui, sans-serif",
  labelSize: 12,
  labelWeight: "500",
  defaultDrawNodeHover: drawHover,
  defaultEdgeColor: W3_EDGE,
};
