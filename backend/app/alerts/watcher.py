"""Live alerts: a WebSocket to Alchemy that reports every new Ethereum transaction sent by or to a watched address.

Watched addresses are the Ethereum addresses in open cases (first) and the OFAC list of sanctioned
Ethereum-format addresses. Alchemy's alchemy_minedTransactions subscription filters on its side, so only
matching transactions are sent and billed (0.04 CU per byte, roughly 40-80 CU per alert). An unfiltered
stream of every Ethereum transaction would use the free plan's month in under a day, so the watcher never
subscribes without a filter.

Alerts are kept in memory (the newest 200) and served by GET /api/alerts. The key is part of the WebSocket
URL, so the URL is never logged or shown.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

log = logging.getLogger("app.alerts")

MAX_FILTER_ENTRIES = 1000  # Alchemy's limit on addresses in one subscription
MAX_WATCHED = MAX_FILTER_ENTRIES // 2  # each address needs a "from" and a "to" entry
CU_PER_BYTE = 0.04
# A watched address with more alerts than this in an hour (an exchange, a token contract) is paused for a day:
# a busy address can send ~100 alerts a minute, which would use the free plan's month within days.
BUSY_LIMIT_PER_HOUR = 20
BUSY_PAUSE_SECONDS = 86_400
_ADDRESS = re.compile(r"^0x[0-9a-f]{40}$")

# address -> why it is watched, e.g. ["case #3: Ransom wallet", "OFAC sanctioned"]
Watchlist = dict[str, list[str]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _int(value, base=16) -> int | None:
    try:
        return int(value, base) if isinstance(value, str) else None
    except ValueError:
        return None


def limit_watchlist(watchlist: Watchlist) -> Watchlist:
    """Valid addresses only, case addresses before OFAC ones, at most MAX_WATCHED."""
    clean = {a.lower(): r for a, r in watchlist.items() if _ADDRESS.match(a.lower())}
    ordered = sorted(clean.items(), key=lambda item: not any(r.startswith("case") for r in item[1]))
    return dict(ordered[:MAX_WATCHED])


def subscribe_message(watchlist: Watchlist, request_id: int = 1) -> dict:
    entries = []
    for address in watchlist:
        entries += [{"from": address}, {"to": address}]
    return {"jsonrpc": "2.0", "id": request_id, "method": "eth_subscribe",
            "params": ["alchemy_minedTransactions", {"addresses": entries, "includeRemoved": True,
                                                     "hashesOnly": False}]}


@dataclass
class WatcherStatus:
    enabled: bool = True
    connected: bool = False
    watching: int = 0
    watching_cases: int = 0
    watching_ofac: int = 0
    left_out: int = 0
    events: int = 0
    bytes_received: int = 0
    connected_since: datetime | None = None
    last_event_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    reconnects: int = 0
    paused: dict = field(default_factory=dict)  # address -> when it was paused for being too busy

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled, "connected": self.connected, "chain": "ethereum",
            "watching": self.watching, "watching_cases": self.watching_cases, "watching_ofac": self.watching_ofac,
            "left_out": self.left_out, "events": self.events,
            "estimated_cu": round(self.bytes_received * CU_PER_BYTE),
            "connected_since": self.connected_since, "last_event_at": self.last_event_at,
            "last_error": self.last_error, "last_error_at": self.last_error_at, "reconnects": self.reconnects,
            "paused": [{"address": a, "since": t} for a, t in self.paused.items()],
            "busy_limit_per_hour": BUSY_LIMIT_PER_HOUR,
        }


@dataclass
class AlertWatcher:
    ws_url: str = field(repr=False)
    load_watchlist: Callable[[], Awaitable[Watchlist]]
    connect: Callable = None  # websockets.connect by default; tests pass a fake
    refresh_seconds: float = 600.0
    max_alerts: int = 200
    status: WatcherStatus = field(default_factory=WatcherStatus)
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self):
        self.alerts: deque[dict] = deque(maxlen=self.max_alerts)
        self._seen: set[tuple[str, bool]] = set()
        self._next_id = 1
        self.watchlist: Watchlist = {}
        self._hits: dict[str, deque[float]] = {}
        self._paused_at: dict[str, float] = {}
        self._resubscribe = False
        if self.connect is None:
            import websockets

            self.connect = websockets.connect

    # ---- reading ----------------------------------------------------------------------------

    def recent(self, after_id: int = 0, limit: int = 50) -> list[dict]:
        """Newest first."""
        return [a for a in reversed(self.alerts) if a["id"] > after_id][:limit]

    def handle(self, raw: str | bytes) -> dict | None:
        """One message from Alchemy -> a new alert, or None (confirmation, duplicate, unwatched)."""
        self.status.bytes_received += len(raw)
        try:
            message = json.loads(raw)
        except ValueError:
            return None
        if message.get("method") != "eth_subscription":
            return None
        result = (message.get("params") or {}).get("result") or {}
        tx = result.get("transaction") or {}
        tx_hash = (tx.get("hash") or "").lower()
        removed = bool(result.get("removed"))
        if not tx_hash or (tx_hash, removed) in self._seen:
            return None
        sender, receiver = (tx.get("from") or "").lower(), (tx.get("to") or "").lower() or None
        matched = [a for a in (sender, receiver) if a and a in self.watchlist]
        if not matched:
            return None
        self._seen.add((tx_hash, removed))
        if len(self._seen) > 5000:
            self._seen.clear()
        self.status.events += 1
        self.status.last_event_at = _now()
        self._count_hits(matched)
        value = _int(tx.get("value")) or 0
        alert = {
            "id": self._next_id, "at": _now(), "chain": "ethereum", "tx_hash": tx_hash,
            "block_number": _int(tx.get("blockNumber")), "from_address": sender, "to_address": receiver,
            "value_wei": str(value), "value_eth": f"{value / 10**18:.6f}".rstrip("0").rstrip("."),
            "direction": "out" if sender in self.watchlist else "in",
            "watched": [{"address": a, "reasons": self.watchlist[a]} for a in matched],
            "sanctioned": any("OFAC" in r for a in matched for r in self.watchlist[a]),
            "removed": removed,  # True when a re-org dropped a transaction reported earlier
        }
        self._next_id += 1
        self.alerts.append(alert)
        log.info("Live alert: %s %s (%s)", alert["direction"], tx_hash, ", ".join(matched))
        return alert

    def _count_hits(self, addresses: list[str]) -> None:
        now = self.clock()
        for address in addresses:
            hits = self._hits.setdefault(address, deque())
            hits.append(now)
            while hits and hits[0] < now - 3600:
                hits.popleft()
            if len(hits) > BUSY_LIMIT_PER_HOUR and address not in self._paused_at:
                self._paused_at[address] = now
                self.status.paused[address] = _now()
                self._resubscribe = True
                log.warning("Live alerts: %s had over %d alerts in an hour; pausing it for a day", address,
                            BUSY_LIMIT_PER_HOUR)

    def _active(self, watchlist: Watchlist) -> Watchlist:
        """Leave out paused addresses; un-pause them after a day."""
        now = self.clock()
        for address, since in list(self._paused_at.items()):
            if now - since >= BUSY_PAUSE_SECONDS:
                del self._paused_at[address]
                self.status.paused.pop(address, None)
                self._hits.pop(address, None)
        return {a: r for a, r in watchlist.items() if a.lower() not in self._paused_at}

    # ---- the connection loop ----------------------------------------------------------------

    async def _refresh_watchlist(self) -> bool:
        """Reload the watchlist. True when it changed."""
        full = self._active(await self.load_watchlist())
        limited = limit_watchlist(full)
        self.status.left_out = max(0, len(full) - len(limited))
        self.status.watching = len(limited)
        self.status.watching_cases = sum(1 for r in limited.values() if any(x.startswith("case") for x in r))
        self.status.watching_ofac = sum(1 for r in limited.values() if any("OFAC" in x for x in r))
        changed = limited != self.watchlist
        self.watchlist = limited
        return changed

    async def _session(self) -> None:
        """One connection: subscribe, then read until the watchlist changes or the connection drops."""
        async with self.connect(self.ws_url, ping_interval=30, ping_timeout=30, max_size=2**22) as ws:
            await ws.send(json.dumps(subscribe_message(self.watchlist)))
            reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            if "error" in reply or not reply.get("result"):
                raise ConnectionError(f"Alchemy refused the subscription: {str(reply.get('error'))[:200]}")
            self.status.connected, self.status.connected_since = True, _now()
            log.info("Live alerts: watching %d Ethereum addresses", len(self.watchlist))
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self.refresh_seconds)
                    self.handle(raw)
                    if self._resubscribe:
                        self._resubscribe = False
                        await self._refresh_watchlist()
                        return  # resubscribe without the paused address
                except asyncio.TimeoutError:
                    if await self._refresh_watchlist():
                        return  # resubscribe with the new list on a fresh connection

    async def run(self) -> None:
        """Runs until cancelled. Reconnects after 5 s, doubling to 5 min while it keeps failing."""
        delay = 5.0
        while True:
            try:
                await self._refresh_watchlist()
                if not self.watchlist:
                    await asyncio.sleep(self.refresh_seconds)
                    continue
                await self._session()
                delay = 5.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let a network problem stop the API
                self.status.last_error = f"{exc.__class__.__name__}: {str(exc)[:200]}".replace(self.ws_url, "<alchemy>")
                self.status.last_error_at = _now()
                log.warning("Live alerts connection problem (%s); retrying in %.0f s", self.status.last_error, delay)
                self.status.connected = False
                self.status.reconnects += 1
                await asyncio.sleep(delay)
                delay = min(delay * 2, 300.0)
                continue
            self.status.connected = False
            self.status.reconnects += 1


_watcher: AlertWatcher | None = None


def get_watcher() -> AlertWatcher | None:
    return _watcher


def set_watcher(watcher: AlertWatcher | None) -> None:
    global _watcher
    _watcher = watcher
