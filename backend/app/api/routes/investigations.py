"""POST /api/investigations: the single endpoint the dashboard calls to run a case.

Each investigation is saved with everything it fetched (address totals and
transactions), after the response is sent. Saved cases can be listed and reopened.
"""
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.db import NullStore, run_safely
from app.intelligence.index import IndicatorIndex, get_indicator_index
from app.providers.base import BitcoinProvider, ProviderError
from app.providers.failover import FailoverEvmProvider
from app.providers.registry import get_base_provider, get_bitcoin_provider, get_ethereum_provider, get_solana_provider
from app.providers.solana_alchemy import SolanaProvider
from app.schemas.investigation import InvestigationRequest, InvestigationResult
from app.services.investigation_service import UnsupportedInvestigation, run_investigation
from app.store import RecordingProvider, get_store
from app.validation import is_bitcoin_address, is_txid, normalize_address

router = APIRouter(prefix="/api/investigations", tags=["investigations"])


def _save_everything(store: NullStore, recorder: RecordingProvider, result: InvestigationResult) -> None:
    for summary in recorder.addresses:
        run_safely(store.save_address, summary)
    run_safely(store.save_transactions, recorder.transactions)
    run_safely(store.save_investigation, result)


@router.post("", response_model=InvestigationResult)
async def create_investigation(
    request: InvestigationRequest,
    background: BackgroundTasks,
    provider: BitcoinProvider = Depends(get_bitcoin_provider),
    settings: Settings = Depends(get_settings),
    store: NullStore = Depends(get_store),
    indicators: IndicatorIndex = Depends(get_indicator_index),
    ethereum: FailoverEvmProvider | None = Depends(get_ethereum_provider),
    base: FailoverEvmProvider | None = Depends(get_base_provider),
    solana: SolanaProvider | None = Depends(get_solana_provider),
) -> InvestigationResult:
    recorder = RecordingProvider(provider)
    try:
        result = await run_investigation(request, recorder, settings.max_graph_nodes, indicators, ethereum, base,
                                         solana)
    except UnsupportedInvestigation as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message, headers=exc.headers())
    background.add_task(_save_everything, store, recorder, result)
    return result


class OlderTransactionsRequest(BaseModel):
    chain: Literal["bitcoin", "ethereum", "base", "solana"] = "bitcoin"
    address: str = Field(min_length=20, max_length=100)
    # Where to carry on, from the result's uncertainty.older_cursor (or the previous answer's next_cursor):
    # Bitcoin: the oldest txid read; Ethereum/Base: the highest block still to read; Solana: the oldest signature read.
    cursor: str | None = Field(default=None, min_length=1, max_length=100)
    after_txid: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")  # older name of the Bitcoin cursor
    pages: int = Field(default=4, ge=1, le=8)  # Bitcoin: 25 confirmed transactions per page
    evidence_id: str = Field(default="eo1", pattern=r"^[A-Za-z0-9_-]{1,20}$")


@router.post("/older-transactions")
async def older_transactions(
    request: OlderTransactionsRequest,
    provider: BitcoinProvider = Depends(get_bitcoin_provider),
    settings: Settings = Depends(get_settings),
    indicators: IndicatorIndex = Depends(get_indicator_index),
    ethereum: FailoverEvmProvider | None = Depends(get_ethereum_provider),
    base: FailoverEvmProvider | None = Depends(get_base_provider),
    solana: SolanaProvider | None = Depends(get_solana_provider),
) -> dict:
    """Older history of a looked-up address, past what the lookup drew ("Load older transactions" on Investigate).
    The answer's graph uses the lookup's shape, so the dashboard merges it into the investigation."""
    from app.services.ethereum_investigation import older_evm_activity
    from app.services.investigation_service import older_bitcoin_transactions
    from app.services.solana_investigation import older_solana_activity
    from app.validation import is_ethereum_address, is_solana_address, is_solana_signature

    cursor = (request.cursor or request.after_txid or "").strip()
    if not cursor:
        raise HTTPException(status_code=422, detail="Say where to carry on (cursor).")
    try:
        if request.chain == "bitcoin":
            if not is_bitcoin_address(request.address) or not is_txid(cursor):
                raise HTTPException(status_code=422, detail="A Bitcoin address and a transaction id are needed.")
            return await older_bitcoin_transactions(provider, request.address, cursor.lower(), request.pages,
                                                    request.evidence_id, settings.max_graph_nodes, indicators)
        if request.chain in ("ethereum", "base"):
            chain_provider = ethereum if request.chain == "ethereum" else base
            if chain_provider is None:
                raise HTTPException(status_code=503, detail=f"{request.chain.title()} lookups are not set up on this server.")
            if not is_ethereum_address(request.address) or not cursor.isdigit():
                raise HTTPException(status_code=422, detail="A 0x address and a block number are needed.")
            return await older_evm_activity(chain_provider, request.chain, request.address, int(cursor),
                                            request.evidence_id, settings.max_graph_nodes, indicators)
        if solana is None:
            raise HTTPException(status_code=503, detail="Solana lookups are not set up on this server.")
        if not is_solana_address(request.address) or not is_solana_signature(cursor):
            raise HTTPException(status_code=422, detail="A Solana address and a transaction signature are needed.")
        return await older_solana_activity(solana, request.address, cursor, request.evidence_id,
                                           settings.max_graph_nodes, indicators)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message, headers=exc.headers())


class ModelScoreRequest(BaseModel):
    address: str = Field(min_length=20, max_length=100)
    family: Literal["xgboost", "random_forest"] = "xgboost"


@router.post("/model-score")
async def model_score(request: ModelScoreRequest, provider: BitcoinProvider = Depends(get_bitcoin_provider)) -> dict:
    """Live model score with its SHAP explanation for one Bitcoin address.

    The dashboard calls this at the same time as the lookup (which it sends with model_score=False), so every
    Bitcoin address shows a score without picking a model method, and the graph does not wait for it.
    The answer has the same shape as an investigation's "explanation".
    """
    from app.ml.live_scoring import NoActivity, is_ready, score_live
    from app.ml.registry import ModelsUnavailable, get_model_registry

    if not is_bitcoin_address(request.address):
        raise HTTPException(status_code=422, detail="That does not look like a mainnet Bitcoin address.")
    registry = get_model_registry()
    if not registry.available:
        raise HTTPException(status_code=503, detail="No trained models on this server.")
    if not is_ready(registry):
        raise HTTPException(status_code=503,
                            detail="The model is still loading after the server started. Try again in a minute.")
    try:
        return await score_live(provider, normalize_address(request.address), request.family, registry)
    except NoActivity as exc:
        raise HTTPException(status_code=422, detail=str(exc) or "The address has no confirmed transactions to score.")
    except ModelsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=f"No live-compatible model for {request.family} ({exc}).")
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message, headers=exc.headers())


@router.get("")
async def list_investigations(
    limit: int = Query(20, ge=1, le=200),
    store: NullStore = Depends(get_store),
) -> list[dict]:
    """Saved investigations, newest first (summary fields only)."""
    return await run_in_threadpool(store.list_investigations, limit)


@router.get("/{investigation_id}", response_model=InvestigationResult)
async def get_investigation(investigation_id: str, store: NullStore = Depends(get_store)) -> InvestigationResult:
    """Reopen a saved investigation exactly as it was returned the first time."""
    result = await run_in_threadpool(store.get_investigation, investigation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No saved investigation with that ID.")
    return InvestigationResult.model_validate(result)
