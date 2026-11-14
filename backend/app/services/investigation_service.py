"""Runs one investigation: fetch data, build the graph, describe what was seen.

Methods: "live_lookup" for addresses and transactions, and "rules_v1" (list checks only,
app/services/rules_investigation.py) for websites and Bitcoin addresses. It reports observations, plus exact-match checks of every address in
the graph against the downloaded OFAC sanctions and Ransomwhere lists
(app/intelligence). Models (Milestones 10-11) add judgements later, as separate findings.

Data can come from a backup provider (see app/providers/failover.py), so evidence and
source timestamps name the provider that actually answered.
"""
import uuid
from datetime import datetime, timezone

from app.intelligence.index import IndicatorIndex, get_indicator_index
from app.intelligence.screening import Screening, screen_bitcoin
from app.providers.base import BitcoinProvider
from app.schemas.bitcoin import Provenance, TransactionSummary, format_btc
from app.schemas.investigation import (
    Evidence,
    Finding,
    Graph,
    GraphEdge,
    GraphLimits,
    GraphNode,
    InvestigationInput,
    InvestigationRequest,
    InvestigationResult,
    Scorecard,
)
from app.validation import is_bitcoin_address, is_txid, normalize_address

METHOD_VERSION = "live_lookup_v1"


class UnsupportedInvestigation(Exception):
    """The mode, input type or method cannot run live yet. The message says why."""


def _short(text: str, keep: int = 6) -> str:
    """Shorten a long ID for display: 'bc1qxy...x0wlh'. The full ID stays in the node id."""
    return text if len(text) <= keep * 2 + 3 else f"{text[:keep]}...{text[-keep:]}"


def _sats(value: int | None) -> str:
    """Amount for display: BTC first, sats in brackets."""
    return format_btc(value)


def new_investigation_id(now: datetime) -> str:
    """Readable, unique ID such as inv_20261026T102233Z_3f9a1c."""
    return f"inv_{now.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"


class GraphBuilder:
    """Collects nodes and edges once each and stops adding nodes at the limit."""

    def __init__(self, network: str, max_nodes: int, chain: str = "bitcoin"):
        self.chain = chain
        self.network = network
        self.max_nodes = max_nodes
        self.nodes: dict[str, GraphNode] = {}
        self.edges: dict[str, GraphEdge] = {}
        self.truncated = False

    def add_node(self, node: GraphNode) -> bool:
        """Add a node unless the limit is reached. Returns True if the node is in the graph."""
        if node.id in self.nodes:
            return True
        if len(self.nodes) >= self.max_nodes:
            self.truncated = True
            return False
        self.nodes[node.id] = node
        return True

    def add_edge(self, edge: GraphEdge) -> None:
        """Add an edge only if both ends are in the graph."""
        if edge.source in self.nodes and edge.target in self.nodes:
            self.edges.setdefault(edge.id, edge)

    def address_node(self, address: str) -> GraphNode:
        return GraphNode(id=f"{self.chain}:{self.network}:address:{address}", type="address", label=_short(address))

    def add_transaction(self, tx: TransactionSummary, evidence_id: str) -> None:
        """Add a transaction node, its input addresses (spent from) and output addresses (paid to).

        Inputs and outputs are linked to the transaction, never directly to each
        other, because Bitcoin does not record which input paid which output.
        """
        tx_node = GraphNode(
            id=tx.entity_id,
            type="transaction",
            label=f"tx {_short(tx.txid)}",
            details={"status": tx.status, "block_height": tx.block_height,
                     "total_transferred_sats": tx.total_output_sats, "fee_sats": tx.fee_sats},
        )
        if not self.add_node(tx_node):
            return
        for index, item in enumerate(tx.inputs):
            if item.address and self.add_node(self.address_node(item.address)):
                self.add_edge(GraphEdge(
                    id=f"{tx.txid}:in:{index}",
                    source=f"bitcoin:{self.network}:address:{item.address}",
                    target=tx.entity_id,
                    relation="observed",
                    label=f"input, {_sats(item.value_sats)}",
                    evidence_ids=[evidence_id],
                ))
        for index, item in enumerate(tx.outputs):
            if item.address and self.add_node(self.address_node(item.address)):
                self.add_edge(GraphEdge(
                    id=f"{tx.txid}:out:{index}",
                    source=tx.entity_id,
                    target=f"bitcoin:{self.network}:address:{item.address}",
                    relation="observed",
                    label=f"output, {_sats(item.value_sats)}",
                    evidence_ids=[evidence_id],
                ))

    def addresses(self) -> list[str]:
        """Every address shown in the graph, in the order it was added."""
        prefix = f"{self.chain}:{self.network}:address:"
        return [node.id[len(prefix):] for node in self.nodes.values() if node.type == "address"]

    def mark_listed(self, flagged: dict[str, list[str]]) -> None:
        """Record on each matched address node which indicator lists it is on."""
        for node_id, titles in flagged.items():
            node = self.nodes.get(node_id)
            if node is not None:
                self.nodes[node_id] = node.model_copy(update={"details": {**node.details, "listed_in": titles}})

    def graph(self) -> Graph:
        return Graph(
            nodes=list(self.nodes.values()),
            edges=list(self.edges.values()),
            truncated=self.truncated,
            limits=GraphLimits(max_nodes=self.max_nodes),
        )


