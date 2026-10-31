"""The "rules_v1" method: indicator-list checks that need no blockchain lookup.

* A website (url_domain): the website lists (CryptoScamDB, MetaMask, ScamSniffer, Polkadot{.js},
  Phishing.Database, Tranco), shared hosting platforms, look-alike names and punycode. The graph
  shows the site with every list that matched (title and licence), any payment addresses
  CryptoScamDB reported for it, and the genuine site it imitates.
* A Bitcoin address: exact match against the OFAC, Ransomwhere and CryptoScamDB lists only
  (use Live lookup in Crypto Investigation for its transactions).

Data mode is "recorded": every answer comes from the downloaded snapshots, not from a live source.
"""
from datetime import datetime, timezone

from app.intelligence.index import IndicatorIndex
from app.intelligence.screening import screen_bitcoin
from app.intelligence.site_screening import _plain, list_match_label, screen_site, site_node_id
from app.intelligence.sources import SOURCES
from app.schemas.investigation import (
    Finding,
    GraphEdge,
    GraphNode,
    InvestigationInput,
    InvestigationRequest,
    InvestigationResult,
    Scorecard,
)
from app.validation import is_bitcoin_address, normalize_address, split_url

SITE_LIMITATIONS = [
    "CryptoScamDB has had no updates since mid-2022 (blacklist) and 2021 (whitelist), so newer scam sites are missing",
    "ScamSniffer's public list is published with a 7-day delay, so the newest scam sites are missing",
    "Phishing.Database covers general phishing (banks, mail, shops), not only crypto, and has some false positives",
    "A website missing from the blacklists is not proof that it is safe; a popular (Tranco) site can still host scams",
    "The look-alike check only compares against about 100 genuine names",
    "The site itself was not visited; nothing here reflects what it shows today",
]
ADDRESS_LIMITATIONS = [
    "Only the address itself was checked against the downloaded lists; its transactions were not fetched",
    "An address missing from those lists is not proof that it is safe",
]


def _address_node_id(code: str, address: str) -> str:
    if code == "BTC":
        return f"bitcoin:mainnet:address:{normalize_address(address)}"
    if address.lower().startswith("0x"):
        return f"ethereum:mainnet:address:{address.lower()}"
    return f"{code.lower()}:mainnet:address:{address}"


def _short(text: str, keep: int = 6) -> str:
    return text if len(text) <= keep * 2 + 3 else f"{text[:keep]}...{text[-keep:]}"


def _result(request: InvestigationRequest, value: str, findings, indicators, nodes, edges, evidence,
            timestamps, limitations, uncertainty, truncated: bool, max_nodes: int) -> InvestigationResult:
    from app.schemas.investigation import Graph, GraphLimits
    from app.services.investigation_service import new_investigation_id

    now = datetime.now(timezone.utc)
    return InvestigationResult(
        investigation_id=new_investigation_id(now), created_at=now,
        input=InvestigationInput(type=request.input_type, value=value),
        mode=request.mode, method=request.method, data_mode="recorded",
        findings=findings, indicators=indicators,
        graph=Graph(nodes=nodes, edges=edges, truncated=truncated, limits=GraphLimits(max_nodes=max_nodes)),
        evidence=evidence, source_timestamps=timestamps, limitations=limitations,
        uncertainty=uncertainty, scorecard=Scorecard(configured=False),
    )


