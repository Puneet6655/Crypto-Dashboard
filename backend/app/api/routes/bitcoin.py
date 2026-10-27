"""HTTP routes for Bitcoin lookups. The browser calls these; they call the provider.

Every successful lookup is also saved to the database, after the response is sent.
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.db import NullStore, run_safely
from app.providers.base import BitcoinProvider, ProviderError
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
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    background.add_task(run_safely, store.save_address, summary)
    return summary


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
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    background.add_task(run_safely, store.save_transactions, [tx])
    return tx