COMMON_LIMITATIONS = [
    "No model has scored this case; rule checks are exact matches against downloaded OFAC and Ransomwhere lists",
    "An address missing from those lists is not proof that it is safe",
    "A link between addresses is not proof of shared ownership",
]
# The Bitcoin address lookup adds a live model score when the model is loaded (see _model_score).
MODEL_SCORED_LIMITATIONS = [
    "Rule checks are exact matches against downloaded OFAC and Ransomwhere lists",
    *COMMON_LIMITATIONS[1:],
]


def _data_sources(*provenances: Provenance) -> dict[str, datetime]:
    """Provider name -> retrieval time, for every provider that supplied data (earliest time each)."""
    out: dict[str, datetime] = {}
    for p in provenances:
        if p.provider not in out or p.retrieved_at < out[p.provider]:
            out[p.provider] = p.retrieved_at
    return out


async def run_investigation(request: InvestigationRequest, provider: BitcoinProvider, max_nodes: int,
                            indicators: IndicatorIndex | None = None, ethereum=None,
                            base=None, solana=None) -> InvestigationResult:
    """Run one live investigation and return the full result. Raises UnsupportedInvestigation or ProviderError.

    ethereum and base are the EVM failover chains (None when no key is set for them).
    """
    if request.mode == "x402":
        from app.x402.investigation import investigate_x402

        return await investigate_x402(request, max_nodes, indicators if indicators is not None else get_indicator_index())
    if request.method == "rules_v1":
        from app.services.rules_investigation import investigate_address_lists, investigate_site

        index = indicators if indicators is not None else get_indicator_index()
        if request.input_type == "url_domain":
            return investigate_site(request, max_nodes, index)
        if request.input_type == "bitcoin_address":
            return investigate_address_lists(request, max_nodes, index)
        raise UnsupportedInvestigation("Indicator rules check websites and Bitcoin addresses only.")
    if request.method == "graph_measures":
        from app.services.graph_measures import apply

        live = request.model_copy(update={"method": "live_lookup"})
        result = await run_investigation(live, provider, max_nodes, indicators, ethereum, base, solana)
        return apply(result)
    if request.method in ("random_forest", "xgboost"):
        from app.ml.scoring import investigate_with_model

        return await investigate_with_model(request, provider,
                                            indicators if indicators is not None else get_indicator_index())
    if request.method != "live_lookup":
        raise UnsupportedInvestigation(f"Method '{request.method}' cannot run on the server yet.")
    if request.mode != "crypto_investigation":
        raise UnsupportedInvestigation("Live lookups run only in Crypto Investigation mode for now.")
    indicators = indicators if indicators is not None else get_indicator_index()

    if request.input_type == "bitcoin_address":
        if not is_bitcoin_address(request.value):
            raise UnsupportedInvestigation("That does not look like a mainnet Bitcoin address.")
        return await _investigate_address(request, provider, max_nodes, indicators)
    if request.input_type == "bitcoin_txid":
        if not is_txid(request.value):
            raise UnsupportedInvestigation("A transaction ID must be 64 hexadecimal characters.")
        return await _investigate_transaction(request, provider, max_nodes, indicators)
    if request.input_type in ("ethereum_address", "ethereum_txid", "base_address", "base_txid"):
        from app.services.ethereum_investigation import investigate_evm

        chain = request.input_type.split("_")[0]
        return await investigate_evm(request, chain, ethereum if chain == "ethereum" else base, max_nodes, indicators)
    if request.input_type in ("solana_address", "solana_txid"):
        from app.services.solana_investigation import investigate_solana

        return await investigate_solana(request, solana, max_nodes, indicators)
    raise UnsupportedInvestigation(f"Input type '{request.input_type}' is not supported by live lookup.")


