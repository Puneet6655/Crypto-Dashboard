import { ChainLogo } from "./ChainBadge.jsx";

// Investigate loading state in Web3 mode: a radar sweep with the chains orbiting around it.
export default function W3Scanner() {
  return (
    <div className="w3-scan" aria-hidden="true">
      <div className="w3-scan-ring r1" />
      <div className="w3-scan-ring r2" />
      <div className="w3-scan-ring r3" />
      <div className="w3-scan-sweep" />
      <div className="w3-scan-orbit">
        {["bitcoin", "ethereum", "base", "solana"].map((id, i) => (
          <span key={id} className={`w3-scan-coin w3-chain-${id}`} style={{ "--i": i }}><ChainLogo id={id} size={16} /></span>
        ))}
      </div>
      <div className="w3-scan-core" />
    </div>
  );
}

// Idle state: the same orbit, slower, without the sweep.
export function W3Idle() {
  return (
    <div className="w3-scan w3-scan-idle" aria-hidden="true">
      <div className="w3-scan-ring r1" />
      <div className="w3-scan-ring r2" />
      <div className="w3-scan-ring r3" />
      <div className="w3-scan-orbit">
        {["bitcoin", "ethereum", "base", "solana"].map((id, i) => (
          <span key={id} className={`w3-scan-coin w3-chain-${id}`} style={{ "--i": i }}><ChainLogo id={id} size={16} /></span>
        ))}
      </div>
      <div className="w3-scan-core" />
    </div>
  );
}
