// Bitcoin amounts arrive from the API as whole satoshis (1 BTC = 100,000,000 sats).
// Show BTC first and sats after it, e.g. "0.0012 BTC (120,000 sats)".
const SATS_PER_BTC = 100_000_000n;

export function formatBtc(sats) {
  if (sats == null) return "unknown amount";
  const value = BigInt(Math.trunc(Number(sats)));
  const sign = value < 0n ? "-" : "";
  const abs = value < 0n ? -value : value;
  const whole = (abs / SATS_PER_BTC).toLocaleString();
  const frac = (abs % SATS_PER_BTC).toString().padStart(8, "0").replace(/0+$/, "");
  return `${sign}${whole}${frac ? `.${frac}` : ""} BTC (${Number(sats).toLocaleString()} ${abs === 1n ? "sat" : "sats"})`;
}

// Node detail keys that hold satoshi amounts end in "_sats".
export function isSatsKey(key) {
  return key.endsWith("_sats");
}