async def _investigate_address(request: InvestigationRequest, provider: BitcoinProvider, max_nodes: int,
                               indicators: IndicatorIndex) -> InvestigationResult:
    address = normalize_address(request.value)
    summary = await provider.get_address_summary(address)
    transactions = await provider.get_address_transactions(address)

    builder = GraphBuilder(summary.network, max_nodes)
    builder.add_node(builder.address_node(address).model_copy(update={"details": {
        "balance_sats": summary.balance_sats,
        "confirmed_balance_sats": summary.confirmed.balance_sats,
        "pending_change_sats": summary.unconfirmed.balance_sats,
        "total_received_sats": summary.confirmed.funded_sats + summary.unconfirmed.funded_sats,
        "total_sent_sats": summary.confirmed.spent_sats + summary.unconfirmed.spent_sats,
    }}))
    for tx in transactions:
        builder.add_transaction(tx, evidence_id="e2")

    total_tx = summary.confirmed.tx_count + summary.unconfirmed.tx_count
    shown_tx = sum(1 for node in builder.nodes.values() if node.type == "transaction")
    findings = [
        Finding(
            id="f1",
            title=f"{summary.confirmed.tx_count} confirmed and {summary.unconfirmed.tx_count} unconfirmed transactions",
            kind="observation",
            entity_ids=[summary.entity_id],
            evidence_ids=["e1"],
        ),
        Finding(
            id="f2",
            title=f"Current balance {_sats(summary.balance_sats)} (confirmed {_sats(summary.confirmed.balance_sats)}; pending change {_sats(summary.unconfirmed.balance_sats)})",
            kind="observation",
            entity_ids=[summary.entity_id],
            evidence_ids=["e1"],
        ),
    ]
    if shown_tx < total_tx:
        findings.append(Finding(
            id="f3",
            title=f"Graph shows {shown_tx} of {total_tx} transactions (newest first)",
            kind="observation",
            entity_ids=[summary.entity_id],
            evidence_ids=["e2"],
        ))

    page_provenance = transactions[0].provenance if transactions else summary.provenance
    evidence = [
        Evidence(
            id="e1", source=f"{summary.provenance.provider} address summary", source_url=summary.provenance.source_url,
            retrieved_at=summary.provenance.retrieved_at, method_version=METHOD_VERSION,
            excerpt=f"chain_stats tx_count={summary.confirmed.tx_count}, mempool_stats tx_count={summary.unconfirmed.tx_count}",
        ),
        Evidence(
            id="e2", source=f"{page_provenance.provider} address transactions (first page)",
            source_url=page_provenance.source_url,
            retrieved_at=page_provenance.retrieved_at,
            method_version=METHOD_VERSION,
            excerpt=f"{len(transactions)} transactions returned on the first page",
        ),
    ]
    screening = screen_bitcoin(indicators, summary.network, address, builder.addresses(),
                               first_finding=len(findings) + 1, first_evidence=len(evidence) + 1)
    builder.mark_listed(screening.flagged_nodes)
    sources = _data_sources(summary.provenance, page_provenance)
    model_findings, model_evidence, model_notes, model_result = await _model_score(
        provider, address, summary.entity_id, first_finding=len(findings) + len(screening.findings) + 1,
        first_evidence=len(evidence) + len(screening.evidence) + 1)
    now = datetime.now(timezone.utc)
    return InvestigationResult(
        investigation_id=new_investigation_id(now),
        created_at=now,
        input=InvestigationInput(type="bitcoin_address", value=address),
        mode=request.mode,
        method=request.method,
        data_mode="live",
        findings=findings + screening.findings + model_findings,
        indicators=screening.indicators,
        graph=builder.graph(),
        evidence=evidence + screening.evidence + model_evidence,
        source_timestamps={**sources, **screening.source_timestamps},
        limitations=(COMMON_LIMITATIONS if model_result is None else MODEL_SCORED_LIMITATIONS)
        + ["The graph shows only the first page of transactions; click the address for older pages"] + model_notes,
        explanation=model_result,
        uncertainty={
            "unconfirmed_tx_count": summary.unconfirmed.tx_count,
            "transactions_shown": shown_tx,
            "transactions_total": total_tx,
            "graph_truncated": builder.truncated,
            "data_sources": ", ".join(sources),
        },
        scorecard=Scorecard(configured=False),
    )


