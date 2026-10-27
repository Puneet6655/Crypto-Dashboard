"""Runs one investigation: fetch data, build the graph, describe what was seen.

Milestone 3 supports one method, "live_lookup", for Bitcoin addresses and
transaction IDs. It reports observations only. Rules (Milestone 7) and models
(Milestones 10-11) add judgements later, as separate findings.
"""
import uuid
from datetime import datetime, timezone

from app.providers.base import BitcoinProvider
from app.schemas.bitcoin import TransactionSummary
from app.schemas.investigation import (
    Evidence,
    Finding,
    Graph,
    GraphEdge,
    GraphLimits,
    GraphNode,
    Indicator,
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
    return "unknown amount" if value is None else f"{value:,} sats"


def new_investigation_id(now: datetime) -> str:
    """Readable, unique ID such as inv_20261026T102233Z_3f9a1c."""
    return f"inv_{now.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"


class GraphBuilder:
    """Collects nodes and edges once each and stops adding nodes at the limit."""

    def __init__(self, network: str, max_nodes: int):
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
        return GraphNode(id=f"bitcoin:{self.network}:address:{address}", type="address", label=_short(address))

    def add_transaction(self, tx: TransactionSummary, evidence_id: str) -> None:
        """Add a transaction node, its input addresses (spent from) and output addresses (paid to).

        Inputs and outputs are linked to the transaction, never directly to each
        other, because Bitcoin does not record which input paid which output.
        """
        tx_node = GraphNode(
            id=tx.entity_id,
            type="transaction",
            label=f"tx {_short(tx.txid)}",
            details={"status": tx.status, "block_height": tx.block_height, "fee_sats": tx.fee_sats},
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

    def graph(self) -> Graph:
        return Graph(
            nodes=list(self.nodes.values()),
            edges=list(self.edges.values()),
            truncated=self.truncated,
            limits=GraphLimits(max_nodes=self.max_nodes),
        )


RULES_NOT_RUN = [
    Indicator(id="i1", rule="ofac_exact_match", matched=None, reason="Not run: the OFAC snapshot is added in Milestone 7"),
]

COMMON_LIMITATIONS = [
    "Observations only: no rule or model has judged this case yet",
    "A link between addresses is not proof of shared ownership",
]


async def run_investigation(request: InvestigationRequest, provider: BitcoinProvider, max_nodes: int) -> InvestigationResult:
    """Run one live investigation and return the full result. Raises UnsupportedInvestigation or ProviderError."""
    if request.method != "live_lookup":
        raise UnsupportedInvestigation(f"Method '{request.method}' cannot run on the server yet.")
    if request.mode != "crypto_investigation":
        raise UnsupportedInvestigation("Live lookups run only in Crypto Investigation mode for now.")

    if request.input_type == "bitcoin_address":
        if not is_bitcoin_address(request.value):
            raise UnsupportedInvestigation("That does not look like a mainnet Bitcoin address.")
        return await _investigate_address(request, provider, max_nodes)
    if request.input_type == "bitcoin_txid":
        if not is_txid(request.value):
            raise UnsupportedInvestigation("A transaction ID must be 64 hexadecimal characters.")
        return await _investigate_transaction(request, provider, max_nodes)
    raise UnsupportedInvestigation(f"Input type '{request.input_type}' is not supported by live lookup.")


async def _investigate_address(request: InvestigationRequest, provider: BitcoinProvider, max_nodes: int) -> InvestigationResult:
    address = normalize_address(request.value)
    summary = await provider.get_address_summary(address)
    transactions = await provider.get_address_transactions(address)

    builder = GraphBuilder(summary.network, max_nodes)
    builder.add_node(builder.address_node(address))
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
            title=f"Confirmed balance {_sats(summary.confirmed.balance_sats)}; pending change {_sats(summary.unconfirmed.balance_sats)}",
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

    evidence = [
        Evidence(
            id="e1", source="mempool.space address summary", source_url=summary.provenance.source_url,
            retrieved_at=summary.provenance.retrieved_at, method_version=METHOD_VERSION,
            excerpt=f"chain_stats tx_count={summary.confirmed.tx_count}, mempool_stats tx_count={summary.unconfirmed.tx_count}",
        ),
        Evidence(
            id="e2", source="mempool.space address transactions (first page)",
            source_url=transactions[0].provenance.source_url if transactions else summary.provenance.source_url,
            retrieved_at=transactions[0].provenance.retrieved_at if transactions else summary.provenance.retrieved_at,
            method_version=METHOD_VERSION,
            excerpt=f"{len(transactions)} transactions returned on the first page",
        ),
    ]
    now = datetime.now(timezone.utc)
    return InvestigationResult(
        investigation_id=new_investigation_id(now),
        created_at=now,
        input=InvestigationInput(type="bitcoin_address", value=address),
        mode=request.mode,
        method=request.method,
        data_mode="live",
        findings=findings,
        indicators=RULES_NOT_RUN,
        graph=builder.graph(),
        evidence=evidence,
        source_timestamps={provider.name: summary.provenance.retrieved_at},
        limitations=COMMON_LIMITATIONS + ["Only the first page of transactions is shown (paging arrives in Milestone 5)"],
        uncertainty={
            "unconfirmed_tx_count": summary.unconfirmed.tx_count,
            "transactions_shown": shown_tx,
            "transactions_total": total_tx,
            "graph_truncated": builder.truncated,
        },
        scorecard=Scorecard(configured=False),
    )


async def _investigate_transaction(request: InvestigationRequest, provider: BitcoinProvider, max_nodes: int) -> InvestigationResult:
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
            title=f"{len(tx.inputs)} inputs ({named_inputs} with an address) and {len(tx.outputs)} outputs ({named_outputs} with an address); fee {_sats(tx.fee_sats)}",
            kind="observation",
            entity_ids=[tx.entity_id],
            evidence_ids=["e1"],
        ),
    ]
    evidence = [
        Evidence(
            id="e1", source="mempool.space transaction", source_url=tx.provenance.source_url,
            retrieved_at=tx.provenance.retrieved_at, method_version=METHOD_VERSION,
            excerpt=f"status={tx.status}, block_height={tx.block_height}, fee={tx.fee_sats}",
        ),
    ]
    now = datetime.now(timezone.utc)
    return InvestigationResult(
        investigation_id=new_investigation_id(now),
        created_at=now,
        input=InvestigationInput(type="bitcoin_txid", value=txid),
        mode=request.mode,
        method=request.method,
        data_mode="live",
        findings=findings,
        indicators=RULES_NOT_RUN,
        graph=builder.graph(),
        evidence=evidence,
        source_timestamps={provider.name: tx.provenance.retrieved_at},
        limitations=COMMON_LIMITATIONS + ["Bitcoin does not record which input paid which output"],
        uncertainty={"unconfirmed": tx.status == "unconfirmed", "graph_truncated": builder.truncated},
        scorecard=Scorecard(configured=False),
    )
