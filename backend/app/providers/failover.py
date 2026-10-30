"""Tries several Bitcoin data sources in order, so one source's rate limit or outage does not stop the dashboard.

How a request is served:

1. Cache first. Recent answers are reused for a short time, and confirmed transactions (which
   never change) for a day, so repeated lookups do not spend the providers' allowance.
2. Then each source in priority order (default: mempool.space, then blockstream.info).
   A source is skipped while it is cooling down after an error, and for methods it does not
   support (blockstream.info has no per-block fee totals).
3. Rate limit (HTTP 429): the source cools down for 30 s, doubling on each repeat up to 10 min,
   and the next source answers instead. Outages and timeouts cool down for 10 s, up to 5 min.
   A cooling source gets no requests at all, so failover never hammers a provider that asked
   us to slow down.
4. "Not found" or "invalid" answers are final: they are returned at once, not retried elsewhere.

Every answer keeps the real source in its provenance (provider and source_url), so saved data
and investigation evidence always say which provider produced them.
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.features.wallet_window import BlockStat
from app.providers.base import BitcoinProvider, ProviderError
from app.schemas.bitcoin import AddressSummary, ProviderHealth, TransactionSummary

log = logging.getLogger("app.providers.failover")

# Errors that mean "this source cannot answer right now, ask another one".
FAILOVER_STATUS = frozenset({403, 429, 500, 502, 503, 504})
NOT_SUPPORTED_STATUS = 501

RATE_LIMIT_COOLDOWN = (30.0, 600.0)  # first cooldown, longest cooldown (seconds)
ERROR_COOLDOWN = (10.0, 300.0)

# How long answers are reused (seconds).
TTL_ADDRESS_SUMMARY = 30.0
TTL_FIRST_PAGE = 30.0  # newest transactions of an address: changes with every new block
TTL_OLDER_PAGE = 600.0  # a page after a known txid: older confirmed history
TTL_CONFIRMED_TX = 86_400.0
TTL_UNCONFIRMED_TX = 15.0
TTL_BLOCK_STATS = 600.0

_MISSING = object()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TTLCache:
    """A small in-memory cache: each entry expires after its own time, oldest entries go first when full."""

    def __init__(self, max_entries: int = 2048, clock: Callable[[], float] = time.monotonic):
        self.max_entries = max_entries
        self.clock = clock
        self._items: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()

    def get(self, key: tuple) -> Any:
        item = self._items.get(key)
        if item is None:
            return _MISSING
        expires_at, value = item
        if expires_at <= self.clock():
            del self._items[key]
            return _MISSING
        self._items.move_to_end(key)
        return value

    def put(self, key: tuple, value: Any, ttl: float) -> None:
        if ttl <= 0:
            return
        self._items[key] = (self.clock() + ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)


@dataclass
class SourceState:
    """What the failover chain remembers about one source."""

    consecutive_failures: int = 0
    cooldown_until: float = 0.0  # time.monotonic() value; 0 = ready
    cooldown_after_rate_limit: bool = False
    requests: int = 0
    failures: int = 0
    answers: int = 0
    last_error: str | None = None
    last_error_status: int | None = None
    last_error_at: datetime | None = None
    last_success_at: datetime | None = None


@dataclass(frozen=True)
class FallbackEvent:
    """One request that a backup source answered (or that no source could answer)."""

    at: datetime
    method: str
    answered_by: str | None
    skipped: tuple[str, ...]


class FailoverBitcoinProvider(BitcoinProvider):
    name = "failover"

    def __init__(self, sources: list[BitcoinProvider], cache: TTLCache | None = None,
                 clock: Callable[[], float] = time.monotonic):
        if not sources:
            raise ValueError("FailoverBitcoinProvider needs at least one source")
        self.sources = list(sources)
        self.clock = clock
        self.cache = cache if cache is not None else TTLCache(clock=clock)
        self.state = {source.name: SourceState() for source in self.sources}
        self.recent_fallbacks: deque[FallbackEvent] = deque(maxlen=20)

    # ---- the BitcoinProvider methods ---------------------------------------------------

    async def get_address_summary(self, address: str) -> AddressSummary:
        return await self._call("get_address_summary", (address,), ("address", address),
                                lambda _: TTL_ADDRESS_SUMMARY)

    async def get_transaction(self, txid: str) -> TransactionSummary:
        return await self._call("get_transaction", (txid,), ("tx", txid),
                                lambda tx: TTL_CONFIRMED_TX if tx.status == "confirmed" else TTL_UNCONFIRMED_TX)

    async def get_address_transactions(self, address: str, after_txid: str | None = None) -> list[TransactionSummary]:
        ttl = TTL_FIRST_PAGE if after_txid is None else TTL_OLDER_PAGE
        page = await self._call("get_address_transactions", (address, after_txid), ("txs", address, after_txid),
                                lambda _: ttl)
        return list(page)

    async def get_block_stats(self, height: int) -> list[BlockStat]:
        page = await self._call("get_block_stats", (height,), ("blocks", height), lambda _: TTL_BLOCK_STATS)
        return list(page)

    async def health(self) -> ProviderHealth:
        """Tip height from the first source that answers. Never raises.

        Health checks do not change cooldowns; they report what a lookup would use right now.
        """
        notes: list[str] = []
        for source in self.sources:
            wait = self._cooldown_left(source)
            if wait > 0:
                notes.append(f"{source.name} cooling down ({wait:.0f} s left)")
                continue
            health = await source.health()
            if health.reachable:
                if notes:
                    health = health.model_copy(update={"detail": f"ok via backup ({'; '.join(notes)})"})
                return health
            notes.append(f"{source.name}: {health.detail}")
        return ProviderHealth(provider=self.name, reachable=False, tip_height=None, checked_at=_utcnow(),
                              detail="No Bitcoin data source answered: " + "; ".join(notes))

    # ---- status for the dashboard --------------------------------------------------------

    async def status(self, check: bool = False) -> dict:
        """Each source's state (ready or cooling down, counts, last error). check=True also pings them."""
        sources = []
        for priority, source in enumerate(self.sources, start=1):
            st = self.state[source.name]
            wait = self._cooldown_left(source)
            entry = {
                "provider": source.name,
                "priority": priority,
                "base_url": getattr(source, "base_url", None),
                "ready": wait <= 0,
                "cooldown_seconds_left": round(wait, 1),
                "consecutive_failures": st.consecutive_failures,
                "requests": st.requests,
                "answers": st.answers,
                "failures": st.failures,
                "last_error": st.last_error,
                "last_error_status": st.last_error_status,
                "last_error_at": st.last_error_at,
                "last_success_at": st.last_success_at,
                "not_supported": sorted(source.unsupported),
                "health": None,
            }
            # Do not ping a source that told us to slow down.
            if check and not (wait > 0 and st.cooldown_after_rate_limit):
                entry["health"] = (await source.health()).model_dump(mode="json")
            sources.append(entry)
        return {
            "sources": sources,
            "cache_entries": len(self.cache),
            "recent_fallbacks": [
                {"at": e.at, "method": e.method, "answered_by": e.answered_by, "skipped": list(e.skipped)}
                for e in reversed(self.recent_fallbacks)
            ],
        }

    # ---- the failover loop ---------------------------------------------------------------

    def _cooldown_left(self, source: BitcoinProvider) -> float:
        return max(0.0, self.state[source.name].cooldown_until - self.clock())

    async def _call(self, method: str, args: tuple, cache_key: tuple, ttl: Callable[[Any], float]) -> Any:
        cached = self.cache.get(cache_key)
        if cached is not _MISSING:
            return cached

        skipped: list[str] = []
        rate_limited = False
        for source in self.sources:
            if method in source.unsupported:
                continue
            st = self.state[source.name]
            wait = self._cooldown_left(source)
            if wait > 0:
                skipped.append(f"{source.name}: cooling down for {wait:.0f} s after: {st.last_error}")
                rate_limited = rate_limited or st.cooldown_after_rate_limit
                continue

            st.requests += 1
            try:
                result = await getattr(source, method)(*args)
            except ProviderError as exc:
                if exc.status_code == NOT_SUPPORTED_STATUS:
                    skipped.append(f"{source.name}: {exc.message}")
                    continue
                if exc.status_code not in FAILOVER_STATUS:
                    # A definite answer such as "not found": the source works, the request does not.
                    self._mark_ok(st)
                    raise
                self._mark_failed(st, exc)
                rate_limited = rate_limited or exc.status_code == 429
                skipped.append(f"{source.name}: {exc.message}")
                log.warning("%s failed for %s (%s); trying the next source", source.name, method, exc.message)
                continue

            self._mark_ok(st)
            st.answers += 1
            if skipped:
                self.recent_fallbacks.append(FallbackEvent(_utcnow(), method, source.name, tuple(skipped)))
                log.info("%s answered %s after: %s", source.name, method, "; ".join(skipped))
            self.cache.put(cache_key, result, ttl(result))
            return result

        self.recent_fallbacks.append(FallbackEvent(_utcnow(), method, None, tuple(skipped)))
        raise self._all_failed(method, skipped, rate_limited)

    def _mark_ok(self, st: SourceState) -> None:
        st.consecutive_failures = 0
        st.cooldown_until = 0.0
        st.cooldown_after_rate_limit = False
        st.last_success_at = _utcnow()

    def _mark_failed(self, st: SourceState, exc: ProviderError) -> None:
        st.failures += 1
        st.consecutive_failures += 1
        first, longest = RATE_LIMIT_COOLDOWN if exc.status_code == 429 else ERROR_COOLDOWN
        seconds = min(first * 2 ** (st.consecutive_failures - 1), longest)
        if exc.retry_after is not None:
            seconds = min(max(seconds, exc.retry_after), longest)
        st.cooldown_until = self.clock() + seconds
        st.cooldown_after_rate_limit = exc.status_code == 429
        st.last_error = exc.message
        st.last_error_status = exc.status_code
        st.last_error_at = _utcnow()

    def _all_failed(self, method: str, skipped: list[str], rate_limited: bool) -> ProviderError:
        candidates = [s for s in self.sources if method not in s.unsupported]
        if not candidates:
            return ProviderError(f"No configured Bitcoin data source supports {method}", 501)
        waits = [self._cooldown_left(s) for s in candidates]
        retry_after = min(waits) if any(w > 0 for w in waits) else None
        detail = "; ".join(skipped) if skipped else "no source was tried"
        message = f"No Bitcoin data source could answer right now ({detail})."
        if retry_after:
            message += f" Try again in {retry_after:.0f} seconds."
        return ProviderError(message, 429 if rate_limited else 503, retry_after=retry_after)