async def _model_score(provider, address: str, entity_id: str, first_finding: int, first_evidence: int):
    """The live XGBoost score as one extra finding for the default address lookup (details in Why Flagged?)."""
    from app.ml.live_scoring import LIVE_NOTE, lookup_score

    result, skipped = await lookup_score(provider, address)
    if result is None:
        return [], [], [skipped], None
    model = result["model"]
    verdict = "at or above" if result["flagged"] else "below"
    recent = result.get("recent_2026_threshold")
    recent_text = (f"; {'at or above' if result['score'] >= recent else 'below'} the 2026 alert threshold {recent:.3f}"
                   if recent else "")
    created = datetime.fromisoformat(result["registry_generated_utc"])
    finding = Finding(
        id=f"f{first_finding}", kind="model_output", entity_ids=[entity_id], evidence_ids=[f"e{first_evidence}"],
        title=(f"{model['name']}: live score {result['score']:.3f}, {verdict} its threshold {result['threshold']:.2f}"
               f"{recent_text} (see Why Flagged?)"))
    metrics = result.get("test_metrics") or {}
    evidence = Evidence(
        id=f"e{first_evidence}", source=f"Model registry: {model['id']} (live features, SHAP)",
        source_url="backend/models/registry.json", retrieved_at=created, method_version=model["version"],
        excerpt=(f"{result['live']['transactions_in_window']} transactions in the window ending at block "
                 f"{result['live']['anchor_block']}; offline test PR-AUC {metrics.get('PR-AUC', 0):.3f}"))
    return [finding], [evidence], [LIVE_NOTE], result


async def _investigate_transaction(request: InvestigationRequest, provider: BitcoinProvider, max_nodes: int,
                                   indicators: IndicatorIndex) -> InvestigationResult:
    txid = request.value.strip().lower()
    tx = await provider.get_transaction(txid)

    builder = GraphBuilder(tx.network, max_nodes)
    builder.add_transaction(tx, evidence_id="e1")

    named_inputs = sum(1 for item in tx.inputs if item.address)
    named_outputs = sum(1 for item in tx.outputs if item.address)
    findings = [
        Finding(
            id="f1",
            title=f"Transaction is {tx.status}" + (f" in block {tx.block_height:,}" if tx.block_height else ""),
            kind="observation",
            entity_ids=[tx.entity_id],
            evidence_ids=["e1"],
        ),
        Finding(
            id="f2",
            title=f"Total transferred {_sats(tx.total_output_sats)}; fee {_sats(tx.fee_sats)}",
            kind="observation",
            entity_ids=[tx.entity_id],
            evidence_ids=["e1"],
        ),
        Finding(
            id="f3",
            title=f"{len(tx.inputs)} inputs ({named_inputs} with an address) and {len(tx.outputs)} outputs ({named_outputs} with an address)",
            kind="observation",
            entity_ids=[tx.entity_id],
            evidence_ids=["e1"],
        ),
    ]
    evidence = [
        Evidence(
            id="e1", source=f"{tx.provenance.provider} transaction", source_url=tx.provenance.source_url,
            retrieved_at=tx.provenance.retrieved_at, method_version=METHOD_VERSION,
            excerpt=f"status={tx.status}, block_height={tx.block_height}, fee={tx.fee_sats}, outputs_total={tx.total_output_sats}",
        ),
    ]
    screening: Screening = screen_bitcoin(indicators, tx.network, None, builder.addresses(),
                                          first_finding=len(findings) + 1, first_evidence=len(evidence) + 1)
    builder.mark_listed(screening.flagged_nodes)
    sources = _data_sources(tx.provenance)
    now = datetime.now(timezone.utc)
    return InvestigationResult(
        investigation_id=new_investigation_id(now),
        created_at=now,
        input=InvestigationInput(type="bitcoin_txid", value=txid),
        mode=request.mode,
        method=request.method,
        data_mode="live",
        findings=findings + screening.findings,
        indicators=screening.indicators,
        graph=builder.graph(),
        evidence=evidence + screening.evidence,
        source_timestamps={**sources, **screening.source_timestamps},
        limitations=COMMON_LIMITATIONS + ["Bitcoin does not record which input paid which output"],
        uncertainty={"unconfirmed": tx.status == "unconfirmed", "graph_truncated": builder.truncated,
                     "data_sources": ", ".join(sources)},
        scorecard=Scorecard(configured=False),
    )
