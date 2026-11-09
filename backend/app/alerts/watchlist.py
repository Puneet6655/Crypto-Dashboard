"""Builds the live-alert watchlists.

Ethereum: addresses in open cases and on the hand-made watchlist, then OFAC sanctioned Ethereum-format addresses.
Solana and Bitcoin: the hand-made watchlist for that chain (see app/alerts/manual.py).
"""
from __future__ import annotations

import asyncio
import logging

from app.alerts import manual
from app.alerts.watcher import Watchlist
from app.db import NullStore
from app.intelligence.index import get_indicator_index

log = logging.getLogger("app.alerts")


def _case_addresses(store: NullStore) -> list[tuple[str, int, str]]:
    if not getattr(store, "enabled", False):
        return []
    from app.workspace.cases import watched_eth_addresses

    return watched_eth_addresses(store.pool)


async def _manual(store: NullStore, chain: str) -> Watchlist:
    """The hand-made watchlist for one chain; empty without a database or if it cannot be read."""
    if not getattr(store, "enabled", False):
        return {}
    try:
        return await asyncio.to_thread(manual.load_chain, store.pool, chain)
    except Exception:
        log.exception("Could not read the %s watchlist for live alerts", chain)
        return {}


def make_chain_loader(get_store, chain: str):
    """An async function returning the hand-made watchlist of one chain (Solana, Bitcoin)."""

    async def load() -> Watchlist:
        return await _manual(get_store(), chain)

    return load


def make_loader(get_store, watch_ofac: bool = True):
    """An async function returning the current watchlist. get_store() gives the store opened at startup."""

    async def load() -> Watchlist:
        watchlist: Watchlist = {}
        try:
            for address, case_id, title in await asyncio.to_thread(_case_addresses, get_store()):
                watchlist.setdefault(address, []).append(f"case #{case_id}: {title}"[:120])
        except Exception:  # a database problem must not stop the OFAC alerts
            log.exception("Could not read case addresses for live alerts")
        for address, reasons in (await _manual(get_store(), "ethereum")).items():
            watchlist.setdefault(address, []).extend(reasons)
        if watch_ofac:
            loaded = get_indicator_index().loaded("ofac_eth")
            for address in (loaded.records if loaded else {}):
                watchlist.setdefault(address.lower(), []).append("OFAC sanctioned")
        return watchlist

    return load
