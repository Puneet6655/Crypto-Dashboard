import { useEffect, useState } from "react";

// Counts a number up from 0 to `target` with an ease-out curve.
// Jumps straight to the end when the user asks for less motion.
export default function useCountUp(target, { duration = 1600, decimals = 0, delay = 0 } = {}) {
  const [value, setValue] = useState(0);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setValue(target);
      return undefined;
    }
    let frame = 0;
    let start = 0;
    const timer = setTimeout(() => {
      function tick(now) {
        if (!start) start = now;
        const progress = Math.min((now - start) / duration, 1);
        setValue(target * (1 - Math.pow(1 - progress, 3)));
        if (progress < 1) frame = requestAnimationFrame(tick);
      }
      frame = requestAnimationFrame(tick);
    }, delay);
    return () => { clearTimeout(timer); cancelAnimationFrame(frame); };
  }, [target, duration, delay]);

  return value.toLocaleString("en-GB", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}
