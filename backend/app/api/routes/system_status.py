"""Extra checks for the Status page. Provider and sanction-list status come from their own routes
(/api/bitcoin/providers and /api/indicators/status)."""
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from psycopg.rows import dict_row

from app.config import Settings, get_settings
from app.db import NullStore
from app.store import get_store
from app.usage import meter

router = APIRouter(prefix="/api/status", tags=["status"])


def _host_summary(database_url: str | None) -> dict:
    """Which service and region the database is on, without the host name or password."""
    host = urlparse(database_url or "").hostname or ""
    if host.endswith(".neon.tech"):
        parts = host.split(".")
        region = parts[1] if len(parts) > 3 else None
        return {"service": "Neon", "region": region, "pooled": "-pooler" in parts[0]}
    return {"service": "Postgres" if host else None, "region": None, "pooled": None}


def _check_database(store: NullStore) -> dict:
    started = time.perf_counter()
    with store.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT current_setting('server_version') AS version, now() AS server_time")
        info = cur.fetchone()
        latency_ms = (time.perf_counter() - started) * 1000
        cur.execute("""SELECT relname AS table, n_live_tup AS rows FROM pg_stat_user_tables
                       WHERE schemaname = 'public' ORDER BY relname""")
        tables = cur.fetchall()
        cur.execute("SELECT max(checked_at) AS last_check FROM provider_health_checks")
        last = cur.fetchone()
    return {
        "connected": True,
        "latency_ms": round(latency_ms, 1),
        "server_version": info["version"],
        "server_time": info["server_time"],
        "tables": tables,
        "last_provider_check": last["last_check"],
        "detail": "ok",
    }


@router.get("/database")
async def database_status(store: NullStore = Depends(get_store), settings: Settings = Depends(get_settings)) -> dict:
    """Is the database connected, how fast does it answer, and roughly how many rows each table holds."""
    summary = _host_summary(settings.database_url)
    if not store.enabled:
        return {**summary, "connected": False,
                "detail": "No database connected. Set DATABASE_URL in backend/.env and restart the API."}
    try:
        return {**summary, **await run_in_threadpool(_check_database, store)}
    except Exception as exc:
        return {**summary, "connected": False, "detail": f"Database not reachable: {exc.__class__.__name__}"}


@router.get("/provider-history")
async def provider_history(limit: int = Query(48, ge=1, le=500), store: NullStore = Depends(get_store)) -> list[dict]:
    """The latest saved provider health checks, newest first (each Status page check adds one)."""
    if not store.enabled:
        return []

    def read() -> list[dict]:
        with store.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""SELECT provider, reachable, tip_height, checked_at, detail
                           FROM provider_health_checks ORDER BY checked_at DESC LIMIT %s""", (limit,))
            return cur.fetchall()

    try:
        return await run_in_threadpool(read)
    except Exception:
        return []


@router.get("/usage")
def api_usage(days: int = Query(14, ge=1, le=60), settings: Settings = Depends(get_settings)) -> dict:
    """How much of each outside source's free plan this API has used: today, this quota period,
    each of the last `days` days, and the kinds of call that used the most. Counted by this API."""
    return meter.summary(days=days, cycle_start_day=settings.alchemy_billing_day)
