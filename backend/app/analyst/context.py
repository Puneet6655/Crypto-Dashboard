"""Turn an investigation result (as the dashboard holds it) into a short fact sheet for the model.

A full result can carry hundreds of graph nodes. A small local model works best with a compact list of
what matters: the subject, the findings, the rules that matched, the model score and its top SHAP
features, and the most relevant counterparties and transactions. Lists are cut to fixed sizes, and the
fact sheet says when something was cut, so the model can say "not in the data" instead of guessing.
"""
from __future__ import annotations

from app.analyst.features import meaning

from dataclasses import dataclass

MAX_FINDINGS = 40
MAX_COUNTERPARTIES = 25
MAX_TRANSACTIONS = 25
MAX_SHAP = 8


@dataclass(frozen=True)
class Limits:
    """How much of the case goes into the fact sheet."""
    findings: int = MAX_FINDINGS
    counterparties: int = MAX_COUNTERPARTIES
    transactions: int = MAX_TRANSACTIONS
    shap: int = MAX_SHAP
    links_per_counterparty: int = 4
    other_entities: int = 15
    limitations: int = 12


# The local 8B model has a 10k-token window, so it gets the compact sheet. Claude (through the Claude CLI) has a
# far larger window, so it gets the whole graph up to the graph explorer's 1,500-node cap.
LOCAL = Limits()
FULL = Limits(findings=500, counterparties=1500, transactions=1500, shap=20, links_per_counterparty=40,
              other_entities=200, limitations=50)


def _value_of(entity_id: str) -> str:
    """'bitcoin:mainnet:address:bc1q...' -> 'bc1q...'; anything without that shape is returned as is.

    Only "<chain>:<network>:<kind>:<value>" ids are shortened. A graph explorer group such as
    "summary:more:bitcoin:mainnet:address:3ES6..." is NOT that address, so it keeps its full id.
    """
    parts = entity_id.split(":")
    return parts[3] if len(parts) == 4 and parts[2] in ("address", "tx") else entity_id


def _model_score(explanation: dict | None, limits: Limits = LOCAL) -> dict | None:
    if not isinstance(explanation, dict) or "score" not in explanation:
        return None
    shap = explanation.get("explanation") or {}
    live = explanation.get("live") or {}
    model = explanation.get("model") or {}
    facts = {
        "model": model.get("name") or model.get("id"),
        "score": explanation.get("score"),
        "threshold": explanation.get("threshold"),
        "verdict": "at or above threshold (flagged)" if explanation.get("flagged") else "below threshold (not flagged)",
        "recent_2026_alert_threshold": explanation.get("recent_2026_threshold"),
        "what_the_score_means": explanation.get("note"),
        "top_shap_features": [
            {**{k: f.get(k) for k in ("feature", "value", "contribution", "pushes")},
             "meaning": meaning(str(f.get("feature"))) or "not described in the data"}
            for f in (shap.get("features") or [])[:limits.shap]
        ],
        "shap_note": shap.get("note"),
    }
    if live:
        facts["scoring_window"] = {
            "transactions_in_window": live.get("transactions_in_window"),
            "window_truncated": live.get("window_truncated"),
            "counterparties": live.get("counterparties"),
            "known_illicit_counterparties": (live.get("known_label_counterparties") or {}).get("illicit"),
            "known_licit_counterparty_count": (live.get("known_label_counterparties") or {}).get("licit_count"),
        }
    if explanation.get("dataset"):
        facts["in_training_dataset"] = explanation["dataset"]
    return {k: v for k, v in facts.items() if v not in (None, [], {})}


