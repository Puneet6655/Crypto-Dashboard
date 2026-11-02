"""Live alerts (see app/alerts).

GET /api/alerts                    alerts from every chain, newest first, with each watcher's status
GET /api/alerts/watchlist          the hand-made watchlist (all chains) and each chain's cap
POST /api/alerts/watchlist         add an address (analysts and admins); checked against the chain's format
DELETE /api/alerts/watchlist/{id}  remove one (analysts and admins)
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.alerts import manual
from app.alerts.watcher import all_watchers, get_watcher, nudge
from app.auth.accounts import CurrentUser
from app.auth.deps import require_analyst
from app.db import NullStore
from app.store import get_store
from app.workspace import audit
from app.workspace.database import pool_of

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

OFF = ("Live alerts are off. They need ALCHEMY_API_KEY and ALCHEMY_ENABLED=true in backend/.env "
       "(and LIVE_ALERTS_ENABLED not set to false).")
OFF_BY_CHAIN = {
    "ethereum": OFF,
    "solana": "Solana alerts need ALCHEMY_API_KEY and ALCHEMY_ENABLED=true in backend/.env.",
    "bitcoin": "Bitcoin alerts are off (LIVE_ALERTS_ENABLED is false).",
}


def _status(watcher) -> dict:
    return watcher.status() if callable(watcher.status) else watcher.status.as_dict()


@router.get("")
def alerts(after_id: int = Query(0, ge=0, description="Only alerts newer than this id"),
           limit: int = Query(50, ge=1, le=200)) -> dict:
    """Newest first. `status` is the Ethereum connection (as before); `statuses` has every chain."""
    watchers = all_watchers()
    statuses = {chain: {"enabled": False, "chain": chain, "detail": OFF_BY_CHAIN[chain]} for chain in manual.CHAINS}
    merged: list[dict] = []
    for chain, watcher in watchers.items():
        statuses[chain] = _status(watcher)
        merged += watcher.recent(after_id, limit)
    merged.sort(key=lambda a: a["id"], reverse=True)
    eth = get_watcher()
    status = eth.status.as_dict() if eth is not None else {"enabled": False, "detail": OFF}
    return {"status": status, "statuses": statuses, "alerts": merged[:limit]}


class WatchIn(BaseModel):
    chain: Literal["ethereum", "solana", "bitcoin"]
    address: str = Field(min_length=1, max_length=120)
    label: str | None = Field(None, max_length=100)


@router.get("/watchlist")
async def watchlist(store: NullStore = Depends(get_store)) -> dict:
    pool = pool_of(store)
    rows = await run_in_threadpool(manual.list_all, pool)
    running = all_watchers()
    return {"addresses": rows, "limits": manual.MAX_PER_CHAIN,
            "running": {chain: chain in running for chain in manual.CHAINS}}


@router.post("/watchlist", status_code=201)
async def add_to_watchlist(body: WatchIn, user: CurrentUser = Depends(require_analyst),
                           store: NullStore = Depends(get_store)) -> dict:
    pool = pool_of(store)
    try:
        row = await run_in_threadpool(manual.add, pool, body.chain, body.address, body.label, user.email)
    except manual.WatchlistError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await run_in_threadpool(audit.record, pool, user, "alerts.watch_added", f"{row['chain']} {row['address']}",
                            {"label": row["label"]})
    nudge(row["chain"])
    return row


@router.delete("/watchlist/{item_id}")
async def remove_from_watchlist(item_id: int, user: CurrentUser = Depends(require_analyst),
                                store: NullStore = Depends(get_store)) -> dict:
    pool = pool_of(store)
    row = await run_in_threadpool(manual.remove, pool, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That address is not on the watchlist.")
    await run_in_threadpool(audit.record, pool, user, "alerts.watch_removed", f"{row['chain']} {row['address']}")
    nudge(row["chain"])
    return row
