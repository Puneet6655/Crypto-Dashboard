// Web3 mode look for the graph explorer (Sigma, WebGL): light labels, dark hover cards, dim links,
// and node colours tuned for a dark background. The Soft (neumorphism) look swaps in light-surface colours.
import { NEU_DESIGN } from "../config/w3Design.js";

export const W3_FADED = NEU_DESIGN ? "#d3dae5" : "#2a2342";
export const W3_EDGE = NEU_DESIGN ? "#b4bfd0" : "#4b4470";
export const W3_EDGE_IN = NEU_DESIGN ? "#0f9f8f" : "#5eead4";   // money coming into the focused node
export const W3_EDGE_OUT = NEU_DESIGN ? "#d94fa8" : "#f0abfc";  // money leaving it

// Brighter versions of the explorer's node colours, so they glow on dark (Soft: calm, saturated tones on light).
export const W3_KIND_COLORS = NEU_DESIGN ? {
  seed: "#6d5dfc",
  address: "#3d7bf2",
  frontier: "#9fb6e8",
  exchange: "#d94fa8",
  hub: "#0f9f8f",
  listed: "#e5484d",
  dust: "#a0a9b8",
  more: "#e09b1a",
} : {
  seed: "#c084fc",
  address: "#60a5fa",
  frontier: "#3b4f86",
  exchange: "#f472b6",
  hub: "#2dd4bf",
  listed: "#f87171",
  dust: "#6b7280",
  more: "#fbbf24",
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
  ctx.shadowColor = NEU_DESIGN ? "rgba(163, 177, 198, 0.7)" : "rgba(0,0,0,0.5)";
  ctx.shadowBlur = 12;
  roundRect(ctx, x, y, w, size + 12, 7);
  ctx.fillStyle = NEU_DESIGN ? "rgba(236, 240, 246, 0.97)" : "rgba(24, 16, 52, 0.95)";
  ctx.fill();
  ctx.restore();
  ctx.strokeStyle = NEU_DESIGN ? "rgba(255, 255, 255, 0.9)" : "rgba(196, 181, 253, 0.35)";
  ctx.lineWidth = 1;
  roundRect(ctx, x, y, w, size + 12, 7);
  ctx.stroke();
  ctx.fillStyle = NEU_DESIGN ? "#2f3747" : "#f1eefc";
  ctx.fillText(label, x + 8, data.y + size / 3);
}

export const W3_SIGMA_SETTINGS = {
  labelColor: { color: NEU_DESIGN ? "#3b4458" : "#e9e5ff" },
  labelFont: "Inter, system-ui, sans-serif",
  labelSize: 12,
  labelWeight: "500",
  defaultDrawNodeHover: drawHover,
  defaultEdgeColor: W3_EDGE,
};
