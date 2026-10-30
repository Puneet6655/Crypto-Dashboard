import { useEffect, useState } from "react";

// A tiny router: the page lives in the address bar after "#", e.g. #/cases/12.
// The back button, reloads and shared links all work, with no extra package.
function read() {
  const path = window.location.hash.replace(/^#/, "") || "/investigate";
  const [page = "investigate", ...rest] = path.split("/").filter(Boolean);
  return { page, params: rest, path };
}

export function navigate(path) {
  window.location.hash = path.startsWith("#") ? path : `#${path}`;
}

export function useHashRoute() {
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const update = () => setRoute(read());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return route;
}
