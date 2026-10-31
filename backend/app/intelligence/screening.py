"""Exact-match screening of investigation addresses against the OFAC and Ransomwhere snapshots.

The output is indicators (did the rule match?), findings for each match, and one evidence item per
list naming the snapshot that was checked. A non-match only means "not in this snapshot".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.intelligence.index import Hit, IndicatorIndex
from app.intelligence.sources import SOURCES
from app.schemas.investigation import Evidence, Finding, Indicator

METHOD_VERSION = "exact_list_match_v1"
# (source id, rule prefix) per chain, in the order they are reported.
CHECKS = {
    "bitcoin": (("ofac_xbt", "ofac"), ("ransomwhere", "ransomwhere")),
    "ethereum": (("ofac_eth", "ofac"),),
    "base": (("ofac_eth", "ofac"),),  # OFAC lists Ethereum-format addresses without naming the chain
    "solana": (("ofac_sol", "ofac"),),
}
BITCOIN_CHECKS = CHECKS["bitcoin"]
NOT_SAFE = "Not being listed does not mean an address is safe."


def _short(text: str, keep: int = 6) -> str:
    return text if len(text) <= keep * 2 + 3 else f"{text[:keep]}...{text[-keep:]}"


def _describe(address: str, hit: Hit) -> str:
    return f"{_short(address)} ({hit.family})" if hit.family else _short(address)


@dataclass
class Screening:
    indicators: list[Indicator] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    source_timestamps: dict[str, datetime] = field(default_factory=dict)
    # graph node id -> titles of the lists it is on
    flagged_nodes: dict[str, list[str]] = field(default_factory=dict)


def screen_bitcoin(index: IndicatorIndex, network: str, subject: str | None, addresses: list[str],
                   first_finding: int, first_evidence: int) -> Screening:
    return screen_addresses(index, "bitcoin", network, subject, addresses, first_finding, first_evidence)


def screen_addresses(index: IndicatorIndex, chain: str, network: str, subject: str | None, addresses: list[str],
                     first_finding: int, first_evidence: int,
                     scope: str = "addresses shown in the graph") -> Screening:
    """Check the investigated address (subject; None for a transaction) and the other graph addresses
    against the lists for `chain` ("bitcoin" or "ethereum"). Addresses are spelled as in the graph."""
    out = Screening()
    node_id = lambda address: f"{chain}:{network}:address:{address}"  # noqa: E731
    others = [a for a in dict.fromkeys(addresses) if a != subject]
    finding_no, evidence_no = first_finding, first_evidence

    for source_id, prefix in CHECKS[chain]:
        loaded = index.loaded(source_id)
        rule_subject = f"{prefix}_exact_match"
        rule_others = f"{prefix}_counterparty_match" if subject else f"{prefix}_address_in_transaction"
        if loaded is None:
            reason = (f"Not run: the {SOURCES[source_id].title} snapshot has not been downloaded yet "
                      "(run: python -m app.intelligence refresh)")
            if subject:
                out.indicators.append(Indicator(id="", rule=rule_subject, matched=None, reason=reason))
            out.indicators.append(Indicator(id="", rule=rule_others, matched=None, reason=reason))
            continue

        evidence_id = f"e{evidence_no}"
        evidence_no += 1
        meta = loaded.meta
        excerpt = f"{len(loaded.records):,} addresses; sha256 {str(meta.get('sha256'))[:12]}"
        if meta.get("list_updated_at"):
            excerpt += f"; list last changed {meta['list_updated_at']}"
        if loaded.source.note:
            excerpt += f". {loaded.source.note}"
        out.evidence.append(Evidence(
            id=evidence_id, source=loaded.source.title, source_url=loaded.source.url,
            retrieved_at=loaded.retrieved_at, method_version=METHOD_VERSION, excerpt=excerpt))
        out.source_timestamps[loaded.source.title] = loaded.retrieved_at
        snapshot = f"{loaded.source.title} {loaded.snapshot_label()}"

        def record_hits(hits: list[tuple[str, Hit]], title: str) -> None:
            nonlocal finding_no
            ids = [node_id(address) for address, _ in hits]
            for i in ids:
                out.flagged_nodes.setdefault(i, []).append(loaded.source.title)
            out.findings.append(Finding(id=f"f{finding_no}", title=title, kind="rule_match",
                                        entity_ids=list(dict.fromkeys(ids)), evidence_ids=[evidence_id]))
            finding_no += 1

        if subject:
            hits = [(subject, h) for h in index.lookup(subject, [source_id])]
            if hits:
                family = hits[0][1].family
                record_hits(hits, f"Address is {loaded.source.label}" + (f" ({family})" if family else ""))
                reason = f"{_describe(*hits[0])} is in the {snapshot}."
            else:
                reason = f"Not in the {snapshot}. {NOT_SAFE}"
            out.indicators.append(Indicator(id="", rule=rule_subject, matched=bool(hits), reason=reason))

        if not others:
            out.indicators.append(Indicator(id="", rule=rule_others, matched=None,
                                            reason=f"Not run: no other {scope}"))
            continue
        hits = [(a, h) for a in others for h in index.lookup(a, [source_id])]
        if hits:
            names = ", ".join(_describe(a, h) for a, h in hits[:5]) + (" and more" if len(hits) > 5 else "")
            many = len(hits) > 1
            if subject:
                who = f"{len(hits)} linked addresses are" if many else "1 linked address is"
            else:
                who = f"{len(hits)} addresses in this transaction are" if many else "1 address in this transaction is"
            record_hits(hits, f"{who} {loaded.source.label}")
            reason = f"{len(hits)} of {len(others):,} {scope} are in the {snapshot}: {names}."
        else:
            reason = f"None of the {len(others):,} {scope} is in the {snapshot}. {NOT_SAFE}"
        out.indicators.append(Indicator(id="", rule=rule_others, matched=bool(hits), reason=reason))

    for number, indicator in enumerate(out.indicators, start=1):
        indicator.id = f"i{number}"
    return out
