"""Counts the calls this API makes to each outside data source, so the Status and Sources pages can
show how much of each free plan is used.

None of the free plans tell us our usage through their API, so the dashboard counts its own calls:
every HTTP request (and every Alchemy WebSocket message) adds one row per day, source and kind of
call. Alchemy is counted in compute units (CU), the unit its monthly quota uses; the others in calls.

Counts live in memory and, when a database is connected, are added to the api_usage table every
minute (and on shutdown), so they survive restarts. Calls made with the same key by anything else
(another copy of the API, scripts, the Etherscan MCP) are not counted here; each source's own
dashboard has the official figure.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger("app.usage")

# Free plan limits (alchemy.com/pricing and etherscan.io/apis checked 2026-10-31, docs.blockscout.com 2026-11-14).
SOURCES = {
    "alchemy": {
        "label": "Alchemy", "unit": "CU", "period": "month", "quota": 30_000_000,
        "rate_limit": "300 CU per second (the API keeps under 250)",
        "used_for": "Ethereum backup, Base, Solana, token and NFT holdings, live alerts",
        "dashboard_url": "https://dashboard.alchemy.com/",
        "note": "Compute units are estimated from Alchemy's published cost per method. WebSocket alerts are "
                "billed by size (about 0.04 CU per byte). Alchemy's month starts on the plan's billing day.",
    },
    "etherscan": {
        "label": "Etherscan", "unit": "calls", "period": "day", "quota": 100_000,
        "rate_limit": "3 calls per second",
        "used_for": "Ethereum lookups (primary)",
        "dashboard_url": "https://etherscan.io/myapikey",
        "note": "The daily count resets at 00:00 UTC. At the cap Etherscan blocks the key until the next day.",
    },
    "blockscout": {
        "label": "Blockscout", "unit": "credits", "period": "day", "quota": 100_000,
        "rate_limit": "5 requests per second",
        "used_for": "Ethereum lookups (backup after Etherscan)",
        "dashboard_url": "https://dev.blockscout.com",
        "note": "Most calls cost 20 credits (about 5,000 calls a day); the count resets at 00:00 UTC. "
                "The free plan has no Base, Polygon or zkSync since 1 Oct 2026.",
    },
    "mempool.space": {
        "label": "mempool.space", "unit": "calls", "period": "day", "quota": None,
        "rate_limit": "Not published; heavy use gets HTTP 429 for a while",
        "used_for": "Bitcoin lookups (primary) and Bitcoin live alerts",
        "dashboard_url": None,
        "note": "Free public API with no key, so there is no quota to compare against.",
    },
    "blockstream.info": {
        "label": "blockstream.info", "unit": "calls", "period": "day", "quota": None,
        "rate_limit": "Not published; heavy use gets HTTP 429 for a while",
        "used_for": "Bitcoin lookups (backup)",
        "dashboard_url": None,
        "note": "Free public API with no key, used only when mempool.space is limited or down.",
    },
}

DAYS_KEPT = 62  # enough for this month and last month
FLUSH_SECONDS = 60


def _today() -> date:
    return datetime.now(timezone.utc).date()


@dataclass
class _Count:
    calls: int = 0
    units: float = 0.0
    errors: int = 0

    def add(self, calls: int, units: float, errors: int) -> None:
        self.calls += calls
        self.units += units
        self.errors += errors


class UsageMeter:
    def __init__(self, today=_today):
        self._today = today
        self._lock = threading.Lock()
        self._totals: dict[tuple[date, str, str], _Count] = defaultdict(_Count)
        self._pending: dict[tuple[date, str, str], _Count] = defaultdict(_Count)  # not yet in the database
        self.started_at = datetime.now(timezone.utc)
        self.saved_from: date | None = None  # oldest day loaded from the database, if any
        self._store = None

    # ---- counting -----------------------------------------------------------------------------

    def record(self, source: str, kind: str, units: float = 1.0, calls: int = 1, ok: bool = True) -> None:
        """One call (or one WebSocket message) to a source. units = CU for Alchemy, 1 for the others."""
        key = (self._today(), source, kind[:80])
        errors = 0 if ok else 1
        with self._lock:
            self._totals[key].add(calls, units, errors)
            self._pending[key].add(calls, units, errors)

    # ---- the database -------------------------------------------------------------------------

    def attach(self, store) -> None:
        """Load saved counts (so a restart keeps this month's totals) and save new ones from now on."""
        if not getattr(store, "enabled", False):
            return
        since = self._today() - timedelta(days=DAYS_KEPT)
        try:
            with store.pool.connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT day, source, kind, calls, units, errors FROM api_usage WHERE day >= %s", (since,))
                rows = cur.fetchall()
        except Exception as exc:
            log.warning("Could not read saved API usage: %s", exc.__class__.__name__)
            return
        with self._lock:
            for day, source, kind, calls, units, errors in rows:
                self._totals[(day, source, kind)].add(calls, float(units), errors)
            self.saved_from = min((r[0] for r in rows), default=None)
            self._store = store

    def detach(self) -> None:
        self._store = None

    def flush(self) -> None:
        """Add the counts made since the last flush to the api_usage table. Keeps them if it fails."""
        store = self._store
        if store is None:
            return
        with self._lock:
            pending, self._pending = self._pending, defaultdict(_Count)
        if not pending:
            return
        rows = [(d, s, k, c.calls, c.units, c.errors) for (d, s, k), c in pending.items()]
        try:
            with store.pool.connection() as conn, conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO api_usage (day, source, kind, calls, units, errors) VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (day, source, kind) DO UPDATE SET
                           calls = api_usage.calls + EXCLUDED.calls,
                           units = api_usage.units + EXCLUDED.units,
                           errors = api_usage.errors + EXCLUDED.errors""", rows)
                cur.execute("DELETE FROM api_usage WHERE day < %s", (self._today() - timedelta(days=400),))
        except Exception as exc:
            log.warning("Could not save API usage: %s", exc.__class__.__name__)
            with self._lock:
                for key, c in pending.items():
                    self._pending[key].add(c.calls, c.units, c.errors)

    # ---- reading ------------------------------------------------------------------------------

    def summary(self, days: int = 14, cycle_start_day: int = 1) -> dict:
        """Usage per source: today, this quota period, the last `days` days and the busiest kinds of call."""
        today = self._today()
        month_start = _cycle_start(today, cycle_start_day)
        with self._lock:
            items = [(d, s, k, _Count(c.calls, c.units, c.errors)) for (d, s, k), c in self._totals.items()]
        sources = []
        for source, info in SOURCES.items():
            rows = [(d, k, c) for d, s, k, c in items if s == source]
            period_start = month_start if info["period"] == "month" else today
            period = _sum(c for d, _, c in rows if d >= period_start)
            today_total = _sum(c for d, _, c in rows if d == today)
            month_total = _sum(c for d, _, c in rows if d >= month_start)
            used = period.units
            quota = info["quota"]
            by_kind: dict[str, _Count] = defaultdict(_Count)
            for d, kind, c in rows:
                if d >= month_start:
                    by_kind[kind].add(c.calls, c.units, c.errors)
            daily = []
            for back in range(days - 1, -1, -1):
                day = today - timedelta(days=back)
                c = _sum(c for d, _, c in rows if d == day)
                daily.append({"day": day.isoformat(), "calls": c.calls, "units": round(c.units)})
            sources.append({
                "id": source, **info,
                "period_start": period_start.isoformat(),
                "used": round(used),
                "percent": round(used / quota * 100, 2) if quota else None,
                "today": _out(today_total), "this_month": _out(month_total),
                "by_kind": [{"kind": k, **_out(c)} for k, c in
                            sorted(by_kind.items(), key=lambda kv: (-kv[1].units, kv[0]))][:12],
                "daily": daily,
            })
        return {
            "counting_since": (self.saved_from.isoformat() if self.saved_from else self.started_at.isoformat()),
            "saved": self._store is not None,
            "month_start": month_start.isoformat(),
            "sources": sources,
        }

    def reset(self) -> None:
        """Tests only."""
        with self._lock:
            self._totals.clear()
            self._pending.clear()
        self.saved_from = None
        self._store = None


def _cycle_start(today: date, day_of_month: int) -> date:
    """The first day of the current monthly period, for a plan whose month starts on day_of_month."""
    day_of_month = max(1, min(28, day_of_month))
    if today.day >= day_of_month:
        return today.replace(day=day_of_month)
    previous = (today.replace(day=1) - timedelta(days=1))
    return previous.replace(day=day_of_month)


def _sum(counts) -> _Count:
    total = _Count()
    for c in counts:
        total.add(c.calls, c.units, c.errors)
    return total


def _out(c: _Count) -> dict:
    return {"calls": c.calls, "units": round(c.units), "errors": c.errors}


def path_kind(path: str) -> str:
    """'/address/bc1q.../txs/chain' -> 'address/txs/chain': the route without IDs, for grouping calls."""
    parts = [p for p in path.split("?")[0].split("/") if p]
    return "/".join(p for p in parts if len(p) < 20 and not p.isdigit()) or "/"


meter = UsageMeter()


async def keep_saving(seconds: float = FLUSH_SECONDS) -> None:
    """Background task: save the counts every minute."""
    import asyncio

    from fastapi.concurrency import run_in_threadpool

    while True:
        await asyncio.sleep(seconds)
        await run_in_threadpool(meter.flush)
