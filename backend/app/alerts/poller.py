"""Live alerts for Solana and Bitcoin: check each watched address for new transactions, round after round.

Solana goes through Alchemy (getSignaturesForAddress, 40 CU a call). Calls are spaced so there is at most one
every 30 seconds, and a round over all addresses takes at least 5 minutes: about 86,000 calls and 3.5 million
CU a month at most, whatever the list size (the free plan has 30 million). Bitcoin goes through mempool.space
(blockstream.info as backup), which costs nothing; calls are spaced 3 seconds apart, rounds take at least 2 minutes.

The first check of an address only records what is already there; later checks alert on what is new.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

from app.alerts.watcher import Watchlist, next_alert_id, wait_or_wake

log = logging.getLogger("app.alerts")

MAX_NEW_PER_CHECK = 10  # a burst on one address gives at most this many alerts per check
SOLANA_CU_PER_CALL = 40


def _now() -> datetime:
    return datetime.now(timezone.utc)


# One transaction for one watched address, as the fetch function gives it:
# {"tx": id, "direction": "in"/"out"/None, "amount": text or None, "counterparty": address or None,
#  "block": number or None, "failed": bool}
Seen = dict


@dataclass
class ChainPolling:
    chain: str
    source: str
    unit: str
    max_watched: int
    min_round_seconds: float
    min_call_spacing: float
    cu_per_call: int = 0  # Alchemy compute units per call (0 when the source is free)


SOLANA = ChainPolling("solana", "Alchemy (getSignaturesForAddress)", "SOL", 20, 300.0, 30.0, SOLANA_CU_PER_CALL)
BITCOIN = ChainPolling("bitcoin", "mempool.space (blockstream.info as backup)", "BTC", 50, 120.0, 3.0)


@dataclass
class PollingWatcher:
    config: ChainPolling
    fetch: Callable[[str], Awaitable[list[Seen]]] = field(repr=False)
    load_watchlist: Callable[[], Awaitable[Watchlist]] = field(repr=False)
    max_alerts: int = 200
    sleep: Callable[[float], Awaitable[None]] | None = None  # tests pass a fast one

    def __post_init__(self):
        self.alerts: deque[dict] = deque(maxlen=self.max_alerts)
        self.watchlist: Watchlist = {}
        self._seen: dict[str, deque[str]] = {}
        self._wake = asyncio.Event()
        self.calls = 0
        self.events = 0
        self.left_out = 0
        self.last_round_at: datetime | None = None
        self.last_event_at: datetime | None = None
        self.last_error: str | None = None
        self.last_error_at: datetime | None = None
        self.failing = False
        self._last_call = float("-inf")

    # ---- reading ----------------------------------------------------------------------------

    def recent(self, after_id: int = 0, limit: int = 50) -> list[dict]:
        return [a for a in reversed(self.alerts) if a["id"] > after_id][:limit]

    def round_seconds(self) -> float:
        return max(self.config.min_round_seconds, len(self.watchlist) * self.config.min_call_spacing)

    def status(self) -> dict:
        c = self.config
        return {
            "enabled": True, "chain": c.chain, "source": c.source, "mode": "polling",
            "connected": bool(self.watchlist) and self.last_round_at is not None and not self.failing,
            "watching": len(self.watchlist), "watching_manual": len(self.watchlist), "max_watched": c.max_watched,
            "left_out": self.left_out, "events": self.events, "calls": self.calls,
            "estimated_cu": self.calls * c.cu_per_call, "cu_per_call": c.cu_per_call,
            "round_seconds": round(self.round_seconds()),
            "last_round_at": self.last_round_at, "last_event_at": self.last_event_at,
            "last_error": self.last_error, "last_error_at": self.last_error_at, "paused": [],
        }

    def nudge(self) -> None:
        self._wake.set()

    # ---- checking ---------------------------------------------------------------------------

    async def check(self, address: str) -> list[dict]:
        """One address: new alerts, oldest first. The first check of an address only records what is there."""
        self._last_call = time.monotonic()
        self.calls += 1
        rows = await self.fetch(address)
        ids = [r["tx"] for r in rows]
        seen = self._seen.get(address)
        if seen is None:
            self._seen[address] = deque(ids[:200], maxlen=200)
            return []
        new = [r for r in rows if r["tx"] not in seen][:MAX_NEW_PER_CHECK]
        made = []
        for row in reversed(new):  # oldest first
            seen.appendleft(row["tx"])
            alert = {
                "id": next_alert_id(), "at": _now(), "chain": self.config.chain, "tx_hash": row["tx"],
                "block_number": row.get("block"), "direction": row.get("direction"),
                "amount": row.get("amount"), "unit": self.config.unit, "counterparty": row.get("counterparty"),
                "watched": [{"address": address, "reasons": self.watchlist.get(address, [])}],
                "sanctioned": any("OFAC" in r for r in self.watchlist.get(address, [])),
                "removed": False, "failed": bool(row.get("failed")),
            }
            self.alerts.append(alert)
            made.append(alert)
            self.events += 1
            self.last_event_at = alert["at"]
            log.info("Live alert (%s): %s %s", self.config.chain, address, row["tx"])
        return made

    async def _refresh(self) -> None:
        full = await self.load_watchlist()
        self.watchlist = dict(list(full.items())[: self.config.max_watched])
        self.left_out = max(0, len(full) - len(self.watchlist))
        for address in list(self._seen):
            if address not in self.watchlist:
                del self._seen[address]

    async def run_round(self) -> None:
        """Check every watched address once, spaced so a round takes round_seconds()."""
        await self._refresh()
        if not self.watchlist:
            return
        spacing = self.round_seconds() / len(self.watchlist)
        problems = 0
        for i, address in enumerate(list(self.watchlist)):
            if i:
                await (self.sleep or asyncio.sleep)(max(spacing, self.config.min_call_spacing))
            if address not in self.watchlist:
                continue
            try:
                await self.check(address)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one bad answer must not stop the others
                problems += 1
                self.last_error = f"{exc.__class__.__name__}: {str(exc)[:200]}"
                self.last_error_at = _now()
                log.warning("Live alerts (%s): could not check %s (%s)", self.config.chain, address, self.last_error)
        self.failing = problems == len(self.watchlist)
        self.last_round_at = _now()

    async def run(self) -> None:
        """Runs until cancelled. A nudge() starts the next round at once (an address was added or removed);
        the spacing between calls still applies within a round."""
        while True:
            try:
                await self.run_round()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error, self.last_error_at = f"{exc.__class__.__name__}: {str(exc)[:200]}", _now()
                log.warning("Live alerts (%s) problem: %s", self.config.chain, self.last_error)
            # One more gap so each address is checked once per round_seconds().
            wait = self.round_seconds() / len(self.watchlist) if self.watchlist else self.config.min_round_seconds
            if self.sleep is not None:
                await self.sleep(wait)
                continue
            await wait_or_wake(self._wake, wait)
            # Woken early by nudge(): still keep the minimum gap between calls (it caps the Alchemy cost).
            gap = self.config.min_call_spacing - (time.monotonic() - self._last_call)
            if gap > 0:
                await asyncio.sleep(gap)


# ---- fetch functions --------------------------------------------------------------------------

def solana_fetch(provider):
    async def fetch(address: str) -> list[Seen]:
        rows = await provider.get_signatures(address, limit=25)
        return [{"tx": r.signature, "block": r.slot, "failed": r.failed, "direction": None, "amount": None,
                 "counterparty": None} for r in rows]

    return fetch


def _btc(sats: int) -> str:
    return f"{sats / 10**8:.8f}".rstrip("0").rstrip(".")


def bitcoin_rows(address: str, transactions) -> list[Seen]:
    """TransactionSummary list -> rows, with the address's net change as the amount."""
    rows = []
    for tx in transactions:
        received = sum(o.value_sats or 0 for o in tx.outputs if o.address == address)
        sent = sum(i.value_sats or 0 for i in tx.inputs if i.address == address)
        out = sent > received
        others = [e.address for e in (tx.outputs if out else tx.inputs) if e.address and e.address != address]
        rows.append({"tx": tx.txid, "block": tx.block_height, "failed": False, "direction": "out" if out else "in",
                     "amount": _btc(abs(received - sent)), "counterparty": others[0] if others else None})
    return rows


def bitcoin_fetch(provider):
    async def fetch(address: str) -> list[Seen]:
        return bitcoin_rows(address, await provider.get_address_transactions(address))

    return fetch
