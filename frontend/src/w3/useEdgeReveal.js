import { useEffect, useRef, useState } from "react";

const EDGE_PX = 28;        // the menu opens when the mouse comes this close to the left edge
const LEAVE_PX = 340;      // and closes once the mouse is this far from it
const CLOSE_DELAY = 280;   // small grace period so a quick wobble does not close it
const PIN_KEY = "w3-sidebar-pinned";

function readPinned() {
  try { return localStorage.getItem(PIN_KEY) === "1"; } catch { return false; }
}

// Web3 mode: the side menu stays hidden until the mouse moves to the left edge.
// Only on wide screens; phones and tablets keep the menu button.
export function useEdgeReveal(enabled) {
  const [shown, setShown] = useState(false);
  const [pinned, setPinnedState] = useState(readPinned);
  const timer = useRef(0);
  const shownRef = useRef(false);
  shownRef.current = shown;

  useEffect(() => {
    if (!enabled) return undefined;
    const wide = window.matchMedia("(min-width: 901px)");
    function move(e) {
      if (!wide.matches) return;
      if (e.clientX <= EDGE_PX) {
        clearTimeout(timer.current);
        timer.current = 0;
        if (!shownRef.current) setShown(true);
      } else if (shownRef.current && e.clientX > LEAVE_PX && !timer.current) {
        timer.current = setTimeout(() => { timer.current = 0; setShown(false); }, CLOSE_DELAY);
      } else if (e.clientX <= LEAVE_PX && timer.current) {
        clearTimeout(timer.current);
        timer.current = 0;
      }
    }
    // Leaving the window to the left (e.g. a second screen) should not leave it stuck open.
    function leave() { if (shownRef.current) setShown(false); }
    window.addEventListener("pointermove", move, { passive: true });
    document.documentElement.addEventListener("mouseleave", leave);
    return () => {
      window.removeEventListener("pointermove", move);
      document.documentElement.removeEventListener("mouseleave", leave);
      clearTimeout(timer.current);
    };
  }, [enabled]);

  function setPinned(value) {
    setPinnedState(value);
    try { localStorage.setItem(PIN_KEY, value ? "1" : "0"); } catch { /* private window: just not remembered */ }
  }

  return {
    shown: shown || pinned,
    pinned,
    togglePin: () => setPinned(!pinned),
    show: () => setShown(true),     // keyboard focus inside the menu
    hide: () => setShown(false),
  };
}
