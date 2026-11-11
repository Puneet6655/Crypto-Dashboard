// Page and card animations, kept apart from the main styles so they can be
// switched off without touching anything else.
//
// On by default. Turn off with:
//   - VITE_MOTION=off in frontend/.env (or the Vercel env vars), or
//   - ?nomotion in the address, e.g. http://localhost:5173/?nomotion#/status
// To remove completely, delete the one import line in main.jsx.
import "./motion.css";

function motionWanted() {
  const env = String(import.meta.env.VITE_MOTION ?? "on").toLowerCase();
  if (env === "off" || env === "false" || env === "0") return false;
  if (new URLSearchParams(window.location.search).has("nomotion")) return false;
  return true;
}

// Same rule as hooks/useHashRoute.js: "#/cases/12" -> "cases".
function currentPage() {
  const path = window.location.hash.replace(/^#/, "") || "/investigate";
  return path.split("/").filter(Boolean)[0] || "investigate";
}

if (motionWanted()) {
  const root = document.documentElement;
  root.classList.add("mo-on");
  const markPage = () => { root.dataset.moPage = currentPage(); };
  markPage();
  window.addEventListener("hashchange", markPage);
}
