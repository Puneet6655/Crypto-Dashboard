"""HTTP routes for EVM lookups: /api/ethereum (Etherscan, then Alchemy) and /api/base (Alchemy).

Keys come from backend/.env (ETHERSCAN_API_KEY, ALCHEMY_API_KEY); without them the routes say so.
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.providers.base import ProviderError
from app.providers.failover import FailoverEvmProvider
from app.providers.registry import get_base_provider, get_ethereum_provider
from app.schemas.ethereum import EthAddressSummary, EthHoldings, EthNftHoldings, EthTransaction
from app.validation import is_ethereum_address, is_ethereum_tx_hash, normalize_hex

BAD_ADDRESS = "An address is 0x followed by 40 hexadecimal characters."
BAD_HASH = "A transaction hash is 0x followed by 64 hexadecimal characters."
NO_KEY = {
    "Ethereum": "Ethereum lookups are off: add ETHERSCAN_API_KEY to backend/.env and restart the API.",
    "Base": "Base lookups are off: they need ALCHEMY_API_KEY and ALCHEMY_ENABLED=true in backend/.env.",
}
NO_ALCHEMY = "Token and NFT holdings come from Alchemy: set ALCHEMY_API_KEY and ALCHEMY_ENABLED=true in backend/.env."


def _http_error(exc: ProviderError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message, headers=exc.headers())


def make_router(prefix: str, label: str, dependency) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[label.lower()])

    def require(provider: FailoverEvmProvider | None) -> FailoverEvmProvider:
        if provider is None:
            raise HTTPException(status_code=503, detail=NO_KEY[label])
        return provider

    @router.get("/providers")
    async def sources(check: bool = Query(False, description="Also ask each source for its latest block"),
                      provider: FailoverEvmProvider | None = Depends(dependency)) -> dict:
        """Which sources serve this chain, which are cooling down, and recent fallbacks."""
        if provider is None:
            return {"configured": False, "detail": NO_KEY[label], "sources": [], "cache_entries": 0,
                    "recent_fallbacks": []}
        return {"configured": True, **await provider.status(check=check)}

    @router.get("/address/{address}", response_model=EthAddressSummary)
    async def lookup_address(address: str, provider: FailoverEvmProvider | None = Depends(dependency)):
        if not is_ethereum_address(address):
            raise HTTPException(status_code=422, detail=BAD_ADDRESS)
        try:
            return await require(provider).get_address_summary(normalize_hex(address))
        except ProviderError as exc:
            raise _http_error(exc)

    @router.get("/address/{address}/transactions", response_model=list[EthTransaction])
    async def address_transactions(address: str, provider: FailoverEvmProvider | None = Depends(dependency)):
        """The newest normal transactions (up to 1,000, one call)."""
        if not is_ethereum_address(address):
            raise HTTPException(status_code=422, detail=BAD_ADDRESS)
        try:
            return await require(provider).get_address_transactions(normalize_hex(address))
        except ProviderError as exc:
            raise _http_error(exc)

    def alchemy_source(provider: FailoverEvmProvider | None):
        source = next((s for s in (provider.sources if provider else []) if hasattr(s, "get_holdings")), None)
        if source is None:
            raise HTTPException(status_code=503, detail=NO_ALCHEMY)
        return source

    @router.get("/address/{address}/tokens", response_model=EthHoldings)
    async def address_tokens(address: str, provider: FailoverEvmProvider | None = Depends(dependency)):
        """ETH and ERC-20 balances held now, with USD values (Alchemy Token and Prices APIs)."""
        if not is_ethereum_address(address):
            raise HTTPException(status_code=422, detail=BAD_ADDRESS)
        try:
            return await alchemy_source(provider).get_holdings(normalize_hex(address))
        except ProviderError as exc:
            raise _http_error(exc)

    @router.get("/address/{address}/nfts", response_model=EthNftHoldings)
    async def address_nfts(address: str, provider: FailoverEvmProvider | None = Depends(dependency)):
        """The newest 100 NFTs held now, with Alchemy's spam flag and floor prices (Alchemy NFT API)."""
        if not is_ethereum_address(address):
            raise HTTPException(status_code=422, detail=BAD_ADDRESS)
        try:
            return await alchemy_source(provider).get_nfts(normalize_hex(address))
        except ProviderError as exc:
            raise _http_error(exc)

    @router.get("/tx/{tx_hash}", response_model=EthTransaction)
    async def lookup_transaction(tx_hash: str, provider: FailoverEvmProvider | None = Depends(dependency)):
        if not is_ethereum_tx_hash(tx_hash):
            raise HTTPException(status_code=422, detail=BAD_HASH)
        try:
            return await require(provider).get_transaction(normalize_hex(tx_hash))
        except ProviderError as exc:
            raise _http_error(exc)

    return router


router = make_router("/api/ethereum", "Ethereum", get_ethereum_provider)
base_router = make_router("/api/base", "Base", get_base_provider)
