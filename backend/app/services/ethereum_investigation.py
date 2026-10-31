"""Live investigations of Ethereum and Base addresses and transactions (Etherscan and Alchemy).

Same result shape as the Bitcoin investigations: observations, a graph, evidence with the exact
(key-free) Etherscan URLs, and exact-match checks against the OFAC list of sanctioned
Ethereum-format addresses.

Each list call asks for as much as Etherscan returns in one go (1,000 records), for normal
transactions, ERC-20 transfers, internal transfers and NFT transfers. The calls run together,
spaced to stay within 3 per second. Findings and sanctions screening use everything fetched; the
graph shows sanctioned counterparties first, then the newest activity, up to the node limit.
"""
import asyncio
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

from app.intelligence.index import IndicatorIndex
from app.intelligence.screening import screen_addresses
from app.providers.base import ProviderError
from app.providers.evm_common import MAX_RECORDS_PER_CALL
from app.schemas.ethereum import EthInternalTransfer, EthNftTransfer, EthTokenTransfer, EthTransaction
from app.schemas.investigation import (
    Evidence,
    Finding,
    GraphEdge,
    GraphNode,
    InvestigationInput,
    InvestigationRequest,
    InvestigationResult,
    Scorecard,
)
from app.services.investigation_service import (
    COMMON_LIMITATIONS,
    METHOD_VERSION,
    GraphBuilder,
    UnsupportedInvestigation,
    _short,
    new_investigation_id,
)
from app.validation import is_ethereum_address, is_ethereum_tx_hash, normalize_hex

MAX = MAX_RECORDS_PER_CALL
STATUS_WORDS = {"success": "succeeded", "failed": "failed", "pending": "is pending",
                "unknown": "status unknown (no receipt from this source)"}
NO_KEY = {
    "ethereum": "Ethereum lookups are off: add ETHERSCAN_API_KEY to backend/.env and restart the API.",
    "base": "Base lookups are off: they need ALCHEMY_API_KEY and ALCHEMY_ENABLED=true in backend/.env.",
}
CHAIN_LIMITATIONS = {
    "ethereum": ["Checked on Ethereum mainnet only; the same address can hold funds on Base and other EVM chains"],
    "base": ["Checked on Base mainnet only; the same address can hold funds on Ethereum and other EVM chains",
             "OFAC lists Ethereum-format addresses without naming a chain: a match on Base is the same address "
             "string, so confirm the designation covers Base before relying on it"],
}
SCOPE = "linked addresses in the fetched history"


# ---- formatting -----------------------------------------------------------------------------

def _amount(raw: str | int, decimals: int | None) -> str:
    """Integer base units as a readable decimal, e.g. 1500000 with 6 decimals -> '1.5'."""
    if decimals is None:
        return f"{int(raw):,} units"
    value = Decimal(int(raw)) / (Decimal(10) ** decimals)
    text = f"{value:,.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def eth(wei: str | int | None) -> str:
    return "unknown" if wei is None else _amount(wei, 18)


def _token(t: EthTokenTransfer) -> str:
    symbol = t.token_symbol or f"tokens of {_short(t.token_contract)}"
    return f"{_amount(t.value_raw, t.token_decimals)} {symbol}"


def _nft(n: EthNftTransfer) -> str:
    name = n.token_symbol or n.token_name or _short(n.token_contract)
    count = "" if n.quantity in ("", "1") else f"{n.quantity} x "
    return f"{count}{name} #{_short(n.token_id, 4)}"


def _count(n: int, noun: str, complete: bool) -> str:
    return f"{n:,} {noun} (full history)" if complete else f"{n:,} {noun} (newest only)"





# ---- graph ----------------------------------------------------------------------------------

def _tx_node(builder: GraphBuilder, network: str, tx_hash: str, details: dict | None = None) -> str | None:
    node_id = f"{builder.chain}:{network}:tx:{tx_hash}"
    node = GraphNode(id=node_id, type="transaction", label=f"tx {_short(tx_hash)}", details=details or {})
    return node_id if builder.add_node(node) else None


