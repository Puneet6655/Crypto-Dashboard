"""HTTP routes for Bitcoin lookups. The browser calls these; they call the provider."""
from fastapi import APIRouter, Depends, HTTPException

from app.providers.base import BitcoinProvider, ProviderError
from app.providers.registry import get_bitcoin_provider
from app.schemas.bitcoin import AddressSummary, ProviderHealth, TransactionSummary
from app.validation import is_bitcoin_address, is_txid, normalize_address

router = APIRouter(prefix="/api/bitcoin", tags=["bitcoin"])


@router.get("/health", response_model=ProviderHealth)
async def provider_health(provider: BitcoinProvider = Depends(get_bitcoin_provider)) -> ProviderHealth:
    return await provider.health()


@router.get("/address/{address}", response_model=AddressSummary)
async def lookup_address(address: str, provider: BitcoinProvider = Depends(get_bitcoin_provider)) -> AddressSummary:
    if not is_bitcoin_address(address):
        raise HTTPException(status_code=422, detail="That does not look like a mainnet Bitcoin address.")
    try:
        return await provider.get_address_summary(normalize_address(address))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/tx/{txid}", response_model=TransactionSummary)
async def lookup_transaction(txid: str, provider: BitcoinProvider = Depends(get_bitcoin_provider)) -> TransactionSummary:
    if not is_txid(txid):
        raise HTTPException(status_code=422, detail="A transaction ID must be 64 hexadecimal characters.")
    try:
        return await provider.get_transaction(txid.strip().lower())
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)