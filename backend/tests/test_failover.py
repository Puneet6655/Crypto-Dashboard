"""Offline tests for the failover chain: rate limits, cooldowns, final answers, caching and provenance."""
import asyncio
from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.providers.base import BitcoinProvider, ProviderError
from app.providers.bitcoin_esplora import BlockstreamEsploraProvider
from app.providers.bitcoin_mempool import MempoolSpaceProvider
from app.providers.failover import FailoverBitcoinProvider
from app.providers.registry import get_bitcoin_provider
from app.schemas.bitcoin import ActivityTotals, AddressSummary, ProviderHealth, Provenance
from tests.fixtures.mempool_synthetic import ADDRESS_RESPONSE, SYNTHETIC_ADDRESS, SYNTHETIC_TXID, TX_RESPONSE


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeSource(BitcoinProvider):
    """Answers address summaries, or raises the error it is told to."""

    def __init__(self, name: str, error: ProviderError | None = None):
        self.name = name
        self.error = error
        self.calls: list[str] = []

    async def get_address_summary(self, address):
        self.calls.append(address)
        if self.error:
            raise self.error
        zero = ActivityTotals(tx_count=0, funded_sats=0, spent_sats=0, balance_sats=0)
        return AddressSummary(
            entity_id=f"bitcoin:mainnet:address:{address}", network="mainnet", address=address,
            confirmed=zero, unconfirmed=zero,
            provenance=Provenance(provider=self.name, source_url=f"https://{self.name}/address/{address}",
                                  retrieved_at=datetime.now(timezone.utc), data_mode="live"),
        )

    async def get_transaction(self, txid):
        raise NotImplementedError

    async def get_address_transactions(self, address, after_txid=None):
        raise NotImplementedError

    async def health(self):
        return ProviderHealth(provider=self.name, reachable=self.error is None, tip_height=1 if not self.error else None,
                              checked_at=datetime.now(timezone.utc), detail="ok" if not self.error else self.error.message)


def run(coro):
    return asyncio.run(coro)


def test_rate_limited_primary_falls_back_and_cools_down():
    clock = Clock()
    primary = FakeSource("primary", ProviderError("primary rate limit reached", 429))
    backup = FakeSource("backup")
    chain = FailoverBitcoinProvider([primary, backup], clock=clock)

    first = run(chain.get_address_summary("a1"))
    assert first.provenance.provider == "backup"
    assert primary.calls == ["a1"]

    # While cooling down, the primary gets no requests at all.
    run(chain.get_address_summary("a2"))
    assert primary.calls == ["a1"]

    # After the 30 s cooldown it is tried again; once it recovers it answers.
    clock.now += 31
    primary.error = None
    assert run(chain.get_address_summary("a3")).provenance.provider == "primary"
    assert chain.state["primary"].consecutive_failures == 0


def test_cooldown_doubles_on_repeated_rate_limits():
    clock = Clock()
    primary = FakeSource("primary", ProviderError("slow down", 429))
    chain = FailoverBitcoinProvider([primary, FakeSource("backup")], clock=clock)
    run(chain.get_address_summary("a1"))
    clock.now += 31
    run(chain.get_address_summary("a2"))
    assert 59 < chain.state["primary"].cooldown_until - clock.now <= 60


def test_not_found_is_final_and_not_retried_elsewhere():
    primary = FakeSource("primary", ProviderError("rejected the request", 404))
    backup = FakeSource("backup")
    chain = FailoverBitcoinProvider([primary, backup], clock=Clock())
    try:
        run(chain.get_address_summary("a1"))
        raise AssertionError("expected ProviderError")
    except ProviderError as exc:
        assert exc.status_code == 404
    assert backup.calls == []
    assert chain.state["primary"].cooldown_until == 0


