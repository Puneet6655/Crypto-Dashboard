import { useEffect, useRef } from "react";
import { startParticleLogo } from "./particleEngine.js";

// React wrapper around the canvas particle animation.
// Example: <ParticleLogo size={320} loop={false} />
// `replayKey` restarts the animation whenever its value changes.
export default function ParticleLogo({ size = 360, count = 500, colors, logoColor, logo, loop = true, replayKey = 0, className = "" }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const options = { size, count, loop };
    if (colors) options.colors = colors;
    if (logoColor) options.logoColor = logoColor;
    if (logo) options.logo = logo;
    return startParticleLogo(canvasRef.current, options);
    // colors/logo are compared by value so a new array each render doesn't restart it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size, count, loop, replayKey, logoColor, JSON.stringify(colors), JSON.stringify(logo)]);

  return <canvas ref={canvasRef} className={`pl-canvas ${className}`} role="img" aria-label="Animated logo" />;
}
