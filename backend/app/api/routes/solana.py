"""HTTP routes for Solana lookups through Alchemy: /api/solana. Off unless ALCHEMY_ENABLED=true."""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.providers.base import ProviderError
from app.providers.registry import get_solana_provider
from app.providers.solana_alchemy import SolanaProvider
from app.schemas.solana import SolAddressSummary, SolHoldings, SolSignature, SolTransaction
from app.validation import is_solana_address, is_solana_signature

router = APIRouter(prefix="/api/solana", tags=["solana"])
NO_KEY = "Solana lookups are off: they need ALCHEMY_API_KEY and ALCHEMY_ENABLED=true in backend/.env."
BAD_ADDRESS = "A Solana address is 32 to 44 base58 characters."
BAD_SIGNATURE = "A Solana transaction signature is about 88 base58 characters."


def _require(provider: SolanaProvider | None) -> SolanaProvider:
    if provider is None:
        raise HTTPException(status_code=503, detail=NO_KEY)
    return provider


async def _answer(call):
    try:
        return await call
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message, headers=exc.headers())


@router.get("/providers")
async def sources(check: bool = Query(False), provider: SolanaProvider | None = Depends(get_solana_provider)) -> dict:
    if provider is None:
        return {"configured": False, "detail": NO_KEY, "sources": []}
    # Same shape as the Bitcoin and EVM source lists, so the Status page draws them the same way.
    entry = {"provider": provider.name, "name": provider.name, "priority": 1, "ready": True,
             "requests": provider.requests, "answers": provider.answers,
             "failures": provider.failures, "last_error": provider.last_error, "health": None}
    if check:
        entry["health"] = (await provider.health()).model_dump(mode="json")
    return {"configured": True, "sources": [entry]}


@router.get("/address/{address}", response_model=SolAddressSummary)
async def lookup_address(address: str, provider: SolanaProvider | None = Depends(get_solana_provider)):
    if not is_solana_address(address):
        raise HTTPException(status_code=422, detail=BAD_ADDRESS)
    return await _answer(_require(provider).get_address_summary(address.strip()))


@router.get("/address/{address}/signatures", response_model=list[SolSignature])
async def address_signatures(address: str, provider: SolanaProvider | None = Depends(get_solana_provider)):
    """The newest transactions (up to 1,000, one call)."""
    if not is_solana_address(address):
        raise HTTPException(status_code=422, detail=BAD_ADDRESS)
    return await _answer(_require(provider).get_signatures(address.strip()))


@router.get("/address/{address}/tokens", response_model=SolHoldings)
async def address_tokens(address: str, provider: SolanaProvider | None = Depends(get_solana_provider)):
    """SOL balance and SPL token balances held now."""
    if not is_solana_address(address):
        raise HTTPException(status_code=422, detail=BAD_ADDRESS)
    return await _answer(_require(provider).get_holdings(address.strip()))


@router.get("/tx/{signature}", response_model=SolTransaction)
async def lookup_transaction(signature: str, provider: SolanaProvider | None = Depends(get_solana_provider)):
    if not is_solana_signature(signature):
        raise HTTPException(status_code=422, detail=BAD_SIGNATURE)
    return await _answer(_require(provider).get_transaction(signature.strip()))
