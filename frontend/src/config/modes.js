// The three investigation modes and the input types each one accepts.
export const MODES = [
  {
    id: "scam_early_warning",
    label: "Scam & Early Warning",
    inputTypes: ["url_domain", "bitcoin_address"],
  },
  {
    id: "crypto_investigation",
    label: "Crypto Investigation",
    inputTypes: ["bitcoin_address", "bitcoin_txid", "ethereum_address", "ethereum_txid", "base_address", "base_txid", "solana_address", "solana_txid"],
  },
  {
    id: "x402",
    label: "x402 / Agentic Payments",
    inputTypes: ["x402_payment_id"],
  },
];

export const INPUT_TYPES = {
  url_domain: { label: "Website URL or domain", placeholder: "example-giveaway.test" },
  bitcoin_address: { label: "Bitcoin address", placeholder: "bc1q... or 1... or 3..." },
  bitcoin_txid: { label: "Bitcoin transaction ID", placeholder: "64 hexadecimal characters" },
  ethereum_address: { label: "Ethereum address", placeholder: "0x followed by 40 hexadecimal characters" },
  ethereum_txid: { label: "Ethereum transaction hash", placeholder: "0x followed by 64 hexadecimal characters" },
  base_address: { label: "Base address", placeholder: "0x followed by 40 hexadecimal characters" },
  base_txid: { label: "Base transaction hash", placeholder: "0x followed by 64 hexadecimal characters" },
  solana_address: { label: "Solana address", placeholder: "32 to 44 base58 characters" },
  solana_txid: { label: "Solana transaction signature", placeholder: "about 88 base58 characters" },
  x402_payment_id: { label: "x402 payment or request ID", placeholder: "x402-demo-0001" },
};

// Method catalogue. "ready" methods can run now; "planned" ones are shown
// disabled with the milestone that adds them, so the menu never offers
// something that cannot execute.
export const METHODS = [
  {
    id: "live_lookup",
    label: "Live lookup (mempool.space, Etherscan or Alchemy via backend)",
    modes: ["crypto_investigation"],
    inputTypes: ["bitcoin_address", "bitcoin_txid", "ethereum_address", "ethereum_txid", "base_address", "base_txid", "solana_address", "solana_txid"],
    status: "ready",
    runsOn: "server",
  },
  {
    id: "rules_v1",
    label: "Indicator rules (scam-website lists, OFAC, Ransomwhere)",
    modes: ["scam_early_warning", "crypto_investigation"],
    inputTypes: ["url_domain", "bitcoin_address"],
    status: "ready",
    runsOn: "server",
  },
  {
    id: "fixture_review",
    label: "Demo case review (synthetic fixture)",
    modes: ["scam_early_warning", "crypto_investigation", "x402"],
    inputTypes: ["url_domain", "bitcoin_address", "bitcoin_txid", "x402_payment_id"],
    status: "ready",
    runsOn: "browser",
  },
  {
    id: "graph_measures",
    label: "Graph measures (NetworkX: bridges and value flow)",
    modes: ["crypto_investigation"],
    inputTypes: ["bitcoin_address", "bitcoin_txid", "ethereum_address", "ethereum_txid", "base_address", "base_txid", "solana_address", "solana_txid"],
    status: "ready",
    runsOn: "server",
  },
  {
    id: "random_forest",
    label: "Random Forest (offline benchmark, dataset addresses only)",
    modes: ["crypto_investigation"],
    inputTypes: ["bitcoin_address"],
    status: "ready",
    runsOn: "server",
  },
  {
    id: "xgboost",
    label: "XGBoost (offline benchmark, dataset addresses only)",
    modes: ["crypto_investigation"],
    inputTypes: ["bitcoin_address"],
    status: "ready",
    runsOn: "server",
  },
  {
    id: "x402_reconcile",
    label: "x402 request/settlement reconciliation",
    modes: ["x402"],
    inputTypes: ["x402_payment_id"],
    status: "planned",
    reason: "from Milestone 13",
  },
];

// Methods that fit the chosen mode and input type, ready ones first.
export function compatibleMethods(modeId, inputType) {
  return METHODS.filter((m) => m.modes.includes(modeId) && m.inputTypes.includes(inputType)).sort(
    (a, b) => (a.status === "ready" ? 0 : 1) - (b.status === "ready" ? 0 : 1)
  );
}
