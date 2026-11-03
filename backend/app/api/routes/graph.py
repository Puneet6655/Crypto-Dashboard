"""Advanced graph mode: POST /api/graph/expand streams graph batches as NDJSON (one JSON object per line).

See app/graph/expand.py for how addresses are fetched, grouped, cached and kept within rate limits.
"""
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.graph.expand import FETCH_CAP, expand_stream, get_expander, normalize
from app.intelligence.index import IndicatorIndex, get_indicator_index
from app.providers.registry import get_base_provider, get_bitcoin_provider, get_ethereum_provider, get_solana_provider

router = APIRouter(prefix="/api/graph", tags=["graph"])

NOT_SET_UP = {
    "ethereum": "Ethereum needs ETHERSCAN_API_KEY (or Alchemy) in backend/.env.",
    "base": "Base needs ALCHEMY_API_KEY and ALCHEMY_ENABLED=true in backend/.env.",
    "solana": "Solana needs ALCHEMY_API_KEY and ALCHEMY_ENABLED=true in backend/.env.",
}


class ExpandRequest(BaseModel):
    chain: Literal["bitcoin", "ethereum", "base", "solana"]
    addresses: list[str] = Field(min_length=1, max_length=100, description="Addresses to expand")
    hops: int = Field(1, ge=1, le=2)
    max_new_nodes: int = Field(5000, ge=1, description="Room left under the browser's node cap")
    per_node_limit: int = Field(40, ge=5, le=500, description="Counterparties shown per node before '+N more'")


def _provider(chain: str, settings: Settings):
    if chain == "bitcoin":
        return get_bitcoin_provider(settings)
    if chain == "ethereum":
        return get_ethereum_provider(settings)
    if chain == "base":
        return get_base_provider(settings)
    return get_solana_provider(settings)


@router.get("/status")
def status(settings: Settings = Depends(get_settings)) -> dict:
    """Graph mode's Alchemy allowance, cache size, and per-chain limits."""
    return {**get_expander(settings.graph_alchemy_daily_cu).status(), "fetch_cap": FETCH_CAP,
            "max_nodes": settings.graph_max_nodes,
            "chains": {chain: _provider(chain, settings) is not None for chain in FETCH_CAP}}


@router.post("/expand")
async def expand(body: ExpandRequest, request: Request, settings: Settings = Depends(get_settings),
                 index: IndicatorIndex = Depends(get_indicator_index)) -> StreamingResponse:
    addresses = []
    for raw in body.addresses:
        address = normalize(body.chain, raw)
        if address is None:
            raise HTTPException(status_code=422, detail=f"{raw!r} is not a valid {body.chain} address.")
        addresses.append(address)
    provider = _provider(body.chain, settings)
    if provider is None:
        raise HTTPException(status_code=503, detail=NOT_SET_UP[body.chain])
    expander = get_expander(settings.graph_alchemy_daily_cu)
    max_new = min(body.max_new_nodes, settings.graph_max_nodes)

    async def lines():
        async for event in expand_stream(expander, provider, body.chain, addresses, body.hops, max_new,
                                         body.per_node_limit, index, request.is_disconnected):
            yield json.dumps(event, default=str, separators=(",", ":")) + "\n"

    # X-Accel-Buffering stops proxies from holding the stream back until it ends.
    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