def investigate_site(request: InvestigationRequest, max_nodes: int, index: IndicatorIndex) -> InvestigationResult:
    from app.services.investigation_service import UnsupportedInvestigation

    split = split_url(request.value)
    if split is None:
        raise UnsupportedInvestigation("That does not look like a website address, e.g. example.com.")
    host = split[0]
    screening = screen_site(index, host, request.value)

    details: dict = {}
    listed_in = [hit.title for hit in screening.block_hits + screening.allow_hits]
    if listed_in:
        details["listed_in"] = listed_in
    if screening.block_hits:
        details["blacklisted_by"] = [list_match_label(hit) for hit in screening.block_hits]
    if screening.allow_hits:
        details["allowlisted_by"] = [list_match_label(hit) for hit in screening.allow_hits]
        if screening.block_hits:
            details["allowlist_overruled"] = "a blacklist match wins over the allowlist"
    if screening.popular_hit:
        details["tranco_rank"] = screening.popular_hit.rank
    if screening.shared_platform:
        details["shared_platform"] = screening.shared_platform
    if screening.blacklist_hit:
        record = screening.blacklist_hit.record
        details.update({"category": record.family, "listed_as": screening.blacklist_hit.listed,
                        "reporter_note": record.description})
    if _plain(host) != host:
        details["shown_as"] = _plain(host)
    nodes = [GraphNode(id=site_node_id(host), type="domain", label=host,
                       details={k: v for k, v in details.items() if v})]
    edges, truncated = [], False
    findings = list(screening.findings)

    if screening.lookalike:
        genuine, why = screening.lookalike
        nodes.append(GraphNode(id=site_node_id(genuine), type="domain", label=genuine,
                               details={"listed_in": [SOURCES[screening.lookalike_source].title]}))
        edges.append(GraphEdge(id=f"lookalike:{host}:{genuine}", source=site_node_id(host),
                               target=site_node_id(genuine), relation="inferred", label=f"looks like ({why})",
                               evidence_ids=[screening.evidence_ids[screening.lookalike_source]]))

    reported = screening.blacklist_hit.record.addresses if screening.blacklist_hit else ()
    if reported:
        evidence_id = screening.evidence_ids["cryptoscamdb_blacklist"]
        shown = []
        for code, address in dict.fromkeys(reported):
            node_id = _address_node_id(code, address)
            if len(nodes) >= max_nodes:
                truncated = True
                break
            nodes.append(GraphNode(id=node_id, type="address", label=f"{code} {_short(address)}",
                                   details={"currency": code, "address": address,
                                            "listed_in": ["CryptoScamDB scam website blacklist"]}))
            edges.append(GraphEdge(id=f"reported:{host}:{node_id}", source=site_node_id(host), target=node_id,
                                   relation="reported", label="payment address reported by CryptoScamDB",
                                   evidence_ids=[evidence_id]))
            shown.append(node_id)
        codes = ", ".join(sorted({code for code, _ in reported}))
        findings.append(Finding(
            id=f"f{len(findings) + 1}",
            title=f"CryptoScamDB reported {len(reported)} payment address{'es' if len(reported) > 1 else ''} "
                  f"for this site ({codes}); look {'them' if len(reported) > 1 else 'it'} up in Crypto Investigation",
            kind="rule_match", entity_ids=shown, evidence_ids=[evidence_id]))

    uncertainty = {"blacklisted": bool(screening.block_hits),
         "blacklists_matched": ", ".join(h.title for h in screening.block_hits) or "none",
         "known_legitimate": screening.known_legitimate,
         "whitelisted": screening.whitelist_hit is not None,
         "popular_tranco_rank": screening.popular_hit.rank if screening.popular_hit else None,
         "shared_platform": screening.shared_platform,
         "lookalike": screening.lookalike is not None,
         "lists_checked": screening.lists_checked,
         "data_sources": ", ".join(screening.source_timestamps) or "none (lists not downloaded)"}
    return _result(
        request, host, findings, screening.indicators, nodes, edges, screening.evidence,
        screening.source_timestamps, SITE_LIMITATIONS,
        {key: value for key, value in uncertainty.items() if value is not None}, truncated, max_nodes)


def investigate_address_lists(request: InvestigationRequest, max_nodes: int,
                              index: IndicatorIndex) -> InvestigationResult:
    from app.services.investigation_service import UnsupportedInvestigation

    if not is_bitcoin_address(request.value):
        raise UnsupportedInvestigation("That does not look like a mainnet Bitcoin address.")
    address = normalize_address(request.value)
    screening = screen_bitcoin(index, "mainnet", address, [address], first_finding=1, first_evidence=1)
    # With only one address there are no counterparties: keep the subject checks.
    indicators = [i for i in screening.indicators if not i.rule.endswith("_counterparty_match")]
    for number, indicator in enumerate(indicators, start=1):
        indicator.id = f"i{number}"
    node_id = f"bitcoin:mainnet:address:{address}"
    listed = screening.flagged_nodes.get(node_id)
    node = GraphNode(id=node_id, type="address", label=_short(address),
                     details={"listed_in": listed} if listed else {})
    return _result(
        request, address, screening.findings, indicators, [node], [], screening.evidence,
        screening.source_timestamps, ADDRESS_LIMITATIONS,
        {"lists_checked": len(screening.evidence),
         "data_sources": ", ".join(screening.source_timestamps) or "none (lists not downloaded)"},
        False, max_nodes)
