"""Run the switched-on rule packs over one address's facts and explain every result.

Each rule ends in one of three states:
* "matched"     the condition is true: a red flag, with the numbers behind it
* "clear"       the condition is false
* "not_checked" a fact it needs is unknown (shown so an unchecked rule never looks clean)

The output also carries findings, indicators and evidence in the same shape as an investigation
result (ids start with "rf"), so the dashboard can merge them into the Findings and Evidence tabs and
the evidence export and AI analyst can cite them.

Red flags are kept apart from the model score on purpose: authorities publish them as reasons to look
closer, not as proof, and they are not calibrated against labelled data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from string import Formatter

from app.redflags.expr import MissingFacts
from app.redflags.facts import CATALOG
from app.redflags.packs import SEVERITY_ORDER, PackRegistry
from app.schemas.investigation import Evidence, Finding, Indicator

METHOD_VERSION = "authority_red_flags_v1"
DISCLAIMER = ("Red flags are indicators that authorities recommend looking into. A red flag is not proof of "
              "wrongdoing, and having none is not proof that an address is safe.")
LIST_FACTS = {
    "on_sanctions_list": "ofac_xbt", "sanctioned_counterparties": "ofac_xbt",
    "on_ransomware_list": "ransomwhere", "ransomware_counterparties": "ransomwhere",
    "on_scam_list": "cryptoscamdb_blacklist", "scam_counterparties": "cryptoscamdb_blacklist",
}
COUNTERPARTY_FACTS = {"sanctioned_counterparties": "sanction", "ransomware_counterparties": "ransomware",
                      "scam_counterparties": "scam", "illicit_label_counterparties": "illicit_label",
                      "illicit_payer_share": "illicit_label"}


def fmt(value) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e12:
            return f"{int(value):,}"
        return f"{value:,.4g}" if abs(value) < 1000 else f"{value:,.0f}"
    return str(value)


def render(template: str, facts: dict) -> str:
    out = []
    for literal, field, spec, _ in Formatter().parse(template):
        out.append(literal)
        if field is None:
            continue
        value = facts.get(field)
        if value is None:
            out.append("unknown")
        elif spec:
            try:
                out.append(format(value, spec))
            except (ValueError, TypeError):
                out.append(fmt(value))
        else:
            out.append(fmt(value))
    return "".join(out)


def evaluate(registry: PackRegistry, chain: str, facts: dict) -> list[dict]:
    """Every rule of every switched-on pack for this chain, with its state and explanation."""
    results = []
    for loaded, pack_weight in registry.enabled(chain):
        pack = loaded.pack
        for rule in pack.rules:
            if chain not in (rule.chains or pack.chains):
                continue
            condition = loaded.conditions[rule.id]
            used = {name: facts.get(name) for name in sorted(condition.names)}
            try:
                hit = condition.evaluate(facts)
            except MissingFacts as exc:
                status = "not_checked"
                reason = ("Not checked: no value for " + ", ".join(sorted(exc.names)) + ". "
                          + " ".join(CATALOG[n].description for n in sorted(exc.names) if "not available" in CATALOG[n].description))
            else:
                status = "matched" if hit else "clear"
                values = ", ".join(f"{k.replace('_', ' ')} {fmt(v)}" for k, v in used.items())
                reason = (render(rule.explain, facts) if hit and rule.explain else
                          f"{'Fired' if hit else 'Did not fire'}: {values} (rule: {condition.text}).")
            results.append({
                "pack_id": pack.id, "pack_title": pack.title, "authority": pack.authority,
                "rule_id": rule.id, "key": f"{pack.id}.{rule.id}", "title": rule.title,
                "status": status, "severity": rule.severity,
                "weight": round(rule.weight * pack_weight, 4),
                "condition": condition.text, "facts_used": used, "reason": reason.strip(),
                "advice": rule.advice, "citation": rule.citation,
                "document_title": pack.document.title, "document_url": pack.document.url,
                "alert": rule.alert,
            })
    order = {"matched": 0, "not_checked": 1, "clear": 2}
    results.sort(key=lambda r: (order[r["status"]], -SEVERITY_ORDER[r["severity"]], -r["weight"]))
    return results


def summarise(results: list[dict]) -> dict:
    matched = [r for r in results if r["status"] == "matched"]
    highest = max((r["severity"] for r in matched), key=SEVERITY_ORDER.get, default=None)
    return {
        "rules": len(results),
        "matched": len(matched),
        "clear": sum(r["status"] == "clear" for r in results),
        "not_checked": sum(r["status"] == "not_checked" for r in results),
        "highest_severity": highest,
        "weighted_total": round(sum(r["weight"] for r in matched), 3),
        "weight_possible": round(sum(r["weight"] for r in results if r["status"] != "not_checked"), 3),
        "authorities": sorted({r["authority"] for r in matched}),
    }


def build_report(registry: PackRegistry, chain: str, network: str, address: str, facts_bundle,
                 data_source: str, retrieved_at: datetime | None = None) -> dict:
    """The full answer for one address: results, summary, facts and citable findings and evidence."""
    retrieved_at = retrieved_at or datetime.now(timezone.utc)
    facts = facts_bundle.facts
    results = evaluate(registry, chain, facts)
    subject_id = f"{chain}:{network}:address:{address}"

    evidence: list[Evidence] = []
    data_evidence = "rfe1"
    window = (f"{fmt(facts.get('tx_count'))} transactions in the window ending at block "
              f"{facts_bundle.anchor_block}" if facts_bundle.anchor_block else "No confirmed transactions found")
    evidence.append(Evidence(id=data_evidence, source=f"{data_source}: address history (red-flag facts)",
                             source_url=f"https://mempool.space/address/{address}", retrieved_at=retrieved_at,
                             method_version=METHOD_VERSION, excerpt=window))
    list_evidence: dict[str, str] = {}
    for source_id, info in facts_bundle.lists_checked.items():
        eid = f"rfe{len(evidence) + 1}"
        list_evidence[source_id] = eid
        evidence.append(Evidence(id=eid, source=info["title"], source_url=info["url"],
                                 retrieved_at=info["retrieved_at"] or retrieved_at, method_version=METHOD_VERSION,
                                 excerpt=f"Exact address match against the {info['title']} {info['snapshot']}"))
    pack_evidence: dict[str, str] = {}
    for loaded, _ in registry.enabled(chain):
        pack = loaded.pack
        if not any(r["pack_id"] == pack.id for r in results):
            continue
        eid = f"rfe{len(evidence) + 1}"
        pack_evidence[pack.id] = eid
        ref = f" ({pack.document.reference})" if pack.document.reference else ""
        evidence.append(Evidence(
            id=eid, source=f"{pack.authority}: {pack.document.title}{ref}", source_url=pack.document.url,
            retrieved_at=retrieved_at, method_version=f"rule pack {pack.id} v{pack.version}",
            excerpt=(f"Rule pack '{pack.title}' ({len(pack.rules)} rules, written in our own words from the "
                     f"published indicators). Licence: {pack.licence}")))

    findings: list[Finding] = []
    indicators: list[Indicator] = []
    flagged_nodes: dict[str, list[str]] = {}
    for number, r in enumerate(results, start=1):
        matched = {"matched": True, "clear": False, "not_checked": None}[r["status"]]
        indicators.append(Indicator(id=f"rfi{number}", rule=r["key"], matched=matched,
                                    reason=f"{r['authority']}, {r['citation']}: {r['reason']}"))
        if r["status"] != "matched":
            continue
        entities = [subject_id]
        evidence_ids = [pack_evidence[r["pack_id"]], data_evidence]
        for fact in r["facts_used"]:
            if fact in LIST_FACTS and LIST_FACTS[fact] in list_evidence:
                evidence_ids.append(list_evidence[LIST_FACTS[fact]])
            kind = COUNTERPARTY_FACTS.get(fact)
            for other, _family in facts_bundle.listed_counterparties.get(kind, [])[:20] if kind else []:
                node = f"{chain}:{network}:address:{other}"
                entities.append(node)
                flagged_nodes.setdefault(node, []).append(r["title"])
        findings.append(Finding(
            id=f"rff{len(findings) + 1}", kind="rule_match",
            title=f"Red flag ({r['severity']}): {r['title']}. {r['reason']} [{r['authority']}, {r['citation']}]",
            entity_ids=list(dict.fromkeys(entities)), evidence_ids=list(dict.fromkeys(evidence_ids))))

    limitations = [DISCLAIMER,
                   "Facts cover the address's newest four weeks of activity (at most 100 transactions), "
                   "the same window as the live model."]
    if facts.get("window_truncated"):
        limitations.append("This address had more than 100 transactions in the window, so only the newest 100 were used.")
    missing_lists = {"ofac_xbt", "ransomwhere", "cryptoscamdb_blacklist"} - set(facts_bundle.lists_checked)
    if missing_lists:
        limitations.append("Some lists are not downloaded on this server (" + ", ".join(sorted(missing_lists))
                           + "), so rules that use them were not checked.")

    return {
        "chain": chain, "network": network, "address": address, "method_version": METHOD_VERSION,
        "retrieved_at": retrieved_at.isoformat(), "anchor_block": facts_bundle.anchor_block,
        "summary": summarise(results), "results": results,
        "packs": [{"id": l.pack.id, "title": l.pack.title, "authority": l.pack.authority,
                   "document_url": l.pack.document.url, "weight": w} for l, w in registry.enabled(chain)],
        "facts": facts,
        "findings": [f.model_dump(mode="json") for f in findings],
        "indicators": [i.model_dump(mode="json") for i in indicators],
        "evidence": [e.model_dump(mode="json") for e in evidence],
        "flagged_nodes": flagged_nodes,
        "limitations": limitations,
        "disclaimer": DISCLAIMER,
    }
