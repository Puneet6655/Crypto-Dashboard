"""Live investigations of Solana addresses and transactions (Alchemy's Solana JSON-RPC).

Same result shape as the Bitcoin and Ethereum investigations: findings, a graph, evidence and
exact-match checks against the OFAC list of sanctioned Solana addresses.

An address investigation reads the newest 1,000 signatures (one call) and the newest
TX_DETAIL_LIMIT transactions in full (one call each), because Solana has no "transfers of this
address" call on the free plan. Findings about amounts and counterparties use those transactions;
counts and dates use all signatures.
"""
import asyncio
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

from app.intelligence.index import IndicatorIndex
from app.intelligence.screening import screen_addresses
from app.providers.solana_alchemy import LAMPORTS_PER_SOL, MAX_SIGNATURES, SYSTEM_PROGRAM
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
from app.schemas.solana import SolTransaction, SolTransfer
from app.services.investigation_service import (
    COMMON_LIMITATIONS,
    METHOD_VERSION,
    GraphBuilder,
    UnsupportedInvestigation,
    _short,
    new_investigation_id,
)
from app.validation import is_solana_address, is_solana_signature

TX_DETAIL_LIMIT = 40  # transactions read in full per address investigation (40 CU each)
NO_KEY = "Solana lookups are off: they need ALCHEMY_API_KEY and ALCHEMY_ENABLED=true in backend/.env."
CHAIN_LIMITATIONS = [
    "Checked on Solana mainnet only",
    "Token amounts are shown in token units with the token's mint address; token names are not looked up",
]
# Well-known stablecoin mints, so findings can name them.
KNOWN_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
    "So11111111111111111111111111111111111111112": "wSOL",
}


def sol(lamports: int | str | None) -> str:
    if lamports is None:
        return "unknown"
    text = f"{Decimal(int(lamports)) / LAMPORTS_PER_SOL:,.9f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _token_amount(t: SolTransfer) -> str:
    name = KNOWN_MINTS.get(t.mint or "", f"tokens of {_short(t.mint or '?')}")
    if t.decimals is None:
        return f"{int(t.amount_raw):,} units of {name}"
    value = Decimal(int(t.amount_raw)) / (Decimal(10) ** t.decimals)
    text = f"{value:,.6f}".rstrip("0").rstrip(".") or "0"
    return f"{text} {name}"


def _describe(t: SolTransfer) -> str:
    return f"{sol(t.amount_raw)} SOL" if t.kind == "sol" else _token_amount(t)


# ---- graph ----------------------------------------------------------------------------------

def _add_tx(builder: GraphBuilder, tx: SolTransaction, evidence_id: str, focus: str | None = None) -> None:
    """Transaction node, then sender -> tx -> receiver for each transfer (those touching `focus` first)."""
    node = GraphNode(id=tx.entity_id, type="transaction", label=f"tx {_short(tx.signature)}", details={
        "status": tx.status, "slot": tx.slot, "fee_sol": sol(tx.fee_lamports),
        "programs": ", ".join(p for p in tx.programs[:6] if p != SYSTEM_PROGRAM) or "system"})
    if not builder.add_node(node):
        return
    transfers = sorted(tx.transfers, key=lambda t: focus not in (t.from_address, t.to_address))
    if not transfers:  # e.g. a swap or program call with no plain transfer: link the signers
        for signer in tx.signers[:3]:
            if builder.add_node(builder.address_node(signer)):
                builder.add_edge(GraphEdge(id=f"{tx.signature}:signer:{signer}", source=builder.address_node(signer).id,
                                           target=tx.entity_id, relation="observed", label="signed",
                                           evidence_ids=[evidence_id]))
    for n, t in enumerate(transfers):
        what = _describe(t)
        if builder.add_node(builder.address_node(t.from_address)):
            builder.add_edge(GraphEdge(id=f"{tx.signature}:{n}:in", source=builder.address_node(t.from_address).id,
                                       target=tx.entity_id, relation="observed", label=f"sent {what}",
                                       evidence_ids=[evidence_id]))
        if builder.add_node(builder.address_node(t.to_address)):
            builder.add_edge(GraphEdge(id=f"{tx.signature}:{n}:out", source=tx.entity_id,
                                       target=builder.address_node(t.to_address).id, relation="observed",
                                       label=f"received {what}", evidence_ids=[evidence_id]))


# ---- entry point ----------------------------------------------------------------------------

async def investigate_solana(request: InvestigationRequest, provider, max_nodes: int,
                             indicators: IndicatorIndex) -> InvestigationResult:
    if provider is None:
        raise UnsupportedInvestigation(NO_KEY)
    value = request.value.strip()
    if request.input_type == "solana_address":
        if not is_solana_address(value):
            raise UnsupportedInvestigation("A Solana address is 32 to 44 base58 characters (no 0, O, I or l).")
        return await _investigate_address(request, value, provider, max_nodes, indicators)
    if not is_solana_signature(value):
        raise UnsupportedInvestigation("A Solana transaction signature is about 88 base58 characters.")
    return await _investigate_transaction(request, value, provider, max_nodes, indicators)


