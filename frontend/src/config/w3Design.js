// Design switch, shared by the files that change with it. Three looks:
//   "neu"     Soft UI (neumorphism): light, raised and pressed surfaces. Uses the interactive Web3 layout.
//   "web3"    Dark glass with the animated backdrop and the hidden side menu.
//   "classic" The original dashboard, which still works as before.
// Order of precedence: ?neu / ?web3 / ?classic in the URL, then the choice saved with the picker in the side menu
// (this browser only), then VITE_LOGIN_DESIGN (set on Vercel: "neu" or "web3"; anything else means classic).
const STORAGE_KEY = "pd_design";

export const DESIGNS = [
  { id: "neu", label: "Soft", hint: "Neumorphism: light, raised and pressed surfaces" },
  { id: "web3", label: "Web3", hint: "Dark glass with the animated backdrop" },
  { id: "classic", label: "Classic", hint: "The original dashboard" },
];
const IDS = DESIGNS.map((d) => d.id);

function pickDesign() {
  const params = new URLSearchParams(window.location.search);
  const fromUrl = ["classic", "neu", "web3"].find((id) => params.has(id));
  if (fromUrl) return fromUrl;
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (IDS.includes(saved)) return saved;
  } catch {
    // storage blocked: fall through to the build setting
  }
  const env = import.meta.env.VITE_LOGIN_DESIGN;
  return IDS.includes(env) ? env : "classic";
}

export const DESIGN = pickDesign();
// The interactive layout (smart search, risk panel, graph tools, alert monitor) is shared by Web3 and Soft.
export const WEB3_DESIGN = DESIGN !== "classic";
// Only the Web3 look: animated backdrop, edge-reveal side menu, particle login page.
export const WEB3_LOOK = DESIGN === "web3";
export const NEU_DESIGN = DESIGN === "neu";

// Save a design for this browser and reload with it (the look is chosen once, at start-up).
export function chooseDesign(id) {
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // storage blocked: the URL parameter below still switches for this visit
  }
  const url = new URL(window.location.href);
  ["classic", "neu", "web3"].forEach((p) => url.searchParams.delete(p));
  if (!canStore()) url.searchParams.set(id, "");
  const next = url.toString().replace(/=(?=&|#|$)/, "");
  if (next === window.location.href) window.location.reload();
  else window.location.assign(next);
}

function canStore() {
  try {
    localStorage.setItem(`${STORAGE_KEY}_t`, "1");
    localStorage.removeItem(`${STORAGE_KEY}_t`);
    return true;
  } catch {
    return false;
  }
}