def _link(builder: GraphBuilder, edge_id: str, tx_id: str, sender: str | None, receiver: str | None,
          sent: str, received: str, evidence_id: str) -> None:
    """sender -> transaction -> receiver, each address node added if there is room."""
    if sender and builder.add_node(builder.address_node(sender)):
        builder.add_edge(GraphEdge(id=f"{edge_id}:in", source=builder.address_node(sender).id, target=tx_id,
                                   relation="observed", label=sent, evidence_ids=[evidence_id]))
    if receiver and builder.add_node(builder.address_node(receiver)):
        builder.add_edge(GraphEdge(id=f"{edge_id}:out", source=tx_id, target=builder.address_node(receiver).id,
                                   relation="observed", label=received, evidence_ids=[evidence_id]))


def _add_tx(builder: GraphBuilder, tx: EthTransaction, evidence_id: str) -> None:
    tx_id = _tx_node(builder, tx.network, tx.tx_hash, {
        "status": tx.status, "block_number": tx.block_number, "value_eth": eth(tx.value_wei),
        "fee_eth": eth(tx.fee_wei) if tx.fee_wei else None, "method": tx.method})
    if tx_id:
        value = f"{eth(tx.value_wei)} ETH"
        _link(builder, tx.tx_hash, tx_id, tx.from_address, tx.to_address or tx.contract_created,
              f"sent {value}" + (f", called {tx.method}" if tx.method else ""),
              f"received {value}" if tx.to_address else "contract created", evidence_id)


def _add_token(builder: GraphBuilder, t: EthTokenTransfer, n: int, network: str, evidence_id: str) -> None:
    tx_id = _tx_node(builder, network, t.tx_hash, {"block_number": t.block_number})
    if tx_id:
        _link(builder, f"{t.tx_hash}:token:{n}", tx_id, t.from_address, t.to_address,
              f"sent {_token(t)}", f"received {_token(t)}", evidence_id)


def _add_internal(builder: GraphBuilder, t: EthInternalTransfer, n: int, network: str, evidence_id: str) -> None:
    tx_id = _tx_node(builder, network, t.tx_hash, {"block_number": t.block_number})
    if tx_id:
        value = f"{eth(t.value_wei)} ETH" + (" (failed)" if t.failed else "")
        _link(builder, f"{t.tx_hash}:internal:{n}", tx_id, t.from_address, t.to_address or t.contract_created,
              f"internal transfer out, {value}", f"internal transfer in, {value}", evidence_id)


def _add_nft(builder: GraphBuilder, t: EthNftTransfer, n: int, network: str, evidence_id: str) -> None:
    tx_id = _tx_node(builder, network, t.tx_hash, {"block_number": t.block_number})
    if tx_id:
        _link(builder, f"{t.tx_hash}:nft:{n}", tx_id, t.from_address, t.to_address,
              f"sent NFT {_nft(t)}", f"received NFT {_nft(t)}", evidence_id)


# ---- entry point ----------------------------------------------------------------------------

async def investigate_evm(request: InvestigationRequest, chain: str, provider, max_nodes: int,
                          indicators: IndicatorIndex) -> InvestigationResult:
    """chain is "ethereum" or "base"; provider is that chain's failover (or a single adapter in tests)."""
    if provider is None:
        raise UnsupportedInvestigation(NO_KEY[chain])
    if request.input_type.endswith("_address"):
        if not is_ethereum_address(request.value):
            raise UnsupportedInvestigation("An address is 0x followed by 40 hexadecimal characters.")
        return await _investigate_address(request, chain, provider, max_nodes, indicators)
    if not is_ethereum_tx_hash(request.value):
        raise UnsupportedInvestigation("A transaction hash is 0x followed by 64 hexadecimal characters.")
    return await _investigate_transaction(request, chain, provider, max_nodes, indicators)


def _result(request: InvestigationRequest, value: str, findings, screening, builder, evidence, sources,
            limitations, uncertainty) -> InvestigationResult:
    builder.mark_listed(screening.flagged_nodes)
    now = datetime.now(timezone.utc)
    return InvestigationResult(
        investigation_id=new_investigation_id(now), created_at=now,
        input=InvestigationInput(type=request.input_type, value=value), mode=request.mode, method=request.method,
        data_mode="live", findings=findings + screening.findings, indicators=screening.indicators,
        graph=builder.graph(), evidence=evidence + screening.evidence,
        source_timestamps={**sources, **screening.source_timestamps},
        limitations=COMMON_LIMITATIONS + limitations,
        uncertainty={**uncertainty, "graph_truncated": builder.truncated, "data_sources": ", ".join(sources)},
        scorecard=Scorecard(configured=False),
    )


