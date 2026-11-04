"""Entry point. Run with: uvicorn app.main:app --reload"""
import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from app.alerts.poller import BITCOIN, SOLANA, PollingWatcher, bitcoin_fetch, solana_fetch
from app.alerts.watcher import AlertWatcher, set_chain_watcher, set_watcher
from app.alerts.watchlist import make_chain_loader, make_loader
from app.api.routes import admin, alerts, auth, bigquery, bitcoin, cases, ethereum, graph, indicators, investigations, models, solana, sources, system_status
from app.auth.deps import require_viewer
from app.config import get_settings
from app.db import NullStore, open_store
from app.intelligence.refresher import keep_indicators_fresh
from app.store import get_store
from app.usage import keep_saving, meter

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """On start: connect to the database and keep the OFAC/Ransomwhere lists fresh in the background.
    On stop: disconnect and stop the refresher."""
    settings = get_settings()
    app.state.store = await run_in_threadpool(open_store, settings.database_url)
    await run_in_threadpool(meter.attach, app.state.store)
    usage_saver = asyncio.create_task(keep_saving())
    refresher = None
    if settings.indicators_auto_refresh:
        refresher = asyncio.create_task(
            keep_indicators_fresh(settings.indicators_dir, settings.indicators_max_age_hours))
    watcher_task = None
    if settings.alchemy_enabled and settings.alchemy_api_key and settings.live_alerts_enabled:
        watcher = AlertWatcher(f"wss://eth-mainnet.g.alchemy.com/v2/{settings.alchemy_api_key}",
                               make_loader(lambda: app.state.store, settings.live_alerts_watch_ofac))
        set_watcher(watcher)
        watcher_task = asyncio.create_task(watcher.run())
    # Solana (Alchemy) and Bitcoin (mempool.space) watch the hand-made watchlist by polling (app/alerts/poller.py).
    pollers = {}
    if settings.live_alerts_enabled:
        from app.providers.registry import shared_bitcoin_provider, shared_solana_provider

        btc = shared_bitcoin_provider(settings.btc_providers, settings.btc_provider_base_url,
                                      settings.btc_esplora_base_url, settings.btc_network,
                                      settings.http_timeout_seconds)
        pollers["bitcoin"] = PollingWatcher(BITCOIN, bitcoin_fetch(btc), make_chain_loader(lambda: app.state.store, "bitcoin"))
        if settings.alchemy_enabled and settings.alchemy_api_key:
            sol = shared_solana_provider(settings.alchemy_api_key, settings.http_timeout_seconds)
            pollers["solana"] = PollingWatcher(SOLANA, solana_fetch(sol), make_chain_loader(lambda: app.state.store, "solana"))
    poller_tasks = []
    for chain, poller in pollers.items():
        set_chain_watcher(chain, poller)
        poller_tasks.append(asyncio.create_task(poller.run()))
    yield
    for task in poller_tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    for chain in pollers:
        set_chain_watcher(chain, None)
    if watcher_task is not None:
        watcher_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher_task
        set_watcher(None)
    if refresher is not None:
        refresher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refresher
    usage_saver.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await usage_saver
    await run_in_threadpool(meter.flush)
    meter.detach()
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
app.include_router(ethereum.router, dependencies=signed_in)
app.include_router(ethereum.base_router, dependencies=signed_in)
app.include_router(investigations.router, dependencies=signed_in)
app.include_router(indicators.router, dependencies=signed_in)
app.include_router(system_status.router, dependencies=signed_in)
app.include_router(alerts.router, dependencies=signed_in)
app.include_router(solana.router, dependencies=signed_in)
app.include_router(models.router, dependencies=signed_in)
app.include_router(graph.router, dependencies=signed_in)
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
