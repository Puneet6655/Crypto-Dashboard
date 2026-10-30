"""Read-only routes for the downloaded indicator lists (OFAC sanctions, Ransomwhere).

Refreshing is done by the server itself once a day, or by hand with
`python -m app.intelligence refresh`; there is deliberately no HTTP route that triggers downloads.
"""
from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings, get_settings
from app.intelligence.index import IndicatorIndex, get_indicator_index

router = APIRouter(prefix="/api/indicators", tags=["indicators"])


@router.get("/status")
def indicator_status(
    index: IndicatorIndex = Depends(get_indicator_index),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    """Each list: loaded or not, address count, when it was downloaded and when the source last changed it."""
    return index.status(settings.indicators_max_age_hours)


@router.get("/check/{value}")
def check_value(value: str, index: IndicatorIndex = Depends(get_indicator_index)) -> dict:
    """Exact match of one address (Bitcoin, Ethereum-format or Solana) against every loaded list."""
    value = value.strip()
    if not 20 <= len(value) <= 100:
        raise HTTPException(status_code=422, detail="That does not look like a blockchain address.")
    hits = index.lookup(value)
    return {
        "value": value,
        "matched": bool(hits),
        "hits": [
            {"source_id": h.source_id, "title": h.title, "label": h.label, "family": h.family,
             "first_seen": h.first_seen, "last_seen": h.last_seen, "listed_at": h.listed_at,
             "snapshot_retrieved_at": h.snapshot_retrieved_at, "list_updated_at": h.list_updated_at}
            for h in hits
        ],
        "checked": [
            {"source_id": source_id, "snapshot": loaded.snapshot_label(), "retrieved_at": loaded.meta.get("retrieved_at")}
            for source_id, loaded in index.lists.items()
        ],
        "note": "An exact match against downloaded snapshots. No match does not mean the address is safe.",
    }