def build_facts(result: dict, limits: Limits = LOCAL) -> dict:
    """The fact sheet for one investigation result (a dict in the InvestigationResult shape)."""
    graph = result.get("graph") or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    subject_value = (result.get("input") or {}).get("value")
    subject_id = next((n["id"] for n in nodes if _value_of(n.get("id", "")) == subject_value), None)

    # Every chain's graph is drawn sender -> transaction -> receiver; "Expand node" adds direct
    # address -> address links ("paid 0.5 BTC, 2 txs"). Spell out the direction of each link relative to
    # the subject, so the model never has to work out who paid whom from "input"/"output".
    labels = {n.get("id"): n.get("label") or _value_of(n.get("id", "")) for n in nodes}
    types = {n.get("id"): n.get("type") for n in nodes}
    subject_sent = {e.get("target") for e in edges if e.get("source") == subject_id}  # txs the subject paid into
    subject_got = {e.get("source") for e in edges if e.get("target") == subject_id}  # txs that paid the subject
    links: dict[str, list[str]] = {}
    relations: dict[str, set[str]] = {}
    for edge in edges:
        source, target, label = edge.get("source"), edge.get("target"), edge.get("label", "")
        if source and target and "transaction" not in (types.get(source), types.get(target)):
            # A direct link from "Expand node" / the graph explorer: source paid target.
            first, last = str(edge.get("first_time") or "")[:10], str(edge.get("last_time") or "")[:10]
            if first and last:
                label = f"{label}, {first}" if first == last else f"{label}, {first} to {last}"
            if source == subject_id:
                pairs = [(target, "was paid by the subject", f"{label} (direct link added by expanding the graph)")]
            elif target == subject_id:
                pairs = [(source, "paid the subject", f"{label} (direct link added by expanding the graph)")]
            else:
                pairs = [(source, "sent (the subject is not in this link)", f"{label} to {labels.get(target)}"),
                         (target, "received (the subject is not in this link)", f"{label} from {labels.get(source)}")]
            for end, how, text in pairs:
                relations.setdefault(end, set()).add(how)
                links.setdefault(end, []).append(f"{how}: {text}")
            continue
        if source and source != subject_id:  # source paid into the transaction `target`
            if target in subject_got:
                how = "paid the subject"
            elif target in subject_sent:
                how = "paid into the same transaction as the subject"
            else:
                how = "sent (the subject is not in this transaction)"
            relations.setdefault(source, set()).add(how)
            links.setdefault(source, []).append(f"{how}: {label} in {labels.get(target, 'a transaction')}")
        if target and target != subject_id:  # target was paid by the transaction `source`
            if source in subject_sent:
                how = "was paid by the subject"
            elif source in subject_got:
                how = "was paid by the same transaction as the subject"
            else:
                how = "received (the subject is not in this transaction)"
            relations.setdefault(target, set()).add(how)
            links.setdefault(target, []).append(f"{how}: {label} from {labels.get(source, 'a transaction')}")

    def flagged(node: dict) -> bool:
        details = node.get("details") or {}
        return bool(details.get("listed_in") or details.get("red_flags") or details.get("blacklisted_by"))

    addresses = [n for n in nodes if n.get("type") == "address" and n.get("id") != subject_id]
    addresses.sort(key=lambda n: (not flagged(n), -len(links.get(n["id"], []))))
    counterparties = []
    for node in addresses[:limits.counterparties]:
        details = {k: v for k, v in (node.get("details") or {}).items()
                   if k in ("listed_in", "red_flags", "blacklisted_by", "label", "labels", "balance_sats")}
        direct = sorted(relations.get(node["id"], set()) & {"paid the subject", "was paid by the subject"})
        counterparties.append({"address": _value_of(node["id"]),
                               "money_flow_with_subject": " and ".join(direct) if direct else "no direct payment",
                               "links": links.get(node["id"], [])[:limits.links_per_counterparty],
                               "link_count": len(links.get(node["id"], [])), **details})

    transactions = []
    for node in [n for n in nodes if n.get("type") == "transaction"][:limits.transactions]:
        details = node.get("details") or {}
        side = [w for w, txs in (("subject paid into it", subject_sent), ("it paid the subject", subject_got))
                if node["id"] in txs]
        transactions.append({"txid": _value_of(node["id"]),
                             "subject_side": " and ".join(side) or "subject not in it",
                             **{k: v for k, v in details.items() if v is not None and not isinstance(v, (dict, list))}})

    others = [n for n in nodes if n.get("type") not in ("address", "transaction")][:limits.other_entities]
    subject_node = next((n for n in nodes if n.get("id") == subject_id), None)
    findings = result.get("findings") or []
    indicators = result.get("indicators") or []

    facts = {
        "subject": {
            "type": (result.get("input") or {}).get("type"),
            "value": subject_value,
            "details": (subject_node or {}).get("details") or {},
        },
        "mode": result.get("mode"),
        "method": result.get("method"),
        "data_mode": result.get("data_mode"),
        "findings": [{"id": f.get("id"), "kind": f.get("kind"), "title": f.get("title")}
                     for f in findings[:limits.findings]],
        "rules_matched": [{"rule": i.get("rule"), "reason": i.get("reason")}
                          for i in indicators if i.get("matched") is True],
        "rules_clear_count": sum(1 for i in indicators if i.get("matched") is False),
        "rules_not_run_count": sum(1 for i in indicators if i.get("matched") is None),
        "list_matches": {
            "counterparties_on_a_list": sum(1 for n in addresses if (n.get("details") or {}).get("listed_in")
                                            or (n.get("details") or {}).get("blacklisted_by")),
            "counterparties_in_graph": len(addresses),
            "note": "Being on a sanctions or scam list is a lead to check, not proof that an address is illicit.",
        },
        "model_score": _model_score(result.get("explanation"), limits),
        "counterparties": counterparties,
        "transactions": transactions,
        "other_entities": [{"type": n.get("type"), "label": n.get("label"),
                            "details": n.get("details") or {}} for n in others],
        "graph_size": {"nodes": len(nodes), "edges": len(edges), "truncated": graph.get("truncated"),
                       "links_added_by_expand_node": sum(1 for e in edges if str(e.get("id", "")).startswith("expand:"))},
        "cut_from_this_sheet": {
            "findings": max(0, len(findings) - limits.findings),
            "counterparties": max(0, len(addresses) - limits.counterparties),
            "transactions": max(0, sum(1 for n in nodes if n.get("type") == "transaction") - limits.transactions),
        },
        "evidence_sources": sorted({e.get("source", "") for e in (result.get("evidence") or [])})[:20],
        "limitations": (result.get("limitations") or [])[:limits.limitations],
        "uncertainty": result.get("uncertainty") or {},
    }
    if not facts["model_score"]:
        facts["model_score"] = "No model score in this result (not a Bitcoin address, or it is still loading)."
    return facts


