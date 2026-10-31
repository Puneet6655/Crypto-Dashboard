"""Builds the live-alert watchlist: Ethereum addresses in open cases, then OFAC sanctioned Ethereum-format addresses."""
from __future__ import annotations

import asyncio
import logging

from app.alerts.watcher import Watchlist
from app.db import NullStore
from app.intelligence.index import get_indicator_index

log = logging.getLogger("app.alerts")


def _case_addresses(store: NullStore) -> list[tuple[str, int, str]]:
    if not getattr(store, "enabled", False):
        return []
    from app.workspace.cases import watched_eth_addresses

    return watched_eth_addresses(store.pool)


def make_loader(get_store, watch_ofac: bool = True):
    """An async function returning the current watchlist. get_store() gives the store opened at startup."""

    async def load() -> Watchlist:
        watchlist: Watchlist = {}
        try:
            for address, case_id, title in await asyncio.to_thread(_case_addresses, get_store()):
                watchlist.setdefault(address, []).append(f"case #{case_id}: {title}"[:120])
        except Exception:  # a database problem must not stop the OFAC alerts
            log.exception("Could not read case addresses for live alerts")
        if watch_ofac:
            loaded = get_indicator_index().loaded("ofac_eth")
            for address in (loaded.records if loaded else {}):
                watchlist.setdefault(address.lower(), []).append("OFAC sanctioned")
        return watchlist

    return load
