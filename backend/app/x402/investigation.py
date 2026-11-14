"""The x402 / Agentic Payments investigation mode: a Base address or transaction, or a Solana address.

Base: live USDC transfers (app/x402/chain.py), classified as x402 by the facilitator that sent them, then checked
with the rules (app/x402/rules.py). Solana: the snapshot only (no live Solana x402 reader yet).
The graph is funder -> client (payer) -> payment -> server payment address, with the facilitator that settled it.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from app.schemas.investigation import (
    Evidence,
    Finding,
    GraphEdge,
    GraphNode,
    Indicator,
    InvestigationInput,
    InvestigationRequest,
    InvestigationResult,
    Scorecard,
)
from app.services.investigation_service import GraphBuilder, UnsupportedInvestigation, _short, new_investigation_id
from app.x402.chain import BaseX402Reader, ChainError
from app.x402.rules import SEVERITY_ORDER, Hit, Payment, check_address, check_payment, worst
from app.x402.snapshot import FACILITATOR_LIST_URL, SOURCE_URL, Snapshot, get_snapshot, norm

METHOD_VERSION = "x402_rules_v1"
INPUTS = ("base_address", "base_txid", "solana_address")
LIMITATIONS = [
    "Rules, not a model: each finding is a reason to look closer, not a verdict",
    "Phishing labels are weak (51 Blockscout tags reviewed by hand in x402analysis) and only cover Base",
    "Server, client and funder data is a snapshot (x402analysis, October 2026); newer servers are missing",
    "A payment counts as x402 when a listed facilitator sent the transaction (x402analysis + x402scan lists), "
    "or when a gasless USDC transfer reaches a known x402 server",
]


def _evidence_snapshot(snap: Snapshot, now: datetime, eid: str) -> Evidence:
    g = snap.meta.get("gathered", {})
    c = snap.meta.get("counts", {})
    built = snap.meta.get("built_at")
    return Evidence(
        id=eid, source="x402analysis dataset (Peter Robinson, MIT) + x402scan facilitator list (MIT)",
        source_url=SOURCE_URL, retrieved_at=datetime.fromisoformat(built) if built else now,
        method_version=METHOD_VERSION,
        excerpt=(f"{c.get('payment_addresses', 0):,} server payment addresses, {c.get('facilitator_addresses', 0)} "
                 f"facilitator addresses, {c.get('phishing_addresses', 0)} phishing tags, "
                 f"{c.get('clients', 0):,} clients; services gathered {str(g.get('services'))[:10]}, "
                 f"clients {str(g.get('clients_base'))[:10]}. Facilitators also from {FACILITATOR_LIST_URL}."))


class _Result:
    def __init__(self, request: InvestigationRequest, chain: str, max_nodes: int):
        self.request = request
        self.chain = chain
        self.now = datetime.now(timezone.utc)
        self.builder = GraphBuilder("mainnet", max_nodes, chain=chain)
        self.findings: list[Finding] = []
        self.indicators: list[Indicator] = []
        self.evidence: list[Evidence] = []
        self.sources: dict[str, datetime] = {}
        self.limitations = list(LIMITATIONS)

    def node(self, address: str, snap: Snapshot, **details) -> str | None:
        a = norm(address)
        roles = snap.role_of(a)
        label = _short(a)
        server = snap.server(a)
        fac = snap.facilitator(a)
        if server:
            label = (server["servers"][0]["name"] or label)[:28]
            details.setdefault("x402_servers", [s["name"] for s in server["servers"]][:5])
            details.setdefault("x402_categories", list(server["categories"])[:8])
        elif fac:
            label = f"{fac['names'][0]} (facilitator)"
        if a in snap.phishing:
            details["listed_in"] = ["Phishing tag (Blockscout via x402analysis)"]
        if roles:
            details.setdefault("x402_roles", roles)
        node = self.builder.address_node(a).model_copy(update={"label": label, "details": details})
        return node.id if self.builder.add_node(node) else None

    def add_payment(self, p: Payment, snap: Snapshot, eid: str, hits: list[Hit]) -> None:
        tx_id = f"{self.chain}:mainnet:payment:{p.tx}:{p.payer}:{p.pay_to}"
        sev = worst(hits)
        label = f"{p.amount:,.4f} USDC".rstrip("0").rstrip(".") if p.amount is not None else "USDC"
        if not self.builder.add_node(GraphNode(id=tx_id, type="payment", label=label, details={
                "tx_hash": p.tx, "amount_usdc": p.amount, "time": p.at, "block": p.block, "x402": True,
                "severity": sev, "rules": [h.rule for h in hits]})):
            return
        payer = self.node(p.payer, snap)
        pay_to = self.node(p.pay_to, snap)
        if payer:
            self.builder.add_edge(GraphEdge(id=f"{tx_id}:in", source=payer, target=tx_id, relation="observed",
                                            label="paid", evidence_ids=[eid]))
        if pay_to:
            self.builder.add_edge(GraphEdge(id=f"{tx_id}:out", source=tx_id, target=pay_to, relation="observed",
                                            label="to server", evidence_ids=[eid]))
        if p.facilitator:
            fac = self.node(p.facilitator, snap)
            if fac:
                self.builder.add_edge(GraphEdge(id=f"{tx_id}:fac", source=fac, target=tx_id, relation="observed",
                                                label="settled", evidence_ids=[eid]))

    def add_funder(self, address: str, snap: Snapshot, eid: str) -> None:
        f = snap.phishing_funded.get(norm(address))
        if not f:
            return
        funder = self.node(f["funder"], snap)
        child = self.node(address, snap)
        if funder and child:
            self.builder.add_edge(GraphEdge(id=f"fund:{f['funder']}:{norm(address)}", source=funder, target=child,
                                            relation="reported", label=f"funded ~US${f['usd']:,.2f}",
                                            evidence_ids=[eid]))

    def add_hits(self, hits: list[Hit], eid: str, default_entity: str | None) -> None:
        seen = set()
        for h in sorted(hits, key=lambda h: -SEVERITY_ORDER[h.severity]):
            key = (h.rule, h.address, h.title)
            if key in seen:
                continue
            seen.add(key)
            entity = self.builder.address_node(h.address).id if h.address else default_entity
            sev = h.severity.upper()
            self.findings.append(Finding(
                id=f"f{len(self.findings) + 1}", title=f"[{sev}] {h.title}. {h.detail}",
                kind="rule_match" if h.severity != "info" else "observation",
                entity_ids=[entity] if entity and entity in self.builder.nodes else [], evidence_ids=[eid]))

    def result(self, value: str) -> InvestigationResult:
        for i, ind in enumerate(self.indicators, start=1):
            ind.id = f"i{i}"
        return InvestigationResult(
            investigation_id=new_investigation_id(self.now), created_at=self.now,
            input=InvestigationInput(type=self.request.input_type, value=value), mode=self.request.mode,
            method=self.request.method, data_mode="live", findings=self.findings, indicators=self.indicators,
            graph=self.builder.graph(), evidence=self.evidence, source_timestamps=self.sources,
            limitations=self.limitations,
            uncertainty={"labels": "weak", "snapshot_built": str(get_snapshot().meta.get("built_at") or "missing")},
            scorecard=Scorecard(configured=False))


def _indicators(r: _Result, hits: list[Hit], rules: list[tuple[str, str]]) -> None:
    fired = {h.rule for h in hits}
    for rule, text in rules:
        r.indicators.append(Indicator(id="", rule=rule, matched=rule in fired,
                                      reason=text if rule in fired else f"Not matched: {text.lower()}"))


RULE_TEXT = [
    ("phishing_address", "Address tagged as phishing (Blockscout metadata via x402analysis)"),
    ("phishing_funded", "Address funded by a phishing-tagged address (dust under US$0.01 ignored)"),
    ("sanctions_or_scam_list", "Address on OFAC or CryptoScamDB"),
    ("tainted_server", "Server received 5% or more of its USDC from phishing-linked clients"),
    ("risky_service", "Server sells high-risk services (gift cards, eSIMs, attack tools, exposure lookups)"),
    ("large_payment", "Payment of US$1,000 or more"),
    ("odd_amount", "Payment far above the server's listed prices"),
    ("cash_out_purchase", "US$50 or more spent at a gift card / eSIM seller"),
]


async def investigate_x402(request: InvestigationRequest, max_nodes: int, indicators=None,
                           reader: BaseX402Reader | None = None) -> InvestigationResult:
    snap = get_snapshot()
    if not snap.loaded:
        raise UnsupportedInvestigation("The x402 snapshot is missing: run python -m training.x402_snapshot "
                                       "(see backend/data/x402/README.md).")
    if request.input_type not in INPUTS:
        raise UnsupportedInvestigation("x402 mode takes a Base address, a Base transaction hash or a Solana address.")
    value = request.value.strip()
    chain = request.input_type.split("_")[0]
    r = _Result(request, chain, max_nodes)
    r.evidence.append(_evidence_snapshot(snap, r.now, "e1"))
    r.sources["x402analysis snapshot"] = r.evidence[0].retrieved_at

    if request.input_type == "solana_address":
        return _solana_address(r, snap, value, indicators)
    reader = reader or BaseX402Reader.from_env(snap)
    if request.input_type == "base_txid":
        if not (value.startswith("0x") and len(value) == 66):
            raise UnsupportedInvestigation("A Base transaction hash is 0x followed by 64 hexadecimal characters.")
        return await _base_tx(r, snap, reader, value.lower(), indicators)
    if not (value.startswith("0x") and len(value) == 42):
        raise UnsupportedInvestigation("A Base address is 0x followed by 40 hexadecimal characters.")
    return await _base_address(r, snap, reader, value.lower(), indicators)


async def _base_tx(r: _Result, snap: Snapshot, reader: BaseX402Reader, tx: str, index) -> InvestigationResult:
    try:
        data = await reader.transaction(tx)
    except ChainError as exc:
        raise UnsupportedInvestigation(f"Could not read that transaction: {exc}") from None
    r.evidence.append(Evidence(id="e2", source=data["source"], source_url=data["source_url"], retrieved_at=r.now,
                               method_version=METHOD_VERSION,
                               excerpt=f"Sent by {data['sender']}; {len(data['payments'])} USDC transfer(s)."))
    r.sources[data["source"]] = r.now
    payments = data["payments"]
    x402 = [p for p in payments if reader.is_x402(p)]
    fac = snap.facilitator(data["sender"] or "")
    if not payments:
        r.findings.append(Finding(id="f1", title="No USDC transfer in this transaction, so it is not an x402 payment",
                                  kind="observation", entity_ids=[], evidence_ids=["e2"]))
    elif not x402:
        r.findings.append(Finding(id="f1", kind="observation", entity_ids=[], evidence_ids=["e2", "e1"],
                                  title=f"USDC moved, but the sender {_short(data['sender'])} is not a listed x402 "
                                        "facilitator: probably not an x402 payment"))
    else:
        r.findings.append(Finding(id="f1", kind="observation", entity_ids=[], evidence_ids=["e2", "e1"],
                                  title=f"x402 payment settled by {fac['names'][0] if fac else _short(data['sender'])}"
                                        f" ({len(x402)} USDC transfer{'s' if len(x402) > 1 else ''})"))
    hits: list[Hit] = []
    for p in (x402 or payments):
        h = check_payment(snap, p, index)
        hits += h
        r.add_payment(p, snap, "e2", h)
        r.add_funder(p.payer, snap, "e1")
    r.add_hits(hits, "e1", None)
    _indicators(r, hits, RULE_TEXT)
    return r.result(tx)


async def _base_address(r: _Result, snap: Snapshot, reader: BaseX402Reader, address: str, index) -> InvestigationResult:
    roles = snap.role_of(address)
    subject = r.node(address, snap, subject=True)
    hits = check_address(snap, address, "base", index)
    client = snap.client(address)
    server = snap.server(address)
    facts = []
    if client:
        facts.append(f"paid {client['servers_paid']} x402 server(s) {client['transfers']:,} times, "
                     f"US${client['usdc']:,.2f} in all (top: {client['top_server']})")
    if server:
        t = snap.taint.get(address) or {}
        facts.append(f"is the payment address of {', '.join(s['name'] for s in server['servers'][:3])}"
                     + (f", which received US${t['usdc_received']:,.2f} from {t['clients']:,} clients" if t else ""))
    if snap.facilitator(address):
        facts.append(f"is a facilitator address ({', '.join(snap.facilitator(address)['names'])})")
    r.findings.append(Finding(id="f1", kind="observation", entity_ids=[subject] if subject else [], evidence_ids=["e1"],
                              title=("In the x402 snapshot: this address " + "; ".join(facts)) if facts else
                              "This address is not in the x402 snapshot (no x402 payments known as of October 2026)"))
    r.add_funder(address, snap, "e1")
    try:
        data = await reader.address_transfers(address, limit=50)
    except ChainError as exc:
        r.limitations.append(f"Live transfers could not be read ({exc}); findings use the snapshot only")
        data = None
    if data:
        r.evidence.append(Evidence(id="e2", source=data["source"], source_url=data["source_url"], retrieved_at=r.now,
                                   method_version=METHOD_VERSION,
                                   excerpt=f"{len(data['payments'])} most recent USDC transfers of this address."))
        r.sources[data["source"]] = r.now
        x402 = [p for p in data["payments"] if reader.is_x402(p)]
        total = sum(p.amount or 0 for p in x402)
        by_fac = Counter((snap.facilitator(p.facilitator or "") or {"names": ["unknown"]})["names"][0] for p in x402)
        r.findings.append(Finding(
            id=f"f{len(r.findings) + 1}", kind="observation", entity_ids=[subject] if subject else [],
            evidence_ids=["e2"],
            title=(f"{len(x402)} of the last {len(data['payments'])} USDC transfers are x402 payments "
                   f"(US${total:,.2f}); settled by {', '.join(f'{k} ({v})' for k, v in by_fac.most_common(3))}")
            if x402 else f"None of the last {len(data['payments'])} USDC transfers is a recognised x402 payment"))
        for p in x402[:20]:
            h = check_payment(snap, p, index)
            hits += [x for x in h if x.rule in ("phishing_address", "phishing_funded", "sanctions_or_scam_list",
                                                 "large_payment", "odd_amount", "cash_out_purchase")]
            r.add_payment(p, snap, "e2", h)
        if len(x402) > 20:
            r.limitations.append("Only the 20 most recent x402 payments are drawn in the graph")
    if "server" not in roles and "client" not in roles and not data:
        r.limitations.append("Unknown to the snapshot and no live data: nothing more to check")
    r.add_hits(hits, "e1", subject)
    _indicators(r, hits, RULE_TEXT)
    return r.result(address)


def _solana_address(r: _Result, snap: Snapshot, address: str, index) -> InvestigationResult:
    subject = r.node(address, snap, subject=True)
    hits = check_address(snap, address, "solana", index)
    client, server = snap.client(address), snap.server(address)
    facts = []
    if client:
        facts.append(f"paid {client['servers_paid']} x402 server(s) {client['transfers']:,} times, "
                     f"US${client['usdc']:,.2f} in all (top: {client['top_server']})")
    if server:
        facts.append(f"is the payment address of {', '.join(s['name'] for s in server['servers'][:3])}")
    if snap.facilitator(address):
        facts.append(f"is a facilitator address ({', '.join(snap.facilitator(address)['names'])})")
    r.findings.append(Finding(id="f1", kind="observation", entity_ids=[subject] if subject else [], evidence_ids=["e1"],
                              title=("In the x402 snapshot: this address " + "; ".join(facts)) if facts else
                              "This Solana address is not in the x402 snapshot"))
    r.limitations.append("Solana x402 checks use the snapshot only; live Solana payments are not read in this mode yet")
    r.add_hits(hits, "e1", subject)
    _indicators(r, hits, [x for x in RULE_TEXT if x[0] in ("sanctions_or_scam_list", "tainted_server", "risky_service")])
    return r.result(address)
