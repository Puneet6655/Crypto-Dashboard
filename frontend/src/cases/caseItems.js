// Turning things on screen (graph nodes, IDs) into case items: { item_type, value, network, label }.

const BITCOIN_ID = /^bitcoin:([a-z0-9]+):(address|tx):(.+)$/;
const EVM_ID = /^(ethereum|base):([a-z0-9-]+):(address|tx):(0x[0-9a-f]+)$/;
const EVM_EXPLORERS = { ethereum: "https://etherscan.io", base: "https://basescan.org" };
const SOLANA_ID = /^solana:([a-z0-9-]+):(address|tx):([1-9A-HJ-NP-Za-km-z]+)$/;

export const ITEM_TYPES = {
  address: { label: "Wallet address", short: "Address", icon: "wallet" },
  transaction: { label: "Transaction", short: "Transaction", icon: "transaction" },
  domain: { label: "Website", short: "Website", icon: "globe" },
  payment: { label: "Payment", short: "Payment", icon: "payment" },
  other: { label: "Other", short: "Other", icon: "tag" },
};

// A graph node from an investigation. Bitcoin IDs look like bitcoin:mainnet:address:bc1q...
export function itemFromNode(node) {
  if (!node) return null;
  const match = BITCOIN_ID.exec(node.id);
  if (match) {
    const [, network, kind, value] = match;
    return {
      item_type: kind === "tx" ? "transaction" : "address",
      value,
      network: network === "mainnet" ? "bitcoin" : `bitcoin-${network}`,
      label: node.label,
    };
  }
  const evm = EVM_ID.exec(node.id);
  if (evm) {
    const [, chain, network, kind, value] = evm;
    return {
      item_type: kind === "tx" ? "transaction" : "address",
      value,
      network: network === "mainnet" ? chain : `${chain}-${network}`,
      label: node.label,
    };
  }
  const sol = SOLANA_ID.exec(node.id);
  if (sol) {
    const [, network, kind, value] = sol;
    return {
      item_type: kind === "tx" ? "transaction" : "address",
      value,
      network: network === "mainnet" ? "solana" : `solana-${network}`,
      label: node.label,
    };
  }
  const type = ITEM_TYPES[node.type] ? node.type : "other";
  const value = node.id.replace(/^web:domain:/, "");
  return { item_type: type, value, network: type === "domain" ? "web" : "other", label: node.label };
}

// Attributes that make any element right-clickable to add it to a case (see AddToCaseMenu).
export function caseItemAttrs(item) {
  if (!item) return {};
  return {
    "data-case-type": item.item_type,
    "data-case-value": item.value,
    "data-case-label": item.label ?? "",
    "data-case-network": item.network ?? "bitcoin",
  };
}

export function itemFromElement(element) {
  const el = element?.closest?.("[data-case-type][data-case-value]");
  if (!el) return null;
  return {
    item_type: el.dataset.caseType,
    value: el.dataset.caseValue,
    label: el.dataset.caseLabel || null,
    network: el.dataset.caseNetwork || "bitcoin",
  };
}

export const sameItem = (a, b) => a.item_type === b.item_type && a.value === b.value;

export function shortValue(value, keep = 8) {
  return value.length > keep * 2 + 3 ? `${value.slice(0, keep)}…${value.slice(-keep)}` : value;
}

// The live investigation input type for a case item, or null when it cannot be looked up live.
export function liveInputType(item) {
  const kind = item.item_type === "address" ? "address" : item.item_type === "transaction" ? "txid" : null;
  if (!kind) return null;
  if (item.network === "bitcoin") return `bitcoin_${kind}`;
  if (item.network === "ethereum" || item.network === "base" || item.network === "solana") return `${item.network}_${kind}`;
  return null;
}

// The explorer site for an item's network, e.g. "etherscan.io".
export function explorerName(item) {
  if (item.network === "solana") return "solscan.io";
  const base = EVM_EXPLORERS[item.network];
  return base ? base.replace("https://", "") : "mempool.space";
}

// A public explorer link for Bitcoin, Ethereum and Base items (opens in a new tab).
export function explorerUrl(item) {
  if (item.network === "solana") {
    if (item.item_type === "address") return `https://solscan.io/account/${item.value}`;
    if (item.item_type === "transaction") return `https://solscan.io/tx/${item.value}`;
    return null;
  }
  const evm = EVM_EXPLORERS[item.network];
  if (evm) {
    if (item.item_type === "address") return `${evm}/address/${item.value}`;
    if (item.item_type === "transaction") return `${evm}/tx/${item.value}`;
    return null;
  }
  if (!item.network?.startsWith("bitcoin")) return null;
  const base = item.network === "bitcoin" ? "https://mempool.space" : `https://mempool.space/${item.network.replace("bitcoin-", "")}`;
  if (item.item_type === "address") return `${base}/address/${item.value}`;
  if (item.item_type === "transaction") return `${base}/tx/${item.value}`;
  return null;
}
