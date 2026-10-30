"""Keeps the indicator snapshots fresh while the API runs: checks hourly, downloads lists older than a day."""
import asyncio
import logging
from pathlib import Path

from app.intelligence import snapshots
from app.intelligence.index import reload_indicator_index

log = logging.getLogger("app.intelligence")


async def keep_indicators_fresh(folder: Path, max_age_hours: float, check_every_seconds: float = 3600) -> None:
    """Runs until cancelled. A failed download keeps the previous snapshot and is retried next hour."""
    await asyncio.to_thread(reload_indicator_index, folder)
    while True:
        try:
            stale = await asyncio.to_thread(snapshots.stale_sources, folder, max_age_hours)
            if stale:
                results = await asyncio.to_thread(snapshots.refresh, folder, stale)
                for r in results:
                    log.info("Indicator list %s: %s (%s)", r.source_id, r.status, r.detail)
                await asyncio.to_thread(reload_indicator_index, folder)
        except Exception:  # never let a download problem stop the API
            log.exception("Indicator refresh failed; keeping the saved snapshots")
        await asyncio.sleep(check_every_seconds)
