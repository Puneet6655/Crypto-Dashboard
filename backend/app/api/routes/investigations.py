"""POST /api/investigations: the single endpoint the dashboard calls to run a case.

Each investigation is saved with everything it fetched (address totals and
transactions), after the response is sent. Saved cases can be listed and reopened.
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.config import Settings, get_settings
from app.db import NullStore, run_safely
from app.intelligence.index import IndicatorIndex, get_indicator_index
from app.providers.base import BitcoinProvider, ProviderError
from app.providers.registry import get_bitcoin_provider
from app.schemas.investigation import InvestigationRequest, InvestigationResult
from app.services.investigation_service import UnsupportedInvestigation, run_investigation
from app.store import RecordingProvider, get_store

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
) -> InvestigationResult:
    recorder = RecordingProvider(provider)
    try:
        result = await run_investigation(request, recorder, settings.max_graph_nodes, indicators)
    except UnsupportedInvestigation as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message, headers=exc.headers())
    background.add_task(_save_everything, store, recorder, result)
    return result


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
