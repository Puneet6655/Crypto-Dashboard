"""Offline tests for advanced graph mode (app/graph/expand.py and POST /api/graph/expand)."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.graph import expand as gx
from app.intelligence.index import get_indicator_index
from app.main import app
from app.providers.base import ProviderError
from app.providers.failover import TTLCache
from app.providers.registry import get_bitcoin_provider
from app.schemas.bitcoin import Provenance, TransactionSummary, TxEndpoint

ME = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
SANCTIONED = "12QtD5BFwRsdNsAZY76UVE1xyCGNTojH9h"
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def addr(i: int) -> str:
    return f"1BoatSLRHtKNngkdXEeobR76b53LETt{i:03d}"  # address-shaped; fetches do not validate


def tx(i: int, inputs, outputs, when=None) -> TransactionSummary:
    return TransactionSummary(
        entity_id=f"bitcoin:mainnet:tx:{i:064x}", network="mainnet", txid=f"{i:064x}", status="confirmed",
        block_height=800_000 + i, block_time=when or T0 - timedelta(days=i), fee_sats=100,
        inputs=[TxEndpoint(address=a, value_sats=v) for a, v in inputs],
        outputs=[TxEndpoint(address=a, value_sats=v) for a, v in outputs],
        provenance=Provenance(provider="fake", source_url="fake", retrieved_at=T0, data_mode="live"))


class FakeBitcoin:
    name = "fake-bitcoin"

    def __init__(self, pages: dict[str, list[TransactionSummary]], fail: dict[str, ProviderError] | None = None):
        self.pages, self.fail, self.calls, self.answered_by = pages, fail or {}, [], {}

    async def get_address_transactions(self, address, after_txid=None):
        self.calls.append(address)
        if address in self.fail:
            raise self.fail[address]
        self.answered_by[("txs", address, None)] = self.name
        return self.pages.get(address, [])


class FakeIndex:
    def lookup(self, address, source_ids=None):
        return [SimpleNamespace(title="OFAC sanctioned addresses")] if address == SANCTIONED else []


def me_page():
    """ME pays 0.5 BTC to SANCTIONED, 0.1 BTC to each of 50 others, gets dust from 3 addresses."""
    others = [(addr(i), 10_000_000) for i in range(50)]
    txs = [tx(1, [(ME, 80_000_000)], [(SANCTIONED, 50_000_000), (ME, 29_000_000)]),
           tx(2, [(ME, 600_000_000)], others + [(ME, 99_000_000)])]
    for i in range(3):
        txs.append(tx(10 + i, [(f"3J98t1WpEZ73CNmQviecrnyiWrnqRhWNL{i}", 5_000)], [(ME, 546)]))
    return txs


def collect(gen):
    async def run():
        return [event async for event in gen]
    return asyncio.run(run())


def test_counterparties_dust_and_summary():
    n = asyncio.run(gx.fetch_bitcoin(FakeBitcoin({ME: me_page()}), ME))
    assert n.counterparties[SANCTIONED].sent == gx.Decimal("0.5")
    center, nodes, edges, next_up = gx.build_batch(n, per_node_limit=10, index=FakeIndex())
    kinds = [node["kind"] for node in nodes]
    # The sanctioned address is shown first and never hidden; 10 ordinary ones; dust and "+more" summaries.
    assert nodes[0]["address"] == SANCTIONED and nodes[0]["listed"] == ["OFAC sanctioned addresses"]
    assert kinds.count("address") == 11
    dust = next(node for node in nodes if node["kind"] == "dust")
    more = next(node for node in nodes if node["kind"] == "more")
    assert dust["count"] == 3 and more["count"] == 40 and len(more["members"]) == 40
    assert center["kind"] == "address" and center["expanded"]
    assert next_up[0] == SANCTIONED and len(next_up) == 11
    ids = {node["id"] for node in nodes} | {center["id"]}
    assert all(e["source"] in ids and e["target"] in ids for e in edges)


def test_busy_address_is_a_hub():
    burst = [tx(i, [(addr(i), 2_000_000)], [(ME, 1_000_000)], when=T0 + timedelta(minutes=i)) for i in range(30)]
    n = asyncio.run(gx.fetch_bitcoin(FakeBitcoin({ME: burst}), ME))
    assert n.hub_reason() and "within" in n.hub_reason()
    center, nodes, _, _ = gx.build_batch(n, per_node_limit=40, index=None)
    assert center["kind"] == "hub"
    assert sum(node["kind"] == "address" for node in nodes) == gx.HUB_TOP


def test_two_hops_stream_respects_node_limit_and_cache():
    friend = addr(0)
    pages = {ME: me_page(), friend: [tx(99, [(friend, 10_000_000)], [(addr(i + 100), 1_000_000) for i in range(30)])]}
    provider = FakeBitcoin(pages)
    expander = gx.GraphExpander(daily_cu=1000)
    gx.MIN_GAP_SECONDS["bitcoin"] = 0
    events = collect(gx.expand_stream(expander, provider, "bitcoin", [ME], 2, 20, 10, FakeIndex()))
    assert events[0]["event"] == "start" and events[-1]["event"] == "done"
    sent = [node for e in events if e["event"] == "batch" for node in e["nodes"]]
    assert len(sent) + 1 == 20 and events[-1]["new_nodes"] == 20
    assert events[-1]["stopped"] == "node_limit"
    # A second request for ME is served from the graph cache: no new provider call.
    calls = len(provider.calls)
    again = collect(gx.expand_stream(expander, provider, "bitcoin", [ME], 1, 100, 10, None))
    assert len(provider.calls) == calls and again[-1]["from_cache"] == 1


def test_rate_limit_stops_the_stream_with_an_error_event():
    provider = FakeBitcoin({}, fail={ME: ProviderError("slow down", 429, retry_after=30)})
    gx.MIN_GAP_SECONDS["bitcoin"] = 0
    events = collect(gx.expand_stream(gx.GraphExpander(1000), provider, "bitcoin", [ME], 1, 100, 10, None))
    assert [e["event"] for e in events] == ["start", "progress", "error", "done"]
    assert events[-1]["stopped"] == "rate_limited"


class FakeSolana:
    name = "alchemy.com"

    def __init__(self):
        self.cache = TTLCache()

    async def get_signatures(self, address):
        raise AssertionError("must not be called when the allowance is used up")


def test_alchemy_allowance_blocks_fetches():
    expander = gx.GraphExpander(daily_cu=100)  # less than one Solana address costs
    events = collect(gx.expand_stream(expander, FakeSolana(), "solana",
                                      ["9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"], 1, 100, 10, None))
    assert any(e["event"] == "warning" and e["code"] == "budget" for e in events)
    assert events[-1]["stopped"] == "budget"


def test_budget_resets_each_day():
    day = [datetime(2026, 11, 1, tzinfo=timezone.utc)]
    budget = gx.Budget(1000, clock=lambda: day[0])
    budget.spend(900)
    assert budget.remaining == 100
    day[0] += timedelta(days=1)
    assert budget.remaining == 1000


def test_expand_route_streams_ndjson():
    gx.MIN_GAP_SECONDS["bitcoin"] = 0
    gx._expander = None
    app.dependency_overrides[get_bitcoin_provider] = lambda: FakeBitcoin({ME: me_page()})
    app.dependency_overrides[get_indicator_index] = lambda: FakeIndex()
    try:
        client = TestClient(app)
        with client.stream("POST", "/api/graph/expand", json={"chain": "bitcoin", "addresses": [ME]}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("application/x-ndjson")
            events = [json.loads(line) for line in response.iter_lines() if line]
        assert [e["event"] for e in events][:2] == ["start", "progress"]
        batch = next(e for e in events if e["event"] == "batch")
        assert batch["expanded"]["address"] == ME and len(batch["nodes"]) > 10
        bad = client.post("/api/graph/expand", json={"chain": "bitcoin", "addresses": ["nope"]})
        assert bad.status_code == 422
    finally:
        app.dependency_overrides.pop(get_bitcoin_provider, None)
        app.dependency_overrides.pop(get_indicator_index, None)
        gx._expander = None


# ---- exchange labels (Dune cex.addresses) ----------------------------------------------------------

def test_exchange_labels_name_nodes_and_stop_the_walk():
    from app.intelligence.cex_labels import CexLabels
    labels = CexLabels([{"blockchain": "bitcoin", "address": addr(3), "cex_name": "Binance",
                         "distinct_name": "Binance 7", "added_by": "someone", "added_date": "2024-01-01"},
                        {"blockchain": "ethereum", "address": addr(4), "cex_name": "Kraken"}])
    n = asyncio.run(gx.fetch_bitcoin(FakeBitcoin({ME: me_page()}), ME))
    _, nodes, _, next_up = gx.build_batch(n, per_node_limit=10, index=None, labels=labels)
    binance = next(node for node in nodes if node.get("address") == addr(3))
    assert binance["kind"] == "exchange" and binance["label"] == "Binance 7"
    assert binance["exchange"]["exchange"] == "Binance"
    assert addr(3) not in next_up  # exchanges are not walked through
    assert all(node["kind"] != "exchange" for node in nodes if node.get("address") == addr(4))  # other chain

    # An expanded exchange is treated like a hub: only its top counterparties are drawn.
    labels_me = CexLabels([{"blockchain": "bitcoin", "address": ME, "cex_name": "Coinbase"}])
    center, nodes, _, _ = gx.build_batch(n, per_node_limit=40, index=None, labels=labels_me)
    assert center["kind"] == "exchange" and center["label"] == "Coinbase"
    assert sum(node["kind"] == "address" for node in nodes) == gx.HUB_TOP


def test_dune_download_follows_the_execution(tmp_path):
    import httpx
    from app.intelligence import cex_labels

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.headers["X-Dune-Api-Key"] == "k" * 20
        if request.url.path.endswith("/sql/execute"):
            assert "cex.addresses" in json.loads(request.content)["sql"]
            return httpx.Response(200, json={"execution_id": "E1", "state": "QUERY_STATE_PENDING"})
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"state": "QUERY_STATE_COMPLETED"})
        if "page2" in str(request.url):
            return httpx.Response(200, json={"result": {"rows": [{"blockchain": "solana", "address": "So1", "cex_name": "OKX"}]}})
        return httpx.Response(200, json={"result": {"rows": [
            {"blockchain": "ethereum", "address": "0xABCDEF0000000000000000000000000000000001", "cex_name": "Binance",
             "distinct_name": "Binance 1"}]}, "next_uri": "https://api.dune.com/api/v1/execution/E1/results?page2=1"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    github_down = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    labels = cex_labels.refresh(tmp_path, api_key="k" * 20, client=client, github_client=github_down)
    assert labels.via == "dune_api"
    assert labels.lookup("ethereum", "0xabcdef0000000000000000000000000000000001").name == "Binance 1"
    assert labels.lookup("solana", "So1").exchange == "OKX"
    assert cex_labels.load(tmp_path).status()["addresses"] == 2
    assert calls[0].endswith("/sql/execute")


def test_dune_bad_key_is_a_readable_error(tmp_path):
    import httpx
    import pytest
    from app.intelligence import cex_labels

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "invalid"})))
    with pytest.raises(RuntimeError, match="refused the API key"):
        cex_labels.download("bad", client)


def test_spellbook_seed_parsing_and_github_fallback(tmp_path):
    import httpx
    from app.intelligence import cex_labels

    btc = """FROM (VALUES
    ('bitcoin', '1Cb1G5qFK91fShyaPPZWVFwYFBtqRG7h8D', 'Coinbase', 'Coinbase 1', 'hildobby', date '2023-04-06')
    , ('bitcoin', 'bc1qXYZ', 'O''Brien Exchange', 'OB 1', 'someone', date '2024-01-02')"""
    sol = "(VALUES ('solana', '5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9', 'Binance', 'Binance 1', 'hildobby', date '2024-04-19')"
    evm = "(VALUES\n -- Binance\n (0x28C6c06298d514Db089934071355E5743bf21d60, 'Binance', 'Binance 14', 'hildobby', date '2022-08-28')"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        text = btc if "bitcoin" in path else sol if "solana" in path else evm
        return httpx.Response(200, text=text)

    def dune_unused(request):
        raise AssertionError("GitHub answered, so the Dune API must not be called")

    labels = cex_labels.refresh(tmp_path, api_key="k" * 20, client=httpx.Client(transport=httpx.MockTransport(dune_unused)),
                                github_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert labels.via == "github_spellbook"
    assert labels.lookup("bitcoin", "bc1qxyz").exchange == "O'Brien Exchange"
    assert labels.lookup("ethereum", "0x28c6c06298d514db089934071355e5743bf21d60").name == "Binance 14"
    assert labels.lookup("base", "0x28c6c06298d514db089934071355e5743bf21d60") is not None
    assert labels.status()["per_chain"] == {"bitcoin": 2, "solana": 1, "ethereum": 1, "base": 1}