# ---- address --------------------------------------------------------------------------------

async def _investigate_address(request: InvestigationRequest, chain: str, provider, max_nodes: int,
                               indicators: IndicatorIndex) -> InvestigationResult:
    address = normalize_hex(request.value)
    summary, txs, tokens, internal, nfts = await asyncio.gather(
        provider.get_address_summary(address),
        provider.get_address_transactions(address),
        provider.get_token_transfers(address),
        provider.get_internal_transfers(address),
        provider.get_nft_transfers(address),
    )
    network = summary.network

    # Every party the address dealt with, most frequent first (all of them are screened).
    pairs = ([(t.from_address, t.to_address or t.contract_created) for t in txs]
             + [(t.from_address, t.to_address) for t in tokens]
             + [(t.from_address, t.to_address or t.contract_created) for t in internal]
             + [(t.from_address, t.to_address) for t in nfts])
    counterparties = Counter(a for pair in pairs for a in pair if a and a != address)
    screening_input = [address] + [a for a, _ in counterparties.most_common()]

    evidence, sources = _address_evidence(provider, address, summary, txs, tokens, internal, nfts)
    # Screening first, so sanctioned counterparties get a place in the graph before the node limit.
    builder = GraphBuilder(network, max_nodes, chain=chain)
    builder.add_node(builder.address_node(address))
    full = {method: provider.history_complete(method, address, len(items)) for method, items in (
        ("get_address_transactions", txs), ("get_token_transfers", tokens),
        ("get_internal_transfers", internal), ("get_nft_transfers", nfts))}
    findings = _address_findings(chain, summary, txs, tokens, internal, nfts, counterparties, full)
    screening = screen_addresses(indicators, chain, network, address, screening_input,
                                 first_finding=len(findings) + 1, first_evidence=len(evidence) + 1, scope=SCOPE)
    flagged = {node_id.rsplit(":", 1)[1] for node_id in screening.flagged_nodes}

    events = ([(t.block_number or 0, "tx", t, 0) for t in txs]
              + [(t.block_number or 0, "token", t, n) for n, t in enumerate(tokens)]
              + [(t.block_number or 0, "internal", t, n) for n, t in enumerate(internal)]
              + [(t.block_number or 0, "nft", t, n) for n, t in enumerate(nfts)])
    involves_flagged = lambda e: bool(flagged & {e[2].from_address, getattr(e[2], "to_address", None)})  # noqa: E731
    events.sort(key=lambda e: (involves_flagged(e), e[0]), reverse=True)
    adders = {"tx": lambda t, n: _add_tx(builder, t, "e2"),
              "token": lambda t, n: _add_token(builder, t, n, network, "e3"),
              "internal": lambda t, n: _add_internal(builder, t, n, network, "e4"),
              "nft": lambda t, n: _add_nft(builder, t, n, network, "e5")}
    for _, kind, item, n in events:
        adders[kind](item, n)

    # Point finding links at nodes that made it into the graph.
    in_graph = set(builder.nodes)
    for f in findings:
        f.entity_ids = [i for i in f.entity_ids if i in in_graph] or [summary.entity_id]

    complete = all(full.values())
    limits = ([] if complete else
              [f"Some lists stopped at the {MAX:,} records one call returns, so older activity is not included"])
    if any(t.status == "unknown" for t in txs):
        limits.append("Transactions listed by alchemy.com carry no fee, success flag or called function "
                      "(open one transaction for its receipt)")
    return _result(
        request, address, findings, screening, builder, evidence, sources,
        CHAIN_LIMITATIONS[chain] + limits + [f"The graph shows up to {max_nodes} nodes: sanctioned counterparties first, "
                                    "then the newest activity; findings use everything fetched"],
        {"transactions_fetched": len(txs), "token_transfers_fetched": len(tokens),
         "internal_transfers_fetched": len(internal), "nft_transfers_fetched": len(nfts),
         "counterparties": len(counterparties), "history_complete": complete,
         "sent_tx_count": summary.sent_tx_count})


