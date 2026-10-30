// Turning things on screen (graph nodes, IDs) into case items: { item_type, value, network, label }.

const BITCOIN_ID = /^bitcoin:([a-z0-9]+):(address|tx):(.+)$/;

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

// A public explorer link for Bitcoin items (opens in a new tab).
export function explorerUrl(item) {
  if (!item.network?.startsWith("bitcoin")) return null;
  const base = item.network === "bitcoin" ? "https://mempool.space" : `https://mempool.space/${item.network.replace("bitcoin-", "")}`;
  if (item.item_type === "address") return `${base}/address/${item.value}`;
  if (item.item_type === "transaction") return `${base}/tx/${item.value}`;
  return null;
}
