"""Live x402 monitoring on Base. Shows in the Live alerts page next to the other chains (as chain key "x402").

Two ways, used together when both are available:

  * network (needs a CDP key, see app/x402/cdp.py): every x402 payment on Base, polled once a minute from the CDP
    SQL API, each one run through the rules.
  * watchlist (always on, free): the riskiest x402 server payment addresses from the snapshot (phishing-linked money,
    high-risk services) plus X402_WATCH_EXTRA, checked through Blockscout (Alchemy as backup) for new USDC payments.

Every x402 payment seen goes into a recent-payments feed. Only payments where a payment-level rule fires
(phishing-linked payer, sanctions, large or odd amount, cash-out purchase) become alerts; server-level context
(taint share, risky category) is attached to the alert but does not raise one by itself.

Settings (backend/.env): X402_MONITOR_ENABLED (default: same as LIVE_ALERTS_ENABLED), X402_WATCH_COUNT (default 10, max 30),
X402_WATCH_EXTRA (comma-separated Base addresses), X402_ALERT_MIN_SEVERITY (medium or high, default medium).
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from datetime import datetime, timedelta, timezone

from app.alerts.watcher import next_alert_id, wait_or_wake
from app.x402.chain import BaseX402Reader
from app.x402.rules import Payment, alert_worthy, check_payment, worst
from app.x402.snapshot import Snapshot, norm

log = logging.getLogger("app.x402")

WATCH_SPACING = 20.0       # seconds between two watchlist calls
WATCH_MIN_ROUND = 300.0    # a full watchlist round takes at least this long
NETWORK_EVERY = 60.0       # seconds between CDP queries
NETWORK_LOOKBACK = timedelta(minutes=10)  # first CDP query looks back this far


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in ("1", "true", "yes", "on")


class X402Monitor:
    def __init__(self, snapshot: Snapshot, reader: BaseX402Reader, cdp=None, index_getter=None,
                 watch_count: int = 10, extra: list[str] | None = None, min_severity: str = "medium",
                 sleep=None, max_alerts: int = 200, max_feed: int = 500):
        self.snap = snapshot
        self.reader = reader
        self.cdp = cdp
        self.index_getter = index_getter or (lambda: None)
        self.min_severity = min_severity if min_severity in ("medium", "high") else "medium"
        self.sleep = sleep
        self.alerts: deque[dict] = deque(maxlen=max_alerts)
        self.feed: deque[dict] = deque(maxlen=max_feed)
        self.watch: dict[str, list[str]] = {}
        mixed = [r for r in snapshot.risky_servers(limit=500) if r["chain"] == "base"]
        for row in mixed:  # half phishing-tainted servers, half busy high-risk (cash-out) servers
            if len(self.watch) >= max(0, min(watch_count, 30)):
                break
            if row["address"] in self.watch:
                continue
            why = []
            if row.get("from_phishing_percent"):
                why.append(f"x402: {row['from_phishing_percent']:.1f}% phishing-linked money")
            if row.get("risk"):
                why.append(f"x402: {row['risk']}-risk services")
            self.watch[row["address"]] = [", ".join(row["servers"][:2])] + why
        for a in extra or []:
            a = norm(a)
            if a.startswith("0x") and len(a) == 42:
                self.watch.setdefault(a, ["Added in X402_WATCH_EXTRA"])
        self._seen: dict[str, set[str]] = {}
        self._seen_payments: deque[str] = deque(maxlen=5000)
        self._seen_set: set[str] = set()
        self._since: datetime | None = None
        self._wake = asyncio.Event()
        self.payments_seen = 0
        self.volume = 0.0
        self.events = 0
        self.last_round_at = None
        self.last_network_at = None
        self.last_event_at = None
        self.last_error = None
        self.last_error_at = None
        self.network_error = None

    @classmethod
    def from_env(cls, snapshot: Snapshot) -> "X402Monitor":
        from app.intelligence.index import get_indicator_index
        from app.x402.cdp import CdpSqlClient

        extra = [a.strip() for a in (os.getenv("X402_WATCH_EXTRA") or "").split(",") if a.strip()]
        return cls(snapshot, BaseX402Reader.from_env(snapshot), CdpSqlClient.from_env(), get_indicator_index,
                   int(os.getenv("X402_WATCH_COUNT") or 10), extra, os.getenv("X402_ALERT_MIN_SEVERITY") or "medium")

    @staticmethod
    def enabled_by_env(default: bool = True) -> bool:
        """X402_MONITOR_ENABLED wins; when it is not set, the monitor follows LIVE_ALERTS_ENABLED (default)."""
        return _env_bool("X402_MONITOR_ENABLED", default)

    # ---- reading (same shape the Live alerts page reads from the other watchers) -------------

    def recent(self, after_id: int = 0, limit: int = 50) -> list[dict]:
        return [a for a in reversed(self.alerts) if a["id"] > after_id][:limit]

    def recent_payments(self, limit: int = 100) -> list[dict]:
        return list(reversed(self.feed))[:limit]

    def round_seconds(self) -> float:
        return max(WATCH_MIN_ROUND, len(self.watch) * WATCH_SPACING)

    def status(self) -> dict:
        mode = "network + watchlist" if self.cdp else "watchlist"
        source = ("Coinbase CDP SQL API + " if self.cdp else "") + self.reader.explorer_label()
        return {
            "enabled": True, "chain": "x402", "source": source, "mode": mode,
            "connected": self.last_round_at is not None and not (self.last_error and self.last_error_at
                                                                and self.last_round_at <= self.last_error_at),
            "watching": len(self.watch), "watching_manual": 0, "max_watched": 30, "left_out": 0,
            "events": self.events, "calls": sum(self.reader.calls.values()) + (self.cdp.queries if self.cdp else 0),
            "estimated_cu": self.reader.calls.get("alchemy", 0) * 40, "cu_per_call": 0,
            "round_seconds": round(self.round_seconds()),
            "last_round_at": self.last_round_at, "last_event_at": self.last_event_at,
            "last_error": self.last_error, "last_error_at": self.last_error_at, "paused": [],
            "network": bool(self.cdp), "network_error": self.network_error, "last_network_at": self.last_network_at,
            "payments_seen": self.payments_seen, "volume_usdc": round(self.volume, 2),
            "watchlist": [{"address": a, "reasons": r} for a, r in self.watch.items()],
        }

    def nudge(self) -> None:
        self._wake.set()

    # ---- handling one payment ---------------------------------------------------------------

    def handle(self, p: Payment, watched: str | None = None, may_alert: bool = True) -> dict | None:
        """Record one x402 payment; returns the alert when one is raised (never when may_alert is False)."""
        key = f"{p.tx}:{p.payer}:{p.pay_to}:{p.amount}"
        if key in self._seen_set:
            return None
        if len(self._seen_payments) == self._seen_payments.maxlen:
            self._seen_set.discard(self._seen_payments[0])
        self._seen_payments.append(key)
        self._seen_set.add(key)
        hits = check_payment(self.snap, p, self.index_getter())
        server = self.snap.server(p.pay_to) or {}
        fac = self.snap.facilitator(p.facilitator or "") or {}
        row = {**p.as_dict(), "server": [s["name"] for s in server.get("servers", [])][:3],
               "facilitator_name": (fac.get("names") or [None])[0], "severity": worst(hits),
               "hits": [h.as_dict() for h in hits], "seen_at": _now()}
        self.feed.append(row)
        self.payments_seen += 1
        self.volume += p.amount or 0
        if not may_alert or not alert_worthy(hits, self.min_severity):
            return None
        reasons = [h.title for h in hits if h.severity in ("high", "medium")]
        watched_addr = watched or p.pay_to
        alert = {
            "id": next_alert_id(), "at": _now(), "chain": "base", "kind": "x402", "tx_hash": p.tx,
            "block_number": p.block, "direction": "in", "amount": f"{p.amount:.6f}".rstrip("0").rstrip(".") if p.amount is not None else None,
            "unit": "USDC", "counterparty": p.payer,
            "watched": [{"address": watched_addr, "reasons": reasons or ["x402 payment"]}],
            "sanctioned": any(h.rule == "sanctions_or_scam_list" for h in hits),
            "removed": False, "failed": False,
            "x402": row,
        }
        self.alerts.append(alert)
        self.events += 1
        self.last_event_at = alert["at"]
        log.info("x402 alert: %s -> %s %s (%s)", p.payer, p.pay_to, p.tx, ", ".join(reasons))
        return alert

    # ---- watchlist --------------------------------------------------------------------------

    async def check_watched(self, address: str) -> int:
        data = await self.reader.address_transfers(address, direction="to", limit=25)
        payments = [p for p in data["payments"] if self.reader.is_x402(p)]
        seen = self._seen.get(address)
        if seen is None:  # first look only records what is there
            self._seen[address] = {p.tx for p in data["payments"]}
            for p in reversed(payments[:10]):  # shown in the feed, never alerted: they happened before we looked
                self.handle(p, address, may_alert=False)
            return 0
        made = 0
        for p in reversed(payments):  # oldest first
            if p.tx in seen:
                continue
            seen.add(p.tx)
            if self.handle(p, address):
                made += 1
        return made

    async def watch_round(self) -> None:
        problems = 0
        for i, address in enumerate(list(self.watch)):
            if i:
                await (self.sleep or asyncio.sleep)(WATCH_SPACING)
            try:
                await self.check_watched(address)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one bad answer must not stop the others
                problems += 1
                self.last_error = f"{exc.__class__.__name__}: {str(exc)[:200]}"
                self.last_error_at = _now()
                log.warning("x402 watchlist: could not check %s (%s)", address, self.last_error)
        self.last_round_at = _now()

    # ---- network (CDP) ----------------------------------------------------------------------

    async def network_round(self) -> int:
        if self.cdp is None:
            return 0
        since = self._since or (_now() - NETWORK_LOOKBACK)
        facilitators = self.snap.facilitator_addresses("base")
        try:
            payments = await self.cdp.x402_payments(facilitators, since)
        except Exception as exc:
            self.network_error = f"{exc.__class__.__name__}: {str(exc)[:200]}"
            log.warning("x402 network (CDP): %s", self.network_error)
            return 0
        self.network_error = None
        self.last_network_at = _now()
        made = 0
        for p in payments:
            if self.handle(p):
                made += 1
            if p.at:
                try:
                    t = datetime.fromisoformat(p.at)
                    if self._since is None or t > self._since:
                        self._since = t
                except ValueError:
                    pass
        if self._since is None:
            self._since = _now() - timedelta(minutes=1)
        return made

    # ---- loop -------------------------------------------------------------------------------

    async def run(self) -> None:
        network = asyncio.create_task(self._network_loop()) if self.cdp else None
        try:
            while True:
                try:
                    await self.watch_round()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_error, self.last_error_at = f"{exc.__class__.__name__}: {str(exc)[:200]}", _now()
                if self.sleep is not None:
                    await self.sleep(self.round_seconds())
                    continue
                await wait_or_wake(self._wake, max(60.0, self.round_seconds() - len(self.watch) * WATCH_SPACING))
        finally:
            if network is not None:
                network.cancel()

    async def _network_loop(self) -> None:
        while True:
            await self.network_round()
            await (self.sleep or asyncio.sleep)(NETWORK_EVERY)


_monitor: X402Monitor | None = None


def get_monitor() -> X402Monitor | None:
    return _monitor


def set_monitor(monitor: X402Monitor | None) -> None:
    global _monitor
    _monitor = monitor