MAX_TOOL_MATCHES = 5
MAX_TOOL_LINKS = 12
TOOL_DETAIL_KEYS = ("listed_in", "red_flags", "blacklisted_by", "label", "labels", "balance_sats", "block_height",
                    "block_time", "total_transferred_sats", "fee_sats", "counterparties", "group")


def find_in_case(result: dict, query: str, limit: int = MAX_TOOL_MATCHES) -> list[dict]:
    """Graph nodes whose id or label contains the query, with the edges that touch them (a tool for chat).

    Each link says in words who paid whom, seen from the matched node, and results are kept small so a
    lookup still fits the local model's context.
    """
    query = query.strip().lower()
    if len(query) < 4:
        return []
    graph = result.get("graph") or {}
    nodes = {n.get("id"): n for n in graph.get("nodes") or []}

    def name(node_id: str) -> str:
        node = nodes.get(node_id) or {}
        kind = node.get("type")
        if kind == "transaction":
            return f"transaction {_value_of(node_id)}"
        if kind == "address":
            return _value_of(node_id)
        return f"group '{node.get('label') or node_id}'"

    matches = []
    for node in nodes.values():
        if query not in node.get("id", "").lower() and query not in node.get("label", "").lower():
            continue
        node_id = node["id"]
        touching = [e for e in graph.get("edges") or [] if node_id in (e.get("source"), e.get("target"))]
        links = []
        for e in touching[:MAX_TOOL_LINKS]:
            if e.get("source") == node_id:  # money flowed out of this node
                verb = "paid into" if (nodes.get(e.get("target")) or {}).get("type") == "transaction" else "paid"
                links.append(f"{name(node_id)} {verb} {name(e.get('target'))}: {e.get('label', '')}")
            else:  # money flowed into this node
                links.append(f"{name(node_id)} was paid by {name(e.get('source'))}: {e.get('label', '')}")
        details = {k: v for k, v in (node.get("details") or {}).items() if k in TOOL_DETAIL_KEYS}
        matches.append({"type": node.get("type"), "value": name(node_id), "details": details,
                        "links": links, "link_count": len(touching),
                        "links_not_shown": max(0, len(touching) - MAX_TOOL_LINKS)})
        if len(matches) >= limit:
            break
    return matches
