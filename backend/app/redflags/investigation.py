"""The "authority_rules" investigation method: a live Bitcoin lookup plus the authority red flags.

The lookup's graph, findings and evidence stay as they are; the red flags are added on top (ids start
with "rf") and graph nodes that a rule pointed at get details["red_flags"].
"""
from __future__ import annotations

from app.schemas.investigation import Evidence, Finding, Indicator, InvestigationRequest, InvestigationResult


def merge_red_flags(result: InvestigationResult, report: dict) -> InvestigationResult:
    findings = [*result.findings, *(Finding.model_validate(f) for f in report["findings"])]
    indicators = [*result.indicators, *(Indicator.model_validate(i) for i in report["indicators"])]
    evidence = [*result.evidence, *(Evidence.model_validate(e) for e in report["evidence"])]
    subject = f"bitcoin:mainnet:address:{report['address']}"
    matched = [r["title"] for r in report["results"] if r["status"] == "matched"]
    nodes = []
    for node in result.graph.nodes:
        flags = list(report["flagged_nodes"].get(node.id, []))
        if node.id == subject and matched:
            flags = matched
        nodes.append(node.model_copy(update={"details": {**node.details, "red_flags": flags}}) if flags else node)
    graph = result.graph.model_copy(update={"nodes": nodes})
    explanation = dict(result.explanation or {})
    explanation["red_flags"] = {k: report[k] for k in ("summary", "results", "packs", "facts", "method_version")}
    return result.model_copy(update={
        "findings": findings, "indicators": indicators, "evidence": evidence, "graph": graph,
        "limitations": [*result.limitations, *report["limitations"]], "explanation": explanation,
        "method": "authority_rules",
    })


async def investigate_with_red_flags(request: InvestigationRequest, provider, max_nodes: int, index,
                                     run_lookup) -> InvestigationResult:
    from app.redflags.service import bitcoin_red_flags
    from app.validation import normalize_address

    live = request.model_copy(update={"method": "live_lookup"})
    result = await run_lookup(live)
    report = await bitcoin_red_flags(provider, normalize_address(request.value), index)
    return merge_red_flags(result, report)
