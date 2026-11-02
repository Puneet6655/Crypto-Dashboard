"""Model registry, comparison and scoring (Milestone 12). Every answer is an offline benchmark.

GET  /api/models             registry: models, versions, features, thresholds, benchmark metrics
GET  /api/models/examples    dataset addresses to try
POST /api/models/score       score one dataset address with one model, with its SHAP explanation
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.ml.registry import OFFLINE_NOTE, ModelRegistry, ModelsUnavailable, get_model_registry
from app.ml.scoring import NotInDataset, score

router = APIRouter(prefix="/api/models", tags=["models"])


class ScoreRequest(BaseModel):
    address: str = Field(min_length=20, max_length=100)
    model_id: str | None = Field(default=None, max_length=80)
    step: int | None = Field(default=None, ge=1, le=101)


def _unavailable(exc: ModelsUnavailable) -> HTTPException:
    return HTTPException(status_code=503, detail=str(exc))


@router.get("")
async def list_models(registry: ModelRegistry = Depends(get_model_registry)) -> dict:
    try:
        data = registry.data
    except ModelsUnavailable as exc:
        return {"available": False, "detail": str(exc), "note": OFFLINE_NOTE, "models": []}
    return {
        "available": True,
        "note": OFFLINE_NOTE,
        "generated_utc": data["generated_utc"],
        "benchmark": data["benchmark"],
        "positive_class": data["positive_class"],
        "test_rows": data["test_rows"],
        "test_illicit": data["test_illicit"],
        "models": registry.comparison(),
        "alert_threshold": {k: v for k, v in (data.get("alert_threshold") or {}).items() if k != "features"},
        "scoring_rows": data.get("scoring_rows"),
    }


@router.get("/examples")
async def examples(registry: ModelRegistry = Depends(get_model_registry)) -> list[dict]:
    try:
        return await run_in_threadpool(registry.examples)
    except ModelsUnavailable as exc:
        raise _unavailable(exc)


@router.post("/score")
async def score_address(request: ScoreRequest, registry: ModelRegistry = Depends(get_model_registry)) -> dict:
    try:
        return await run_in_threadpool(score, request.address, request.model_id, request.step, "xgboost", registry)
    except ModelsUnavailable as exc:
        raise _unavailable(exc)
    except KeyError:
        raise HTTPException(status_code=404, detail="No model with that id in the registry.")
    except NotInDataset as exc:
        raise HTTPException(status_code=404, detail=str(exc))
