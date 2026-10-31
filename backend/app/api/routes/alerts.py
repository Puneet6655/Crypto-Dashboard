"""GET /api/alerts: live alerts for watched Ethereum addresses (see app/alerts)."""
from fastapi import APIRouter, Query

from app.alerts.watcher import get_watcher

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

OFF = ("Live alerts are off. They need ALCHEMY_API_KEY and ALCHEMY_ENABLED=true in backend/.env "
       "(and LIVE_ALERTS_ENABLED not set to false).")


@router.get("")
def alerts(after_id: int = Query(0, ge=0, description="Only alerts newer than this id"),
           limit: int = Query(50, ge=1, le=200)) -> dict:
    """Newest first, with the watcher's connection status."""
    watcher = get_watcher()
    if watcher is None:
        return {"status": {"enabled": False, "detail": OFF}, "alerts": []}
    return {"status": watcher.status.as_dict(), "alerts": watcher.recent(after_id, limit)}
