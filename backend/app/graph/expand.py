"""Advanced graph mode: grow an address graph step by step, streamed to the browser in batches.

How one request works (POST /api/graph/expand):

1. The browser names a chain, the addresses to expand, how many hops (1 or 2) and how many new
   nodes it still has room for (its node cap minus what it already shows).
2. Each address is fetched with ONE list call per kind of history (Bitcoin: the newest page of
   transactions; Ethereum/Base: normal transactions and token transfers; Solana: the signature list
   plus the newest few transactions), then turned into counterparties: who it paid and who paid it,
   with totals per counterparty.
3. Counterparties are grouped so the graph stays readable at thousands of nodes:
   - dust: tiny or zero-value transfers (address poisoning, spam airdrops) become one summary node;
   - busy addresses (many counterparties, or a full page of history inside one day: exchanges,
     payment processors, mixers, popular contracts) are marked "hub" and are not expanded further
     automatically, and only their top counterparties are shown;
   - beyond the top N counterparties of a node, the rest become one "+N more" summary node that the
     browser can open without another API call (the members travel inside the summary).
   Addresses on a sanctions or scam list are always shown on their own, never hidden in a summary.
4. Each fetched address is sent as soon as it is ready (NDJSON lines), so the browser can draw while
   the rest load and never waits for the whole request.

Rate limits and cost:
- Results are cached for 10 minutes per address (on top of each provider's own cache), and two
  requests for the same address share one fetch.
- Bitcoin (mempool.space, blockstream.info as backup) is free but rate limited: graph mode makes at
  most 2 calls at a time, 0.4 s apart. The failover provider cools a source down after HTTP 429.
- Ethereum uses Etherscan first (free key, 3 calls a second, paced by the provider).
- Alchemy (Ethereum backup, Base, Solana) has 30M compute units (CU) a month on the free plan.
  Graph mode has its own daily CU allowance (GRAPH_ALCHEMY_DAILY_CU, default 300,000, about 9M a
  month at most), checked before every Alchemy-backed fetch, and fetches fewer addresses per request.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator, Callable

from app.intelligence.index import IndicatorIndex
from app.intelligence.screening import CHECKS
from app.providers.base import ProviderError
from app.providers.failover import _MISSING, TTLCache
from app.validation import is_bitcoin_address, is_ethereum_address, is_solana_address, normalize_address

log = logging.getLogger("app.graph")

CHAINS = ("bitcoin", "ethereum", "base", "solana")
UNIT = {"bitcoin": "BTC", "ethereum": "ETH", "base": "ETH", "solana": "SOL"}
# Below this much (in the chain's own coin) with no token value, a counterparty counts as dust.
DUST = {"bitcoin": Decimal("0.00001"), "ethereum": Decimal("0.0001"), "base": Decimal("0.0001"),
        "solana": Decimal("0.0001")}
# Addresses fetched per request at most. Alchemy-backed chains get fewer because each costs CU.
FETCH_CAP = {"bitcoin": 60, "ethereum": 40, "base": 15, "solana": 8}
CONCURRENCY = {"bitcoin": 2, "ethereum": 2, "base": 2, "solana": 2}
MIN_GAP_SECONDS = {"bitcoin": 0.4, "ethereum": 0.0, "base": 0.0, "solana": 0.0}  # EVM/Solana pace themselves
# Hub rule: this many counterparties, or this many transactions within one day.
HUB_COUNTERPARTIES = 150
HUB_MIN_TXS = {"bitcoin": 25, "ethereum": 300, "base": 300, "solana": 300}
HUB_WINDOW_SECONDS = 86_400
HUB_TOP = 10  # counterparties shown for a hub; the rest go into "+N more"
MAX_MEMBERS = 2000  # members carried inside one summary node
SOLANA_TXS_READ = 15  # newest Solana transactions read in full per address (40 CU each)
ALCHEMY_CU = {"evm_list_pair": 240, "solana_call": 40}
CACHE_TTL = 600.0
LAMPORTS = Decimal(10**9)
WEI = Decimal(10**18)
SATS = Decimal(10**8)


def short(text: str, keep: int = 6) -> str:
    return text if len(text) <= keep * 2 + 3 else f"{text[:keep]}...{text[-keep:]}"


def node_id(chain: str, address: str) -> str:
    return f"{chain}:mainnet:address:{address}"


def normalize(chain: str, address: str) -> str | None:
    """The address spelled the way the graph uses it, or None if it is not valid for the chain."""
    address = (address or "").strip()
    if chain == "bitcoin":
        return normalize_address(address) if is_bitcoin_address(address) else None
    if chain in ("ethereum", "base"):
        return address.lower() if is_ethereum_address(address) else None
    if chain == "solana":
        return address if is_solana_address(address) else None
    return None


# ---- what one fetch produces ---------------------------------------------------------------------

@dataclass
class Counterparty:
    address: str
    sent: Decimal = Decimal(0)  # the expanded address paid this much to the counterparty
    received: Decimal = Decimal(0)  # the counterparty paid this much to the expanded address
    sent_txs: int = 0
    received_txs: int = 0
    tokens: Counter = field(default_factory=Counter)  # token symbol -> transfers with a non-zero amount
    zero_value_txs: int = 0
    last_time: datetime | None = None

    @property
    def total(self) -> Decimal:
        return self.sent + self.received

    @property
    def tx_count(self) -> int:
        return self.sent_txs + self.received_txs

    def seen(self, when: datetime | None) -> None:
        if when and (self.last_time is None or when > self.last_time):
            self.last_time = when


@dataclass
class Neighbors:
    chain: str
    address: str
    counterparties: dict[str, Counterparty]
    tx_seen: int
    source: str
    times: list[datetime] = field(default_factory=list)
    note: str | None = None
    cu_spent: int = 0

    def hub_reason(self) -> str | None:
        if len(self.counterparties) >= HUB_COUNTERPARTIES:
            return f"{len(self.counterparties)} counterparties in its newest {self.tx_seen} transactions"
        if self.tx_seen >= HUB_MIN_TXS[self.chain] and len(self.times) >= 2:
            span = (max(self.times) - min(self.times)).total_seconds()
            if span <= HUB_WINDOW_SECONDS:
                return f"{self.tx_seen} transactions within {max(span / 3600, 0.1):.1f} hours"
        return None


def _cp(table: dict[str, Counterparty], address: str) -> Counterparty:
    if address not in table:
        table[address] = Counterparty(address)
    return table[address]


# ---- per-chain fetchers --------------------------------------------------------------------------

class Budget:
    """Graph mode's own daily allowance of Alchemy compute units (resets at midnight UTC)."""

    def __init__(self, daily_cu: int, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.daily_cu = daily_cu
        self.clock = clock
        self.day = clock().date()
        self.used = 0

    def _roll(self) -> None:
        today = self.clock().date()
        if today != self.day:
            self.day, self.used = today, 0

    @property
    def remaining(self) -> int:
        self._roll()
        return max(0, self.daily_cu - self.used)

    def spend(self, cu: int) -> None:
        self._roll()
        self.used += cu

    def status(self) -> dict:
        return {"alchemy_cu_used_today": self.used, "alchemy_cu_daily_limit": self.daily_cu,
                "alchemy_cu_remaining": self.remaining}


async def fetch_bitcoin(provider, address: str) -> Neighbors:
    txs = await provider.get_address_transactions(address)
    table: dict[str, Counterparty] = {}
    times = []
    for tx in txs:
        when = tx.block_time
        if when:
            times.append(when)
        ins = [i for i in tx.inputs if i.address]
        outs = [o for o in tx.outputs if o.address]
        in_total = sum(Decimal(i.value_sats or 0) for i in ins)
        if any(i.address == address for i in ins):
            # We paid: every output to someone else is a payment (outputs back to us are change).
            for o in outs:
                if o.address != address:
                    cp = _cp(table, o.address)
                    cp.sent += Decimal(o.value_sats or 0) / SATS
                    cp.sent_txs += 1
                    cp.seen(when)
        else:
            got = sum(Decimal(o.value_sats or 0) for o in outs if o.address == address)
            if not got:
                continue
            # We were paid: split what we got over the inputs by their size.
            for i in ins:
                share = got * Decimal(i.value_sats or 0) / in_total if in_total else got / len(ins)
                cp = _cp(table, i.address)
                cp.received += share / SATS
                cp.received_txs += 1
                cp.seen(when)
    source = getattr(provider, "answered_by", {}).get(("txs", address, None), getattr(provider, "name", "bitcoin"))
    note = None
    if len(txs) >= 25:
        note = f"Built from the newest {len(txs)} transactions (one page); older history is not included."
    return Neighbors("bitcoin", address, table, len(txs), source, times, note)


def _evm_will_use_alchemy(provider) -> bool:
    """True when the next uncached list call would go to Alchemy (no ready Etherscan source first)."""
    for source in provider.sources:
        if provider._cooldown_left(source) > 0:
            continue
        return source.name == "alchemy.com"
    return False


async def fetch_evm(provider, address: str, chain: str) -> Neighbors:
    keys = [("get_address_transactions", address), ("get_token_transfers", address)]
    uncached = [k for k in keys if provider.cache.get(k) is _MISSING]
    txs, tokens = await asyncio.gather(provider.get_address_transactions(address),
                                       provider.get_token_transfers(address))
    cu = sum(ALCHEMY_CU["evm_list_pair"] for k in uncached if provider.answered_by.get(k) == "alchemy.com")
    table: dict[str, Counterparty] = {}
    times = []
    for tx in txs:
        if tx.block_time:
            times.append(tx.block_time)
        value = Decimal(int(tx.value_wei or 0)) / WEI
        if tx.from_address == address and tx.to_address and tx.to_address != address:
            cp = _cp(table, tx.to_address)
            cp.sent += value
            cp.sent_txs += 1
        elif tx.to_address == address and tx.from_address != address:
            cp = _cp(table, tx.from_address)
            cp.received += value
            cp.received_txs += 1
        else:
            continue
        if value == 0:
            cp.zero_value_txs += 1
        cp.seen(tx.block_time)
    for t in tokens:
        if t.from_address == address and t.to_address and t.to_address != address:
            cp = _cp(table, t.to_address)
            cp.sent_txs += 1
        elif t.to_address == address and t.from_address != address:
            cp = _cp(table, t.from_address)
            cp.received_txs += 1
        else:
            continue
        if int(t.value_raw or 0) > 0:
            cp.tokens[t.token_symbol or short(t.token_contract, 4)] += 1
        else:
            cp.zero_value_txs += 1
        cp.seen(t.block_time)
    sources = {provider.answered_by.get(k) for k in keys} - {None}
    note = None
    if max(len(txs), len(tokens)) >= 1000:
        note = "Built from the newest 1,000 records of each kind; older history is not included."
    return Neighbors(chain, address, table, len(txs) + len(tokens), ", ".join(sorted(sources)) or provider.label,
                     times, note, cu)


async def fetch_solana(provider, address: str) -> Neighbors:
    cu = 0 if provider.cache.get(("signatures", address, 1000)) is not _MISSING else ALCHEMY_CU["solana_call"]
    signatures = await provider.get_signatures(address)
    newest = [s.signature for s in signatures if not s.failed][:SOLANA_TXS_READ]
    cu += sum(ALCHEMY_CU["solana_call"] for sig in newest if provider.cache.get(("tx", sig)) is _MISSING)
    txs = [tx for tx in await provider.get_transactions(newest) if tx is not None]
    table: dict[str, Counterparty] = {}
    for tx in txs:
        for t in tx.transfers:
            if t.kind == "sol":
                amount = Decimal(int(t.amount_raw or 0)) / LAMPORTS
            else:
                amount = Decimal(0)
            if t.from_address == address and t.to_address != address:
                cp = _cp(table, t.to_address)
                cp.sent += amount
                cp.sent_txs += 1
            elif t.to_address == address and t.from_address != address:
                cp = _cp(table, t.from_address)
                cp.received += amount
                cp.received_txs += 1
            else:
                continue
            if t.kind == "token" and int(t.amount_raw or 0) > 0:
                cp.tokens[short(t.mint or "token", 4)] += 1
            cp.seen(tx.block_time)
    times = [s.block_time for s in signatures if s.block_time]
    note = (f"Counterparties come from the newest {len(txs)} of {len(signatures)} transactions "
            "(each one read costs Alchemy compute units).")
    return Neighbors("solana", address, table, len(signatures), provider.name, times, note, cu)


# ---- shared state: cache, in-flight fetches, pacing, budget -------------------------------------

class GraphExpander:
    """One per process, so the cache, pacing and Alchemy allowance are shared by every request."""

    def __init__(self, daily_cu: int):
        self.cache = TTLCache(max_entries=20_000)
        self.inflight: dict[tuple, asyncio.Future] = {}
        self.budget = Budget(daily_cu)
        self.semaphores = {chain: asyncio.Semaphore(CONCURRENCY[chain]) for chain in CHAINS}
        self.next_slot = {chain: 0.0 for chain in CHAINS}
        self.fetches = Counter()
        self.cache_hits = Counter()

    def alchemy_estimate(self, chain: str, provider) -> int:
        if chain == "solana":
            return ALCHEMY_CU["solana_call"] * (1 + SOLANA_TXS_READ)
        if chain in ("ethereum", "base") and _evm_will_use_alchemy(provider):
            return 2 * ALCHEMY_CU["evm_list_pair"]
        return 0

    async def _pace(self, chain: str) -> None:
        gap = MIN_GAP_SECONDS[chain]
        if not gap:
            return
        now = time.monotonic()
        start = max(now, self.next_slot[chain])
        self.next_slot[chain] = start + gap
        if start > now:
            await asyncio.sleep(start - now)

    async def neighbors(self, chain: str, address: str, provider) -> tuple[Neighbors, bool]:
        """(neighbors, came_from_cache). Raises ProviderError; raises BudgetExhausted for Alchemy."""
        key = (chain, address)
        cached = self.cache.get(key)
        if cached is not _MISSING:
            self.cache_hits[chain] += 1
            return cached, True
        if key in self.inflight:
            return await asyncio.shield(self.inflight[key]), True
        future = asyncio.get_running_loop().create_future()
        self.inflight[key] = future
        try:
            estimate = self.alchemy_estimate(chain, provider)
            if estimate and self.budget.remaining < estimate:
                raise BudgetExhausted(self.budget.status())
            async with self.semaphores[chain]:
                await self._pace(chain)
                if chain == "bitcoin":
                    result = await fetch_bitcoin(provider, address)
                elif chain == "solana":
                    result = await fetch_solana(provider, address)
                else:
                    result = await fetch_evm(provider, address, chain)
            self.budget.spend(result.cu_spent)
            self.fetches[chain] += 1
            self.cache.put(key, result, CACHE_TTL)
            future.set_result(result)
            return result, False
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
                future.exception()  # mark it retrieved, so asyncio does not log it when nobody else waits
            raise
        finally:
            self.inflight.pop(key, None)

    def status(self) -> dict:
        return {**self.budget.status(), "cached_addresses": len(self.cache),
                "fetches": dict(self.fetches), "cache_hits": dict(self.cache_hits)}


class BudgetExhausted(Exception):
    def __init__(self, status: dict):
        super().__init__("Graph mode has used today's Alchemy allowance")
        self.status = status


_expander: GraphExpander | None = None


def get_expander(daily_cu: int) -> GraphExpander:
    global _expander
    if _expander is None or _expander.budget.daily_cu != daily_cu:
        _expander = GraphExpander(daily_cu)
    return _expander


# ---- turning neighbors into graph nodes and edges -------------------------------------------------

def _fmt(value: Decimal) -> float:
    return float(round(value, 8))


def _listed(index: IndicatorIndex | None, chain: str, address: str) -> list[str]:
    if index is None:
        return []
    source_ids = [source_id for source_id, _ in CHECKS[chain]]
    return list(dict.fromkeys(hit.title for hit in index.lookup(address, source_ids)))


def _member(cp: Counterparty) -> dict:
    return {"address": cp.address, "sent": _fmt(cp.sent), "received": _fmt(cp.received), "tx_count": cp.tx_count,
            "tokens": dict(cp.tokens.most_common(3))}


def _edges_for(chain: str, center: str, target: str, cp_like: dict) -> list[dict]:
    """Up to two edges between the expanded node and a counterparty (or summary): one per direction."""
    unit = UNIT[chain]
    edges = []
    tokens = ", ".join(f"{sym} x{n}" for sym, n in (cp_like.get("tokens") or {}).items())
    if cp_like["sent_txs"]:
        edges.append({"id": f"{center}>{target}", "source": center, "target": target, "value": cp_like["sent"],
                      "tx_count": cp_like["sent_txs"], "unit": unit, "tokens": tokens})
    if cp_like["received_txs"]:
        edges.append({"id": f"{target}>{center}", "source": target, "target": center, "value": cp_like["received"],
                      "tx_count": cp_like["received_txs"], "unit": unit, "tokens": tokens})
    return edges


def build_batch(n: Neighbors, per_node_limit: int, index: IndicatorIndex | None) -> tuple[dict, list[dict], list[dict], list[str]]:
    """(the expanded node, counterparty and summary nodes, edges, addresses worth expanding next)."""
    chain = n.chain
    center = node_id(chain, n.address)
    hub = n.hub_reason()
    center_node = {"id": center, "kind": "hub" if hub else "address", "chain": chain, "address": n.address,
                   "label": short(n.address), "expanded": True, "tx_seen": n.tx_seen, "counterparties": len(n.counterparties),
                   "source": n.source, "note": n.note, "hub_reason": hub, "listed": _listed(index, chain, n.address)}

    listed, dust, normal = [], [], []
    for cp in n.counterparties.values():
        flags = _listed(index, chain, cp.address)
        if flags:
            listed.append((cp, flags))
        elif cp.total < DUST[chain] and not cp.tokens:
            dust.append(cp)
        else:
            normal.append(cp)
    # Biggest money first, then the busiest relationships.
    normal.sort(key=lambda cp: (cp.total, cp.tx_count), reverse=True)
    limit = min(per_node_limit, HUB_TOP) if hub else per_node_limit
    shown, rest = normal[:limit], normal[limit:]

    nodes, edges = [], []
    for cp, flags in [*listed, *((cp, []) for cp in shown)]:
        cid = node_id(chain, cp.address)
        nodes.append({"id": cid, "kind": "address", "chain": chain, "address": cp.address, "label": short(cp.address),
                      "expanded": False, "listed": flags})
        edges += _edges_for(chain, center, cid, {"sent": _fmt(cp.sent), "received": _fmt(cp.received),
                                                 "sent_txs": cp.sent_txs, "received_txs": cp.received_txs,
                                                 "tokens": dict(cp.tokens.most_common(3))})

    for kind, group, title in (("dust", dust, "dust or zero-value"), ("more", rest, "more")):
        if not group:
            continue
        sid = f"summary:{kind}:{center}"
        members = [_member(cp) for cp in group[:MAX_MEMBERS]]
        nodes.append({"id": sid, "kind": kind, "chain": chain, "label": f"{len(group):,} {title}", "parent": center,
                      "count": len(group), "members": members, "members_cut": max(0, len(group) - MAX_MEMBERS)})
        totals = {"sent": _fmt(sum((cp.sent for cp in group), Decimal(0))),
                  "received": _fmt(sum((cp.received for cp in group), Decimal(0))),
                  "sent_txs": sum(cp.sent_txs for cp in group), "received_txs": sum(cp.received_txs for cp in group)}
        edges += _edges_for(chain, center, sid, totals)

    next_up = [cp.address for cp, _ in listed] + [cp.address for cp in shown]
    return center_node, nodes, edges, next_up


# ---- the streamed expansion -----------------------------------------------------------------------

async def expand_stream(expander: GraphExpander, provider, chain: str, addresses: list[str], hops: int,
                        max_new_nodes: int, per_node_limit: int, index: IndicatorIndex | None,
                        is_disconnected: Callable[[], "asyncio.Future[bool]"] | None = None) -> AsyncIterator[dict]:
    """Yield events: start, batch (one per fetched address), warning, error, done."""
    seeds = list(dict.fromkeys(addresses))
    fetch_cap = FETCH_CAP[chain]
    sent_ids: set[str] = set()
    fetched: set[str] = set()
    new_nodes = 0
    fetches = cache_hits = cu = 0
    stopped = None
    started = time.monotonic()

    yield {"event": "start", "chain": chain, "seeds": seeds, "hops": hops, "fetch_cap": fetch_cap,
           "max_new_nodes": max_new_nodes, "budget": expander.budget.status()}

    frontier = seeds
    for hop in range(1, hops + 1):
        todo = [a for a in frontier if a not in fetched][: max(0, fetch_cap - len(fetched))]
        if not todo:
            break
        fetched.update(todo)
        yield {"event": "progress", "hop": hop, "fetching": len(todo)}
        tasks = {asyncio.ensure_future(expander.neighbors(chain, a, provider)): a for a in todo}
        next_frontier: list[str] = []
        done_count = 0
        try:
            for done in asyncio.as_completed(tasks):
                if is_disconnected is not None and await is_disconnected():
                    stopped = "cancelled"
                    break
                try:
                    neighbors, from_cache = await done
                except BudgetExhausted as exc:
                    stopped = "budget"
                    yield {"event": "warning", "code": "budget",
                           "message": "Graph mode has used today's Alchemy allowance; it resets at midnight UTC.",
                           "budget": exc.status}
                    break
                except ProviderError as exc:
                    yield {"event": "error", "message": exc.message, "status": exc.status_code,
                           "retry_after": exc.retry_after}
                    if exc.status_code == 429:
                        stopped = "rate_limited"
                        break
                    continue
                cache_hits += from_cache
                fetches += not from_cache
                cu += 0 if from_cache else neighbors.cu_spent

                center, nodes, edges, next_up = build_batch(neighbors, per_node_limit, index)
                if center["id"] not in sent_ids:  # a seed; counterparties were counted when they arrived
                    sent_ids.add(center["id"])
                    new_nodes += 1
                kept = []
                for node in nodes:
                    if node["id"] in sent_ids:
                        continue
                    if new_nodes >= max_new_nodes:
                        stopped = "node_limit"
                        break
                    sent_ids.add(node["id"])
                    kept.append(node)
                    new_nodes += 1
                edges = [e for e in edges if e["source"] in sent_ids and e["target"] in sent_ids]
                done_count += 1
                yield {"event": "batch", "hop": hop, "expanded": center, "nodes": kept, "edges": edges,
                       "from_cache": from_cache, "progress": {"done": done_count, "total": len(todo)}}
                # Walk on from ordinary addresses only: a hub's counterparties are mostly its customers.
                if center["kind"] != "hub":
                    next_frontier += [a for a in next_up if node_id(chain, a) in sent_ids]
                if stopped == "node_limit":
                    break
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
        if stopped:
            break
        # Next hop: counterparties just shown (hubs are expanded only when the analyst asks).
        frontier = list(dict.fromkeys(next_frontier))
        if hop < hops and len(fetched) >= fetch_cap:
            stopped = "fetch_cap"
            yield {"event": "warning", "code": "fetch_cap",
                   "message": f"Fetched {fetch_cap} addresses, the most for one request on {chain}. "
                              "Click nodes to expand further."}
            break

    yield {"event": "done", "stopped": stopped, "new_nodes": new_nodes, "fetched": fetches, "from_cache": cache_hits,
           "alchemy_cu": cu, "seconds": round(time.monotonic() - started, 2), "budget": expander.budget.status()}
