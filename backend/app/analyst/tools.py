"""Read-only tools the analyst may call while answering a question.

None of them changes anything: they search the current case, look up a Bitcoin address's totals and list
matches, or get the live model score. Each returns a small JSON-able dict, which is also added to the
facts the answer is checked against.
"""
from __future__ import annotations

from app.analyst.context import find_in_case
from app.providers.base import ProviderError
from app.validation import is_bitcoin_address, normalize_address

TOOLS = [
    {"type": "function", "function": {
        "name": "find_in_case",
        "description": ("Search the current investigation's full graph for an address or transaction ID "
                        "(or a part of one, at least 4 characters). Returns its details and every link to it."),
        "parameters": {"type": "object", "required": ["query"],
                       "properties": {"query": {"type": "string", "description": "Address, txid, or part of one"}}},
    }},
    {"type": "function", "function": {
        "name": "look_up_bitcoin_address",
        "description": ("Look up any Bitcoin address live: balance, totals received and sent, transaction count, "
                        "and whether it is on the OFAC, Ransomwhere or other loaded lists."),
        "parameters": {"type": "object", "required": ["address"],
                       "properties": {"address": {"type": "string", "description": "A full Bitcoin address"}}},
    }},
    {"type": "function", "function": {
        "name": "get_model_score",
        "description": ("Get the live XGBoost risk score for any Bitcoin address, with its threshold and the "
                        "features that pushed it up or down. Takes 5 to 30 seconds."),
        "parameters": {"type": "object", "required": ["address"],
                       "properties": {"address": {"type": "string", "description": "A full Bitcoin address"}}},
    }},
]


async def run_tool(name: str, arguments: dict, result: dict, provider, indicators) -> dict:
    """Run one tool call. Errors come back as {"error": ...} so the model can say what went wrong."""
    try:
        if name == "find_in_case":
            matches = find_in_case(result, str(arguments.get("query", "")))
            return {"matches": matches} if matches else {"matches": [], "note": "Not in this investigation's graph."}
        address = str(arguments.get("address", "")).strip()
        if name in ("look_up_bitcoin_address", "get_model_score") and not is_bitcoin_address(address):
            return {"error": f"{address!r} is not a full mainnet Bitcoin address."}
        address = normalize_address(address)
        if name == "look_up_bitcoin_address":
            summary = await provider.get_address_summary(address)
            hits = indicators.lookup(address) if indicators is not None else []
            return {
                "address": address,
                "balance_sats": summary.balance_sats,
                "total_received_sats": summary.confirmed.funded_sats + summary.unconfirmed.funded_sats,
                "total_sent_sats": summary.confirmed.spent_sats + summary.unconfirmed.spent_sats,
                "confirmed_tx_count": summary.confirmed.tx_count,
                "unconfirmed_tx_count": summary.unconfirmed.tx_count,
                "listed_in": [f"{h.title}" + (f" ({h.family})" if h.family else "") for h in hits],
                "source": summary.provenance.provider,
            }
        if name == "get_model_score":
            from app.ml.live_scoring import lookup_score

            score, skipped = await lookup_score(provider, address)
            if score is None:
                return {"error": skipped}
            return {
                "address": address,
                "model": score["model"].get("name"),
                "score": score["score"],
                "threshold": score["threshold"],
                "flagged": score["flagged"],
                "top_features": [{k: f.get(k) for k in ("feature", "value", "pushes")}
                                 for f in score["explanation"]["features"][:5]],
                "note": score.get("note"),
            }
        return {"error": f"There is no tool called {name}."}
    except ProviderError as exc:
        return {"error": f"The data provider failed: {exc.message}"}
    except Exception as exc:  # noqa: BLE001 - a tool failure must not end the chat
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