def _address_evidence(provider, address, summary, txs, tokens, internal, nfts) -> tuple[list[Evidence], dict]:
    """Evidence items naming the source that answered each list, and provider name -> retrieval time."""
    p = summary.provenance
    sources = {p.provider: p.retrieved_at}

    def item(id_: str, what: str, method: str, n: int) -> Evidence:
        name, url = provider.describe(method, address)
        sources.setdefault(name, p.retrieved_at)
        return Evidence(id=id_, source=f"{name} {what}", source_url=url, retrieved_at=p.retrieved_at,
                        method_version=METHOD_VERSION, excerpt=f"{n:,} records returned (at most {MAX:,} per call)")

    evidence = [
        Evidence(id="e1", source=f"{p.provider} balance and nonce", source_url=p.source_url, retrieved_at=p.retrieved_at,
                 method_version=METHOD_VERSION,
                 excerpt=f"balance={summary.balance_wei} wei, sent transactions={summary.sent_tx_count}"),
        item("e2", "normal transactions", "get_address_transactions", len(txs)),
        item("e3", "ERC-20 transfers", "get_token_transfers", len(tokens)),
        item("e4", "internal transfers", "get_internal_transfers", len(internal)),
        item("e5", "NFT transfers (ERC-721 and ERC-1155)", "get_nft_transfers", len(nfts)),
    ]
    return evidence, sources


def _address_findings(chain, summary, txs, tokens, internal, nfts, counterparties: Counter,
                      full: dict[str, bool]) -> list[Finding]:
    me = summary.address
    sid = summary.entity_id
    findings = [
        Finding(id="f1", title=f"Balance {eth(summary.balance_wei)} ETH; {summary.sent_tx_count:,} transactions sent",
                kind="observation", entity_ids=[sid], evidence_ids=["e1"]),
        Finding(id="f2", title=f"Fetched {_count(len(txs), 'transactions', full['get_address_transactions'])}, "
                               f"{_count(len(tokens), 'token transfers', full['get_token_transfers'])}, "
                               f"{_count(len(internal), 'internal transfers', full['get_internal_transfers'])} and "
                               f"{_count(len(nfts), 'NFT transfers', full['get_nft_transfers'])}",
                kind="observation", entity_ids=[sid], evidence_ids=["e2", "e3", "e4", "e5"]),
    ]
    times = [t.block_time for group in (txs, tokens, internal, nfts) for t in group if t.block_time]
    if times:
        findings.append(Finding(
            id="", title=f"Fetched activity runs from {min(times):%Y-%m-%d} to {max(times):%Y-%m-%d}",
            kind="observation", entity_ids=[sid], evidence_ids=["e2", "e3", "e4", "e5"]))

    ok = [t for t in txs if t.status == "success"]
    received = [t for t in ok if t.to_address == me and int(t.value_wei)]
    sent = [t for t in ok if t.from_address == me and int(t.value_wei)]
    inner_in = sum(int(t.value_wei) for t in internal if t.to_address == me and not t.failed)
    inner_out = sum(int(t.value_wei) for t in internal if t.from_address == me and not t.failed)
    own = [t for t in txs if t.from_address == me]
    fees = (f"fees paid {eth(sum(int(t.fee_wei) for t in own))}" if all(t.fee_wei is not None for t in own)
            else "fees not reported by this source")
    if any(t.status == "unknown" for t in txs):  # Alchemy lists: no success flag, so count every transfer
        received = [t for t in txs if t.to_address == me and int(t.value_wei)]
        sent = [t for t in txs if t.from_address == me and int(t.value_wei)]
    findings.append(Finding(
        id="", title=f"ETH in fetched transactions: received {eth(sum(int(t.value_wei) for t in received))} "
                     f"({len(received):,} transactions), sent {eth(sum(int(t.value_wei) for t in sent))} "
                     f"({len(sent):,}); internal in {eth(inner_in)}, out {eth(inner_out)}; {fees}",
        kind="observation", entity_ids=[sid], evidence_ids=["e2", "e4"]))

    if counterparties:
        top = counterparties.most_common(3)
        names = ", ".join(f"{_short(a)} ({n:,})" for a, n in top)
        findings.append(Finding(
            id="", title=f"{len(counterparties):,} distinct counterparties; most frequent: {names}",
            kind="observation", entity_ids=[f"{chain}:{summary.network}:address:{a}" for a, _ in top],
            evidence_ids=["e2", "e3", "e4", "e5"]))
    if tokens:
        by_token = Counter(t.token_symbol or _short(t.token_contract) for t in tokens).most_common(5)
        findings.append(Finding(
            id="", title="Most transferred tokens: " + ", ".join(f"{s} ({n:,})" for s, n in by_token),
            kind="observation", entity_ids=[sid], evidence_ids=["e3"]))
    failed = [t for t in txs if t.status == "failed"]
    if failed:
        findings.append(Finding(id="", title=f"{len(failed):,} of the fetched transactions failed",
                                kind="observation", entity_ids=[t.entity_id for t in failed[:20]],
                                evidence_ids=["e2"]))
    for number, f in enumerate(findings, start=1):
        f.id = f"f{number}"
    return findings


