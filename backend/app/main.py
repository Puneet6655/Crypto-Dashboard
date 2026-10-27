"""Entry point. Run with: uvicorn app.main:app --reload"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import bitcoin, investigations
from app.config import get_settings
from app.db import NullStore, open_store
from app.store import get_store

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Connect to the database when the server starts and disconnect when it stops."""
    app.state.store = await run_in_threadpool(open_store, get_settings().database_url)
    yield
    await run_in_threadpool(app.state.store.close)


app = FastAPI(title="Payments Intelligence API", version="0.4.0", lifespan=lifespan)

# Allow the React dev server (Milestone 2) to call this API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().frontend_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.include_router(bitcoin.router)
app.include_router(investigations.router)


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
