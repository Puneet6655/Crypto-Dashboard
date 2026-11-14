import { apiFetch } from "../api/client.js";
import { navigate } from "../hooks/useHashRoute.js";
import { chainOf } from "./chains.js";

// Opening case items in the graph explorer (Web3 mode).
// The explorer grows a graph from addresses, so a transaction is opened through the addresses that took part
// in it (Bitcoin inputs and outputs, the sender and receiver on Ethereum/Base, the wallets moving SOL or tokens).

export const GRAPH_CHAINS = ["bitcoin", "ethereum", "base", "solana"];
// Most addresses the backend expands in one request (see app/graph/expand.py).
export const GRAPH_START_LIMIT = { bitcoin: 60, ethereum: 40, base: 15, solana: 8 };

// { bitcoin: { addresses: [...], transactions: [...] }, ... } for the items the explorer can open.
export function graphGroups(items) {
  const groups = {};
  for (const item of items) {
    const chain = chainOf(item.network);
    if (!GRAPH_CHAINS.includes(chain)) continue;
    if (item.item_type !== "address" && item.item_type !== "transaction") continue;
    groups[chain] ??= { addresses: [], transactions: [] };
    groups[chain][item.item_type === "address" ? "addresses" : "transactions"].push(item.value);
  }
  return groups;
}

// The addresses that took part in a transaction.
export async function transactionAddresses(chain, hash) {
  if (chain === "bitcoin") {
    const tx = await apiFetch(`/api/bitcoin/tx/${encodeURIComponent(hash)}`);
    return [...tx.inputs, ...tx.outputs].map((e) => e.address).filter(Boolean);
  }
  if (chain === "ethereum" || chain === "base") {
    const tx = await apiFetch(`/api/${chain}/tx/${encodeURIComponent(hash)}`);
    return [tx.from_address, tx.to_address].filter(Boolean);
  }
  if (chain === "solana") {
    const tx = await apiFetch(`/api/solana/tx/${encodeURIComponent(hash)}`);
    return [...tx.signers, ...tx.transfers.flatMap((t) => [t.from_address, t.to_address])].filter(Boolean);
  }
  return [];
}

// Work out the start addresses for one chain and open the explorer. Returns { opened, trimmed, failed }.
export async function openInGraph(chain, { addresses = [], transactions = [] }) {
  const starts = new Set(addresses);
  let failed = 0;
  for (const hash of transactions) {
    try {
      for (const a of await transactionAddresses(chain, hash)) starts.add(a);
    } catch {
      failed += 1;
    }
  }
  const all = [...starts];
  const limit = GRAPH_START_LIMIT[chain] ?? 20;
  const list = all.slice(0, limit);
  if (list.length) navigate(`/graph/${chain}/${encodeURIComponent(list.join(","))}`);
  return { opened: list.length, trimmed: all.length - list.length, failed };
}
