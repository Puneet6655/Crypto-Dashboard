import { CHAIN_INFO, chainOf } from "./chains.js";

// Small pill with the chain's logo and name. chain: "bitcoin", "ethereum", "base", "solana", "web" or a network
// such as "ethereum-sepolia". compact shows the ticker (BTC) instead of the full name.
export default function ChainBadge({ chain, compact = false, title }) {
  const id = chainOf(chain);
  const info = CHAIN_INFO[id];
  return (
    <span className={`w3-chain w3-chain-${id}`} style={{ "--chain": info.color }} title={title ?? info.label}>
      <ChainLogo id={id} />
      <span>{compact ? info.short : info.label}</span>
    </span>
  );
}

export function ChainLogo({ id, size = 14 }) {
  const props = { width: size, height: size, viewBox: "0 0 24 24", "aria-hidden": true, className: "w3-chain-logo" };
  if (id === "bitcoin") {
    return <svg {...props}><circle cx="12" cy="12" r="11" fill="currentColor" opacity="0.18" /><path fill="currentColor" d="M14.6 11.6c.9-.4 1.5-1.2 1.4-2.4-.2-1.6-1.6-2.1-3.3-2.3V5h-1.3v1.8h-1V5H9.1v1.8H6.8v1.4h.9c.5 0 .7.3.7.6v6.4c0 .3-.2.6-.6.6h-.9l-.3 1.5h2.5V19h1.3v-1.7h1V19h1.3v-1.7c2.3-.1 3.8-.7 4-2.7.2-1.6-.6-2.4-2.1-2.9zM10 8.3h1.8c1 0 2.2.2 2.2 1.4 0 1.2-1.2 1.4-2.2 1.4H10V8.3zm2.1 7.5H10v-3.2h2.1c1.1 0 2.5.3 2.5 1.6 0 1.4-1.4 1.6-2.5 1.6z" /></svg>;
  }
  if (id === "ethereum") {
    return <svg {...props}><path fill="currentColor" d="M12 2 5.5 12.2 12 16l6.5-3.8z" /><path fill="currentColor" opacity="0.6" d="M12 17.2 5.5 13.4 12 22l6.5-8.6z" /></svg>;
  }
  if (id === "base") {
    return <svg {...props}><circle cx="12" cy="12" r="10" fill="currentColor" /><path d="M11.9 18.2a6.2 6.2 0 1 0-6.1-7h8.1v1.6H5.8a6.2 6.2 0 0 0 6.1 5.4z" fill="#0b0718" /></svg>;
  }
  if (id === "solana") {
    return <svg {...props}><path fill="currentColor" d="M7.2 5h12.3l-2.4 3H4.8zM4.8 10.5h12.3l2.4 3H7.2zM7.2 16h12.3l-2.4 3H4.8z" /></svg>;
  }
  if (id === "web") {
    return <svg {...props} fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18" /></svg>;
  }
  return <svg {...props}><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="3 3" /></svg>;
}
