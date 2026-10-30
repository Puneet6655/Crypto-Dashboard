"""BigQuery refresh page (under development). Admins can estimate a load; running it is not built yet."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.auth.accounts import CurrentUser
from app.auth.deps import require_admin, require_viewer
from app.db import NullStore
from app.store import get_store
from app.workspace import audit, bigquery_preview as bq

router = APIRouter(prefix="/api/bigquery", tags=["bigquery"])


class EstimateRequest(BaseModel):
    start_date: date
    end_date: date
    include_inputs_outputs: bool = False


@router.get("/status")
def bigquery_status(user: CurrentUser = Depends(require_viewer)) -> dict:
    key_found = bq.find_key_path() is not None
    return {
        "stage": "under_development",
        "dataset": bq.TABLE,
        "library_installed": bq.library_installed(),
        "key_found": key_found,
        "project_id": bq.project_id() if user.has("admin") else None,
        "destination": None,
        "free_tier_gb_per_month": bq.FREE_TIER_GB_PER_MONTH,
        "max_days": bq.MAX_DAYS,
        "next_steps": [
            "Choose where the loaded transactions will live (Neon, a BigQuery dataset you own, or files in cloud storage).",
            "Add a capped run: dry-run first, then maximum_bytes_billed, as the training scripts already do.",
            "Schedule it (daily or weekly) and show each run's cost and row count on this page.",
        ],
    }


@router.post("/estimate")
async def estimate(body: EstimateRequest, admin: CurrentUser = Depends(require_admin),
                   store: NullStore = Depends(get_store)) -> dict:
    problem = bq.dates_problem(body.start_date, body.end_date)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    try:
        result = await run_in_threadpool(bq.estimate, body.start_date, body.end_date, body.include_inputs_outputs)
    except bq.NotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:  # BigQuery errors carry no secrets, but keep the message short
        raise HTTPException(status_code=502, detail=f"BigQuery did not accept the dry run: {str(exc)[:300]}")
    if store.enabled:
        audit.record(store.pool, admin, "bigquery.estimate", bq.TABLE,
                     {"start": str(body.start_date), "end": str(body.end_date), "gb": result["gb_processed"]})
    return result


@router.post("/run", status_code=501)
def run(_: CurrentUser = Depends(require_admin)) -> dict:
    raise HTTPException(status_code=501, detail="Under development: loading data needs a decided storage location first.")