def test_all_sources_down_reports_429_with_retry_after():
    chain = FailoverBitcoinProvider([
        FakeSource("primary", ProviderError("rate limit", 429)),
        FakeSource("backup", ProviderError("did not answer", 504)),
    ], clock=Clock())
    try:
        run(chain.get_address_summary("a1"))
        raise AssertionError("expected ProviderError")
    except ProviderError as exc:
        assert exc.status_code == 429
        assert exc.retry_after == 10  # the backup's error cooldown ends first
        assert exc.headers() == {"Retry-After": "10"}
        assert "primary" in exc.message and "backup" in exc.message


def test_answers_are_cached():
    clock = Clock()
    source = FakeSource("primary")
    chain = FailoverBitcoinProvider([source], clock=clock)
    run(chain.get_address_summary("a1"))
    run(chain.get_address_summary("a1"))
    assert source.calls == ["a1"]
    clock.now += 31  # address summaries are reused for 30 s
    run(chain.get_address_summary("a1"))
    assert source.calls == ["a1", "a1"]


def test_unsupported_methods_skip_the_source():
    calls = []

    class NoBlocks(FakeSource):
        unsupported = frozenset({"get_block_stats"})

    class Blocks(FakeSource):
        async def get_block_stats(self, height):
            calls.append(height)
            return []

    chain = FailoverBitcoinProvider([NoBlocks("esplora"), Blocks("mempool")], clock=Clock())
    assert run(chain.get_block_stats(800000)) == []
    assert calls == [800000]


# ---- the real adapters, with recorded responses -----------------------------------------


def fake_api(rate_limited: bool):
    def handler(request: httpx.Request) -> httpx.Response:
        if rate_limited:
            return httpx.Response(429, text="Too Many Requests")
        path = request.url.path
        if path == f"/api/address/{SYNTHETIC_ADDRESS}":
            return httpx.Response(200, json=ADDRESS_RESPONSE)
        if path == f"/api/tx/{SYNTHETIC_TXID}":
            return httpx.Response(200, json=TX_RESPONSE)
        if path == "/api/blocks/tip/height":
            return httpx.Response(200, text="800123")
        return httpx.Response(404, text="not found")
    return httpx.MockTransport(handler)


def test_blockstream_adapter_converts_exactly_like_mempool():
    mempool = MempoolSpaceProvider("https://mempool.test/api", "mainnet", 5, transport=fake_api(False))
    esplora = BlockstreamEsploraProvider("https://esplora.test/api", "mainnet", 5, transport=fake_api(False))
    a = run(mempool.get_transaction(SYNTHETIC_TXID)).model_dump(exclude={"provenance"})
    b = run(esplora.get_transaction(SYNTHETIC_TXID))
    assert a == b.model_dump(exclude={"provenance"})
    assert b.provenance.provider == "blockstream.info"
    assert b.provenance.source_url == f"https://esplora.test/api/tx/{SYNTHETIC_TXID}"


def test_api_uses_backup_when_mempool_is_rate_limited():
    chain = FailoverBitcoinProvider([
        MempoolSpaceProvider("https://mempool.test/api", "mainnet", 5, transport=fake_api(True)),
        BlockstreamEsploraProvider("https://esplora.test/api", "mainnet", 5, transport=fake_api(False)),
    ])
    app.dependency_overrides[get_bitcoin_provider] = lambda: chain
    try:
        client = TestClient(app)
        body = client.get(f"/api/bitcoin/address/{SYNTHETIC_ADDRESS}").json()
        assert body["provenance"]["provider"] == "blockstream.info"
        assert body["confirmed"]["balance_sats"] == 100000

        status = client.get("/api/bitcoin/providers").json()
        mempool, esplora = status["sources"]
        assert mempool["provider"] == "mempool.space" and not mempool["ready"]
        assert mempool["last_error_status"] == 429
        assert esplora["ready"] and esplora["answers"] == 1
        assert status["recent_fallbacks"][0]["answered_by"] == "blockstream.info"

        health = client.get("/api/bitcoin/health").json()
        assert health["provider"] == "blockstream.info" and health["tip_height"] == 800123
    finally:
        app.dependency_overrides.pop(get_bitcoin_provider, None)
