import { useEffect, useState } from "react";

// Icons. Each page icon can be replaced by your own image: save it as
// frontend/public/icons/<name>.png (see ICON_PROMPTS.md) and it is used automatically.
// Until a file exists, the simple line drawing below is shown instead.
export const IMAGE_ICONS = ["brand", "investigate", "cases", "status", "sources", "bigquery", "users", "account"];

const PATHS = {
  brand: "M12 2 3 6v6c0 5 3.8 9.4 9 10 5.2-.6 9-5 9-10V6l-9-4Zm-3 9.5 2 2 4-4.5",
  investigate: "M10.5 3a7.5 7.5 0 1 0 4.6 13.4L21 22M10.5 7v7M7 10.5h7",
  cases: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Zm6 6h6",
  status: "M3 12h4l2-6 4 12 2-6h6",
  sources: "M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2V5Zm0 16a2 2 0 0 1 2-2h12M8 7h6M8 11h4",
  bigquery: "M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3Zm-8 3v6c0 1.7 3.6 3 8 3m8-9v4M4 12v6c0 1.7 3.6 3 8 3m5-5.5 2 2 3.5-3.5",
  users: "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 10c0-3.3 3.1-6 7-6s7 2.7 7 6M16 3.5a4 4 0 0 1 0 7.5M18 15c2.4.6 4 2.9 4 6",
  account: "M12 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10Zm-8 10c0-4.4 3.6-7 8-7s8 2.6 8 7",
  logout: "M15 4h4v16h-4M10 17l5-5-5-5M15 12H3",
  plus: "M12 5v14M5 12h14",
  wallet: "M3 7a2 2 0 0 1 2-2h13v4M3 7v10a2 2 0 0 0 2 2h15V9H5a2 2 0 0 1-2-2Zm14 7h.01",
  transaction: "M4 8h13l-3-3M20 16H7l3 3",
  globe: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm-9-9h18M12 3c2.5 2.5 3.8 5.5 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.5-3.8-9S9.5 5.5 12 3Z",
  payment: "M3 6h18v12H3V6Zm0 4h18M7 15h3",
  tag: "M3 12V4h8l10 10-8 8L3 12Zm5-4h.01",
  check: "M5 12.5 10 17l9-10",
  bell: "M6 16V11a6 6 0 1 1 12 0v5l2 2H4l2-2Zm4 4a2 2 0 0 0 4 0",
  alert: "M12 3 2 20h20L12 3Zm0 6v5m0 3h.01",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  database: "M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3ZM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  server: "M4 4h16v6H4V4Zm0 10h16v6H4v-6Zm4-7h.01M8 17h.01",
  shield: "M12 2 3 6v6c0 5 3.8 9.4 9 10 5.2-.6 9-5 9-10V6l-9-4Z",
  external: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  edit: "M4 20h4L19 9l-4-4L4 16v4Zm9-13 4 4",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13",
  up: "M12 19V5M6 11l6-6 6 6",
  down: "M12 5v14M6 13l6 6 6-6",
  close: "M6 6l12 12M18 6 6 18",
  menu: "M4 6h16M4 12h16M4 18h16",
  copy: "M9 9h11v11H9V9ZM5 15H4V4h11v1",
  search: "M10.5 18a7.5 7.5 0 1 0 0-15 7.5 7.5 0 0 0 0 15ZM21 21l-5.2-5.2",
  lock: "M6 11h12v10H6V11Zm2 0V7a4 4 0 1 1 8 0v4",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-14v5l3 3",
  play: "M7 4v16l13-8L7 4Z",
  download: "M12 4v12m-5-5 5 5 5-5M4 20h16",
  construction: "M3 20h18M5 20l3-12h8l3 12M7 14h10M12 4v4",
};

// Which image files exist, checked once per page load.
const imageState = new Map();

function useImage(name) {
  const [state, setState] = useState(() => imageState.get(name) ?? "unknown");
  useEffect(() => {
    if (!IMAGE_ICONS.includes(name) || imageState.get(name) === "missing") return;
    if (imageState.get(name) === "loaded") {
      setState("loaded");
      return;
    }
    let alive = true;
    const img = new Image();
    img.onload = () => {
      imageState.set(name, "loaded");
      if (alive) setState("loaded");
    };
    img.onerror = () => {
      imageState.set(name, "missing");
      if (alive) setState("missing");
    };
    img.src = `/icons/${name}.png`;
    return () => {
      alive = false;
    };
  }, [name]);
  return state === "loaded";
}

export default function Icon({ name, size = 18, className = "", title }) {
  const hasImage = useImage(name);
  if (hasImage) {
    return (
      <img
        src={`/icons/${name}.png`}
        width={size}
        height={size}
        className={`icon icon-img ${className}`}
        alt={title ?? ""}
        aria-hidden={title ? undefined : true}
      />
    );
  }
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`icon ${className}`}
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      <path d={PATHS[name] ?? PATHS.tag} />
    </svg>
  );
}
