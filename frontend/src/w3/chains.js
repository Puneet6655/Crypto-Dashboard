// Chain names, colours and logos for Web3 mode, plus a guess of what a typed value is.
// Shared by the Investigate hint, the Cases badges and the Live alerts list.

export const CHAIN_INFO = {
  bitcoin: { label: "Bitcoin", short: "BTC", color: "#f7931a" },
  ethereum: { label: "Ethereum", short: "ETH", color: "#8c9eff" },
  base: { label: "Base", short: "BASE", color: "#3b82f6" },
  solana: { label: "Solana", short: "SOL", color: "#14f195" },
  web: { label: "Website", short: "WEB", color: "#fb923c" },
  other: { label: "Unknown chain", short: "?", color: "#94a3b8" },
};

// "ethereum-sepolia" -> ethereum; anything we do not know -> other.
export function chainOf(network) {
  const id = String(network ?? "").toLowerCase().split("-")[0];
  return CHAIN_INFO[id] ? id : "other";
}

// What a typed value looks like. Returns the input types it could be, best first.
// Same formats as services/runFixtureInvestigation.js (and the backend's validation.py).
const BTC_ADDRESS = /^([13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[qp][02-9ac-hj-np-z]{38,58})$/;
const HEX64 = /^[0-9a-fA-F]{64}$/;
const EVM_ADDRESS = /^0x[0-9a-fA-F]{40}$/;
const EVM_TX = /^0x[0-9a-fA-F]{64}$/;
const SOL_ADDRESS = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const SOL_TX = /^[1-9A-HJ-NP-Za-km-z]{64,90}$/;
const DOMAIN = /^(https?:\/\/)?([\p{L}\p{N}-]+\.)+[\p{L}\p{N}-]{2,}(:\d+)?([/?#].*)?$/iu;

export function detectInput(raw) {
  const value = raw.trim();
  if (!value) return [];
  const lower = value.toLowerCase().startsWith("bc1") ? value.toLowerCase() : value;
  if (BTC_ADDRESS.test(lower)) return [{ inputType: "bitcoin_address", chain: "bitcoin", what: "address" }];
  if (HEX64.test(value)) return [{ inputType: "bitcoin_txid", chain: "bitcoin", what: "transaction" }];
  if (EVM_ADDRESS.test(value)) {
    return [
      { inputType: "ethereum_address", chain: "ethereum", what: "address" },
      { inputType: "base_address", chain: "base", what: "address" },
    ];
  }
  if (EVM_TX.test(value)) {
    return [
      { inputType: "ethereum_txid", chain: "ethereum", what: "transaction" },
      { inputType: "base_txid", chain: "base", what: "transaction" },
    ];
  }
  if (SOL_TX.test(value)) return [{ inputType: "solana_txid", chain: "solana", what: "transaction" }];
  if (SOL_ADDRESS.test(value)) return [{ inputType: "solana_address", chain: "solana", what: "address" }];
  if (DOMAIN.test(value)) return [{ inputType: "url_domain", chain: "web", what: "website" }];
  return [];
}
