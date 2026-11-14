import { useEffect, useRef, useState } from "react";

// Counts a number up from 0 to `target` once the element scrolls into view.
// Usage: const [ref, value] = useCountUp(10779, { duration: 1600, decimals: 0 });
// People who turn on "reduce motion" see the final number straight away.
export function useCountUp(target, { duration = 1600, decimals = 0 } = {}) {
  const ref = useRef(null);
  const [value, setValue] = useState(0);

  useEffect(() => {
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce || typeof IntersectionObserver === "undefined") {
      setValue(target);
      return undefined;
    }
    let frame = 0;
    const run = () => {
      const start = performance.now();
      const tick = (now) => {
        const t = Math.min(1, (now - start) / duration);
        const eased = 1 - Math.pow(1 - t, 3); // fast start, soft landing
        setValue(target * eased);
        if (t < 1) frame = requestAnimationFrame(tick);
      };
      frame = requestAnimationFrame(tick);
    };
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        observer.disconnect();
        run();
      }
    }, { threshold: 0.3 });
    if (ref.current) observer.observe(ref.current);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [target, duration]);

  const factor = 10 ** decimals;
  return [ref, Math.round(value * factor) / factor];
}
