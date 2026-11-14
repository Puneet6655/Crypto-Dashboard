"""x402 / agentic payments (see app/x402).

GET /api/x402/summary            snapshot counts and dates, which live sources are on, the monitor's status
GET /api/x402/risky-servers      server payment addresses ranked by phishing-linked money and risky services
GET /api/x402/facilitators       known facilitator addresses (x402analysis + x402scan lists)
GET /api/x402/payments           recent x402 payments the monitor has seen, with their rule hits
GET /api/x402/address/{address}  what the snapshot knows about an address, and the address-level rule hits
GET /api/x402/tx/{tx_hash}       one Base transaction decoded live: is it x402, who paid whom, rule hits
"""
from fastapi import APIRouter, HTTPException, Query

from app.intelligence.index import get_indicator_index
from app.x402 import cdp
from app.x402.chain import BaseX402Reader, ChainError
from app.x402.monitor import get_monitor
from app.x402.rules import check_address, check_payment, worst
from app.x402.snapshot import get_snapshot, norm

router = APIRouter(prefix="/api/x402", tags=["x402"])


def _index():
    try:
        return get_indicator_index()
    except Exception:
        return None


@router.get("/summary")
def summary() -> dict:
    snap = get_snapshot()
    monitor = get_monitor()
    creds = cdp.credentials()
    missing = cdp.missing_packages()
    reader = BaseX402Reader.from_env(snap)
    return {
        "snapshot": snap.summary(),
        "sources": {
            "explorer": reader.explorer_label(),
            "alchemy_backup": bool(reader.alchemy_key),
            "cdp": {"configured": bool(creds), "ready": bool(creds) and not missing, "missing_packages": missing,
                    "docs": cdp.DOCS,
                    "detail": ("Every x402 payment on Base is checked once a minute." if creds and not missing else
                               f"Key set, but install: pip install {' '.join(missing)}" if creds else
                               "Add CDP_API_KEY_ID and CDP_API_KEY_SECRET to backend/.env to watch every x402 "
                               "payment on Base (free key from portal.cdp.coinbase.com).")},
        },
        "monitor": monitor.status() if monitor else {"enabled": False,
                                                     "detail": "The x402 monitor is off (X402_MONITOR_ENABLED=false) "
                                                               "or the snapshot is missing."},
    }


@router.get("/risky-servers")
def risky_servers(limit: int = Query(50, ge=1, le=500)) -> dict:
    return {"servers": get_snapshot().risky_servers(limit)}


@router.get("/facilitators")
def facilitators(chain: str | None = Query(None, pattern="^(base|solana)$")) -> dict:
    rows = [f for f in get_snapshot().facilitators.values() if chain is None or f["chain"] == chain]
    return {"facilitators": rows}


@router.get("/payments")
def payments(limit: int = Query(100, ge=1, le=500), flagged_only: bool = False) -> dict:
    monitor = get_monitor()
    if monitor is None:
        return {"payments": [], "monitor": False}
    rows = monitor.recent_payments(500)
    if flagged_only:
        rows = [r for r in rows if r.get("severity") in ("high", "medium")]
    return {"payments": rows[:limit], "monitor": True}


@router.get("/address/{address}")
def address(address: str, chain: str = Query("base", pattern="^(base|solana)$")) -> dict:
    snap = get_snapshot()
    a = norm(address)
    hits = check_address(snap, a, chain, _index())
    return {"address": a, "chain": chain, "roles": snap.role_of(a), "client": snap.client(a),
            "server": snap.server(a), "taint": snap.taint.get(a), "facilitator": snap.facilitator(a),
            "phishing": snap.phishing.get(a), "phishing_funded": snap.phishing_funded.get(a),
            "severity": worst(hits), "hits": [h.as_dict() for h in hits]}


@router.get("/tx/{tx_hash}")
async def transaction(tx_hash: str) -> dict:
    if not (tx_hash.startswith("0x") and len(tx_hash) == 66):
        raise HTTPException(status_code=422, detail="A Base transaction hash is 0x followed by 64 hexadecimal characters.")
    snap = get_snapshot()
    reader = BaseX402Reader.from_env(snap)
    try:
        data = await reader.transaction(tx_hash)
    except ChainError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    index = _index()
    out = []
    for p in data["payments"]:
        hits = check_payment(snap, p, index)
        out.append({**p.as_dict(), "x402": reader.is_x402(p), "severity": worst(hits),
                    "hits": [h.as_dict() for h in hits]})
    fac = snap.facilitator(data["sender"] or "")
    return {"tx": data["tx"], "sender": data["sender"], "facilitator": fac, "status": data.get("status"),
            "x402": any(p["x402"] for p in out), "payments": out, "source": data["source"],
            "source_url": data["source_url"]}