def _result(request, value, findings, screening, builder, evidence, sources, limitations, uncertainty):
    builder.mark_listed(screening.flagged_nodes)
    now = datetime.now(timezone.utc)
    return InvestigationResult(
        investigation_id=new_investigation_id(now), created_at=now,
        input=InvestigationInput(type=request.input_type, value=value), mode=request.mode, method=request.method,
        data_mode="live", findings=findings + screening.findings, indicators=screening.indicators,
        graph=builder.graph(), evidence=evidence + screening.evidence,
        source_timestamps={**sources, **screening.source_timestamps},
        limitations=COMMON_LIMITATIONS + CHAIN_LIMITATIONS + limitations,
        uncertainty={**uncertainty, "graph_truncated": builder.truncated, "data_sources": ", ".join(sources)},
        scorecard=Scorecard(configured=False),
    )


def _number(findings: list[Finding]) -> list[Finding]:
    for n, f in enumerate(findings, start=1):
        f.id = f"f{n}"
    return findings


# ---- address --------------------------------------------------------------------------------

async def _investigate_address(request, address: str, provider, max_nodes: int,
                               indicators: IndicatorIndex) -> InvestigationResult:
    summary, signatures = await asyncio.gather(provider.get_address_summary(address),
                                               provider.get_signatures(address))
    detailed = [tx for tx in await provider.get_transactions([s.signature for s in signatures[:TX_DETAIL_LIMIT]])
                if tx is not None]
    sid = summary.entity_id
    network = summary.network
    p = summary.provenance

    counterparties = Counter()
    for tx in detailed:
        for t in tx.transfers:
            if address in (t.from_address, t.to_address):
                other = t.to_address if t.from_address == address else t.from_address
                if other != address:
                    counterparties[other] += 1
        if not tx.transfers:
            counterparties.update(s for s in tx.signers if s != address)
    screening_input = [address] + [a for a, _ in counterparties.most_common()] + \
        [a for tx in detailed for t in tx.transfers for a in (t.from_address, t.to_address)]

    evidence = [
        Evidence(id="e1", source=f"{p.provider} Solana balance and account", source_url=p.source_url,
                 retrieved_at=p.retrieved_at, method_version=METHOD_VERSION,
                 excerpt=f"balance={summary.balance_lamports} lamports, owner program={summary.owner_program}, "
                         f"executable={summary.executable}"),
        Evidence(id="e2", source=f"{p.provider} getSignaturesForAddress",
                 source_url=f"{provider.base_url} getSignaturesForAddress {address} limit={MAX_SIGNATURES}",
                 retrieved_at=p.retrieved_at, method_version=METHOD_VERSION,
                 excerpt=f"{len(signatures):,} signatures returned (at most {MAX_SIGNATURES:,} per call)"),
        Evidence(id="e3", source=f"{p.provider} getTransaction (jsonParsed)",
                 source_url=f"{provider.base_url} getTransaction, newest {TX_DETAIL_LIMIT} signatures",
                 retrieved_at=p.retrieved_at, method_version=METHOD_VERSION,
                 excerpt=f"{len(detailed)} transactions read in full"),
    ]
    complete = len(signatures) < MAX_SIGNATURES
    findings = [Finding(
        id="", title=f"Balance {sol(summary.balance_lamports)} SOL"
                     + ("; this is a program (smart contract) account" if summary.executable else "")
                     + ("; the account does not exist on chain (never funded or closed)" if not summary.exists else ""),
        kind="observation", entity_ids=[sid], evidence_ids=["e1"])]
    failed = sum(1 for s in signatures if s.failed)
    count = f"{len(signatures):,} transactions" + (" (full history)" if complete else " (newest only)")
    findings.append(Finding(id="", title=f"Took part in {count}; {failed:,} failed",
                            kind="observation", entity_ids=[sid], evidence_ids=["e2"]))
    times = [s.block_time for s in signatures if s.block_time]
    if times:
        findings.append(Finding(id="", title=f"Activity runs from {min(times):%Y-%m-%d} to {max(times):%Y-%m-%d}",
                                kind="observation", entity_ids=[sid], evidence_ids=["e2"]))
    if detailed:
        sol_in = sum(int(t.amount_raw) for tx in detailed if tx.status == "success" for t in tx.transfers
                     if t.kind == "sol" and t.to_address == address and t.from_address != address)
        sol_out = sum(int(t.amount_raw) for tx in detailed if tx.status == "success" for t in tx.transfers
                      if t.kind == "sol" and t.from_address == address and t.to_address != address)
        fees = sum(tx.fee_lamports or 0 for tx in detailed if address in tx.signers[:1])
        findings.append(Finding(
            id="", title=f"In the newest {len(detailed)} transactions: received {sol(sol_in)} SOL, sent {sol(sol_out)} SOL "
                         f"in plain transfers; paid {sol(fees)} SOL in fees",
            kind="observation", entity_ids=[sid], evidence_ids=["e3"]))
        tokens = Counter(KNOWN_MINTS.get(t.mint or "", _short(t.mint or "?")) for tx in detailed
                         for t in tx.transfers if t.kind == "token" and address in (t.from_address, t.to_address))
        if tokens:
            findings.append(Finding(
                id="", title="Most moved tokens: " + ", ".join(f"{m} ({n:,})" for m, n in tokens.most_common(5)),
                kind="observation", entity_ids=[sid], evidence_ids=["e3"]))
    if counterparties:
        top = counterparties.most_common(3)
        findings.append(Finding(
            id="", title=f"{len(counterparties):,} distinct counterparties in the transactions read; most frequent: "
                         + ", ".join(f"{_short(a)} ({n:,})" for a, n in top),
            kind="observation", entity_ids=[f"solana:{network}:address:{a}" for a, _ in top], evidence_ids=["e3"]))
    _number(findings)

    screening = screen_addresses(indicators, "solana", network, address, list(dict.fromkeys(screening_input)),
                                 first_finding=len(findings) + 1, first_evidence=len(evidence) + 1,
                                 scope="linked addresses in the transactions read")
    flagged = {node_id.rsplit(":", 1)[1] for node_id in screening.flagged_nodes}
    builder = GraphBuilder(network, max_nodes, chain="solana")
    builder.add_node(builder.address_node(address))
    touches_flagged = lambda tx: any(a in flagged for t in tx.transfers for a in (t.from_address, t.to_address))  # noqa: E731
    for tx in sorted(detailed, key=lambda tx: (touches_flagged(tx), tx.slot or 0), reverse=True):
        _add_tx(builder, tx, "e3", focus=address)
    in_graph = set(builder.nodes)
    for f in findings:
        f.entity_ids = [i for i in f.entity_ids if i in in_graph] or [sid]

    limits = [f"Amounts and counterparties come from the newest {TX_DETAIL_LIMIT} transactions read in full; "
              "counts and dates use all fetched signatures",
              f"The graph shows up to {max_nodes} nodes: sanctioned counterparties first, then the newest activity"]
    if not complete:
        limits.append(f"The signature list stopped at {MAX_SIGNATURES:,}, so older activity is not included")
    return _result(request, address, findings, screening, builder, evidence, {p.provider: p.retrieved_at}, limits,
                   {"signatures_fetched": len(signatures), "transactions_read": len(detailed),
                    "counterparties": len(counterparties), "history_complete": complete})