# ---- single transaction -------------------------------------------------------------------

async def _optional(coro):
    """A lookup that may be unavailable (e.g. internal transfers when only Alchemy answers): None instead of failing."""
    try:
        return await coro
    except ProviderError:
        return None


async def _investigate_transaction(request: InvestigationRequest, chain: str, provider, max_nodes: int,
                                   indicators: IndicatorIndex) -> InvestigationResult:
    tx_hash = normalize_hex(request.value)
    tx, internal = await asyncio.gather(provider.get_transaction(tx_hash),
                                        _optional(provider.get_tx_internal_transfers(tx_hash)))
    internal_known = internal is not None
    internal = internal or []
    builder = GraphBuilder(tx.network, max_nodes, chain=chain)
    _add_tx(builder, tx, "e1")
    for n, transfer in enumerate(tx.token_transfers):
        _add_token(builder, transfer, n, tx.network, "e1")
    for n, transfer in enumerate(internal):
        _add_internal(builder, transfer, n, tx.network, "e2")

    where = f" in block {tx.block_number:,}" if tx.block_number else ""
    findings = [
        Finding(id="f1", title=f"Transaction {STATUS_WORDS[tx.status]}{where}",
                kind="observation", entity_ids=[tx.entity_id], evidence_ids=["e1"]),
        Finding(id="f2", title=f"Value {eth(tx.value_wei)} ETH; fee {eth(tx.fee_wei)} ETH; "
                               f"{len(tx.token_transfers)} ERC-20 transfers; "
                               + (f"{len(internal)} internal transfers" if internal_known else "internal transfers unavailable")
                               + (f"; calls {tx.method}" if tx.method else ""),
                kind="observation", entity_ids=[tx.entity_id], evidence_ids=["e1", "e2"]),
    ]
    evidence = [
        Evidence(id="e1", source=f"{tx.provenance.provider} transaction and receipt", source_url=tx.provenance.source_url,
                 retrieved_at=tx.provenance.retrieved_at, method_version=METHOD_VERSION,
                 excerpt=f"status={tx.status}, block={tx.block_number}, value={tx.value_wei} wei, fee={tx.fee_wei} wei"),
        Evidence(id="e2", source="internal transfers of this transaction",
                 source_url=f"txlistinternal txhash={tx_hash}" if internal_known else "not available",
                 retrieved_at=tx.provenance.retrieved_at, method_version=METHOD_VERSION,
                 excerpt=f"{len(internal)} internal transfers" if internal_known
                 else "No source that lists internal transfers per transaction could answer"),
    ]
    screening = screen_addresses(indicators, chain, tx.network, None, builder.addresses(),
                                 first_finding=len(findings) + 1, first_evidence=len(evidence) + 1,
                                 scope="addresses in this transaction")
    return _result(request, tx.tx_hash, findings, screening, builder, evidence,
                   {tx.provenance.provider: tx.provenance.retrieved_at},
                   CHAIN_LIMITATIONS[chain] + ["Token amounts from the receipt are raw units; the token's symbol is not looked up"],
                   {"pending": tx.status == "pending"})
