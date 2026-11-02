"""HTTP routes for Bitcoin lookups. The browser calls these; they call the provider.

Every successful lookup is also saved to the database, after the response is sent.
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.db import NullStore, run_safely
from app.providers.base import BitcoinProvider, ProviderError
from app.providers.failover import FailoverBitcoinProvider
from app.providers.registry import get_bitcoin_provider
from app.schemas.bitcoin import AddressSummary, ProviderHealth, TransactionSummary
from app.store import get_store
from app.validation import is_bitcoin_address, is_txid, normalize_address

router = APIRouter(prefix="/api/bitcoin", tags=["bitcoin"])


@router.get("/health", response_model=ProviderHealth)
async def provider_health(
    background: BackgroundTasks,
    provider: BitcoinProvider = Depends(get_bitcoin_provider),
    store: NullStore = Depends(get_store),
) -> ProviderHealth:
    health = await provider.health()
    background.add_task(run_safely, store.save_health, health)
    return health


@router.get("/providers")
async def data_sources(
    check: bool = Query(False, description="Also ask each source for its tip height (one request each)"),
    provider: BitcoinProvider = Depends(get_bitcoin_provider),
) -> dict:
    """Which Bitcoin data sources are configured, which are cooling down, and recent fallbacks."""
    if isinstance(provider, FailoverBitcoinProvider):
        return await provider.status(check=check)
    # A single provider (tests, or a custom setup): report just that one.
    health = await provider.health() if check else None
    return {
        "sources": [{"provider": provider.name, "priority": 1, "ready": True,
                     "health": health.model_dump(mode="json") if health else None}],
        "cache_entries": 0,
        "recent_fallbacks": [],
    }


@router.get("/address/{address}", response_model=AddressSummary)
async def lookup_address(
    address: str,
    background: BackgroundTasks,
    provider: BitcoinProvider = Depends(get_bitcoin_provider),
    store: NullStore = Depends(get_store),
) -> AddressSummary:
    if not is_bitcoin_address(address):
        raise HTTPException(status_code=422, detail="That does not look like a mainnet Bitcoin address.")
    try:
        summary = await provider.get_address_summary(normalize_address(address))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message, headers=exc.headers())
    background.add_task(run_safely, store.save_address, summary)
    return summary


@router.get("/address/{address}/transactions", response_model=list[TransactionSummary])
async def address_transactions(
    address: str,
    background: BackgroundTasks,
    after_txid: str | None = Query(None, description="Last confirmed txid of the previous page; omit for the newest page"),
    provider: BitcoinProvider = Depends(get_bitcoin_provider),
    store: NullStore = Depends(get_store),
) -> list[TransactionSummary]:
    """One page of an address's transactions, newest first (Milestone 5 paging).

    The first page holds unconfirmed transactions plus the newest confirmed ones (50 on mempool.space,
    25 on blockstream.info); pass the last txid of a page as after_txid for the next 25 confirmed ones.
    An empty list means there are no more."""
    if not is_bitcoin_address(address):
        raise HTTPException(status_code=422, detail="That does not look like a mainnet Bitcoin address.")
    if after_txid is not None and not is_txid(after_txid):
        raise HTTPException(status_code=422, detail="after_txid must be 64 hexadecimal characters.")
    try:
        page = await provider.get_address_transactions(normalize_address(address),
                                                       after_txid.lower() if after_txid else None)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message, headers=exc.headers())
    background.add_task(run_safely, store.save_transactions, page)
    return page


@router.get("/address/{address}/history")
async def address_history(
    address: str,
    limit: int = Query(50, ge=1, le=500),
    store: NullStore = Depends(get_store),
) -> list[dict]:
    """Saved balance snapshots for one address, newest first. Empty if nothing is saved."""
    if not is_bitcoin_address(address):
        raise HTTPException(status_code=422, detail="That does not look like a mainnet Bitcoin address.")
    return await run_in_threadpool(store.address_history, normalize_address(address), limit)


@router.get("/tx/{txid}", response_model=TransactionSummary)
async def lookup_transaction(
    txid: str,
    background: BackgroundTasks,
    provider: BitcoinProvider = Depends(get_bitcoin_provider),
    store: NullStore = Depends(get_store),
) -> TransactionSummary:
    if not is_txid(txid):
        raise HTTPException(status_code=422, detail="A transaction ID must be 64 hexadecimal characters.")
    try:
        tx = await provider.get_transaction(txid.strip().lower())
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message, headers=exc.headers())
    background.add_task(run_safely, store.save_transactions, [tx])
    return tx