# ---- single transaction -------------------------------------------------------------------

async def _investigate_transaction(request, signature: str, provider, max_nodes: int,
                                   indicators: IndicatorIndex) -> InvestigationResult:
    tx = await provider.get_transaction(signature)
    builder = GraphBuilder(tx.network, max_nodes, chain="solana")
    _add_tx(builder, tx, "e1")
    when = f" at slot {tx.slot:,}" if tx.slot else ""
    sol_moved = sum(int(t.amount_raw) for t in tx.transfers if t.kind == "sol")
    tokens = [t for t in tx.transfers if t.kind == "token"]
    findings = _number([
        Finding(id="", title=f"Transaction {'succeeded' if tx.status == 'success' else 'failed'}{when}",
                kind="observation", entity_ids=[tx.entity_id], evidence_ids=["e1"]),
        Finding(id="", title=f"Fee {sol(tx.fee_lamports)} SOL; {sol(sol_moved)} SOL in {sum(1 for t in tx.transfers if t.kind == 'sol')} "
                             f"plain transfers; {len(tokens)} token transfers; signed by "
                             + ", ".join(_short(s) for s in tx.signers[:3]),
                kind="observation", entity_ids=[tx.entity_id], evidence_ids=["e1"]),
    ])
    named = [p for p in tx.programs if p not in (SYSTEM_PROGRAM,)]
    if named:
        findings.append(Finding(id=f"f{len(findings) + 1}", title="Programs called: " + ", ".join(named[:8]),
                                kind="observation", entity_ids=[tx.entity_id], evidence_ids=["e1"]))
    p = tx.provenance
    evidence = [Evidence(id="e1", source=f"{p.provider} getTransaction (jsonParsed)", source_url=p.source_url,
                         retrieved_at=p.retrieved_at, method_version=METHOD_VERSION,
                         excerpt=f"status={tx.status}, slot={tx.slot}, fee={tx.fee_lamports} lamports, "
                                 f"{len(tx.transfers)} transfers")]
    addresses = list(dict.fromkeys(tx.signers + [a for t in tx.transfers for a in (t.from_address, t.to_address)]))
    screening = screen_addresses(indicators, "solana", tx.network, None, addresses,
                                 first_finding=len(findings) + 1, first_evidence=2,
                                 scope="addresses in this transaction")
    return _result(request, signature, findings, screening, builder, evidence, {p.provider: p.retrieved_at},
                   ["Swaps and other program calls appear as their plain SOL and token transfers only"],
                   {"transfers": len(tx.transfers)})
