"""Entry point. Run with: uvicorn app.main:app --reload"""
import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin, auth, bigquery, bitcoin, cases, indicators, investigations, sources, system_status
from app.auth.deps import require_viewer
from app.config import get_settings
from app.db import NullStore, open_store
from app.intelligence.refresher import keep_indicators_fresh
from app.store import get_store

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """On start: connect to the database and keep the OFAC/Ransomwhere lists fresh in the background.
    On stop: disconnect and stop the refresher."""
    settings = get_settings()
    app.state.store = await run_in_threadpool(open_store, settings.database_url)
    refresher = None
    if settings.indicators_auto_refresh:
        refresher = asyncio.create_task(
            keep_indicators_fresh(settings.indicators_dir, settings.indicators_max_age_hours))
    yield
    if refresher is not None:
        refresher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refresher
    await run_in_threadpool(app.state.store.close)


app = FastAPI(title="Payments Intelligence API", version="0.4.0", lifespan=lifespan)

# Allow the React dev server (Milestone 2) to call this API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().frontend_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

# Everything except sign-in and the two health checks needs a signed-in user (see app/auth).
signed_in = [Depends(require_viewer)]
app.include_router(auth.router)
app.include_router(bitcoin.router, dependencies=signed_in)
app.include_router(investigations.router, dependencies=signed_in)
app.include_router(indicators.router, dependencies=signed_in)
app.include_router(system_status.router, dependencies=signed_in)
app.include_router(cases.router)
app.include_router(sources.router)
app.include_router(bigquery.router)
app.include_router(admin.router)


@app.get("/api/health")
def health() -> dict:
    """Says the API process is running. It does not check external providers."""
    return {"status": "ok", "service": "payments-intelligence-api", "version": app.version}


@app.get("/api/db/health")
def db_health(store: NullStore = Depends(get_store)) -> dict:
    """Says whether live data is being saved to the database."""
    if not store.enabled:
        return {"saving": False, "detail": "No database connected. Set DATABASE_URL in backend/.env and restart."}
    try:
        with store.pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"saving": True, "detail": "ok"}
    except Exception as exc:
        return {"saving": False, "detail": f"Database not reachable: {exc.__class__.__name__}"}
