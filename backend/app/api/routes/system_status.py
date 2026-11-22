"""Extra checks for the Status page. Provider and sanction-list status come from their own routes
(/api/bitcoin/providers and /api/indicators/status)."""
import os
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


def _service(service_id: str, name: str, used_for: str, state: str, detail: str, docs: str | None = None,
             tag: str | None = None) -> dict:
    return {"id": service_id, "name": name, "used_for": used_for, "state": state, "detail": detail,
            "docs": docs, "tag": tag}


@router.get("/services")
def data_services(settings: Settings = Depends(get_settings)) -> dict:
    """The newer outside services (x402, exchange labels, extra keys): is each one set up and in use?
    Reads settings and in-memory state only, so it never calls the services or spends any quota.
    Never returns key values."""
    from app.intelligence.cex_labels import get_cex_labels
    from app.x402 import cdp
    from app.x402.monitor import get_monitor
    from app.x402.snapshot import get_snapshot

    rows = []
    monitor = get_monitor()
    mon = monitor.status() if monitor else None

    # Coinbase CDP SQL API: every x402 payment on Base.
    creds, missing = cdp.credentials(), cdp.missing_packages()
    if not creds:
        state, detail = "off", "Not set up: add CDP_API_KEY_ID and CDP_API_KEY_SECRET (free key from portal.cdp.coinbase.com)."
    elif missing:
        state, detail = "warn", f"Key set, but the API needs: pip install {' '.join(missing)}"
    elif mon and mon.get("network"):
        err = mon.get("network_error")
        state = "warn" if err else "up"
        detail = (f"Watching every x402 payment on Base · {mon.get('payments_seen', 0):,} payments seen"
                  + (f" · last error: {err}" if err else ""))
    else:
        state, detail = "up", "Key set. Used for x402 address and payment lookups on Base; the network monitor is off."
    rows.append(_service("cdp", "Coinbase CDP SQL API", "x402 payments on Base", state, detail, cdp.DOCS, "x402"))

    # Helius: Solana x402 payment records (research script, not called by the live site).
    helius = bool((os.getenv("HELIUS_API_KEY") or "").strip())
    rows.append(_service(
        "helius", "Helius", "x402 payments on Solana (test records)",
        "up" if helius else "off",
        ("Key set. Used by the research script training/x402_records_solana.py to pull real Solana x402 payments "
         "for rule testing; the live site does not call it." if helius else
         "Not set up: add HELIUS_API_KEY to pull Solana x402 test records (free plan, 1M credits a month)."),
        "https://www.helius.dev/docs", "x402"))

    # Blockscout PRO API: Ethereum backup after Etherscan.
    rows.append(_service(
        "blockscout", "Blockscout PRO API", "Ethereum backup after Etherscan",
        "up" if settings.blockscout_api_key else "off",
        ("Key set. Answers Ethereum lookups when Etherscan is limited or down (100,000 credits a day)."
         if settings.blockscout_api_key else
         "Not set up: add BLOCKSCOUT_API_KEY (free key from dev.blockscout.com). Ethereum falls back to Alchemy."),
        "https://docs.blockscout.com/devs/pro-api", "Ethereum"))

    # Exchange labels: Spellbook seed lists from GitHub, Dune API as backup.
    labels = get_cex_labels().status()
    count = labels.get("addresses") or 0
    via = labels.get("via")
    detail = (f"{count:,} exchange addresses" + (f" · via {via}" if via else "")
              + (f" · downloaded {str(labels['retrieved_at'])[:10]}" if labels.get("retrieved_at") else "")
              if count else "No exchange labels loaded yet; they download in the background (checked every 6 hours).")
    detail += " · Dune key set" if settings.dune_api_key else " · no DUNE_API_KEY (GitHub lists only)"
    rows.append(_service("dune", "Spellbook / Dune exchange labels", "Names exchange addresses in the Graph explorer",
                         "up" if count else "warn", detail,
                         "https://docs.dune.com/data-catalog/curated/cex-flows/addresses", "Labels"))

    # x402 snapshot: x402analysis + x402scan facilitator list, shipped in backend/data/x402.
    snap = get_snapshot().summary()
    counts = snap.get("counts") or {}
    shown = ", ".join(f"{v:,} {k.replace('_', ' ')}" for k, v in list(counts.items())[:4] if isinstance(v, int))
    rows.append(_service(
        "x402-snapshot", "x402 snapshot (x402analysis + x402scan)", "Known x402 servers, facilitators and phishing tags",
        "up" if snap.get("loaded") else "down",
        (f"Loaded{f' · {shown}' if shown else ''}" + (f" · built {str(snap['built_at'])[:10]}" if snap.get("built_at") else ""))
        if snap.get("loaded") else "Missing: backend/data/x402 not found, so x402 checks are off.",
        "https://github.com/drinkcoffee/x402analysis", "x402"))

    # x402 monitor.
    if mon:
        state = "up" if mon.get("connected") else "warn"
        detail = (f"{mon.get('mode')} via {mon.get('source')} · {mon.get('watching', 0)} servers watched · "
                  f"every {mon.get('round_seconds')} s" + (f" · last error: {mon['last_error']}" if mon.get("last_error") else ""))
    else:
        state, detail = "off", "Off (X402_MONITOR_ENABLED=false, or the snapshot is missing)."
    rows.append(_service("x402-monitor", "x402 monitor", "Live alerts for risky x402 payments", state, detail, None, "x402"))

    return {"services": rows}
