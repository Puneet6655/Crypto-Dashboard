"""Investigate's "Load older transactions": POST /api/investigations/older-transactions."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.bitcoin_mempool import MempoolSpaceProvider
from app.providers.registry import get_bitcoin_provider
from tests.fixtures.mempool_synthetic import SYNTHETIC_ADDRESS, TX_RESPONSE

client = TestClient(app)
CURSOR, OLDER = "1" * 64, "2" * 64


@pytest.fixture(autouse=True)
def _restore_overrides():
    yield
    app.dependency_overrides.pop(get_bitcoin_provider, None)


def fake(request: httpx.Request) -> httpx.Response:
    if request.url.path == f"/api/address/{SYNTHETIC_ADDRESS}/txs/chain/{CURSOR}":
        return httpx.Response(200, json=[{**TX_RESPONSE, "txid": OLDER}])
    return httpx.Response(404, text="not found")


def test_older_page_comes_in_the_lookup_graph_shape():
    app.dependency_overrides[get_bitcoin_provider] = lambda: MempoolSpaceProvider(
        base_url="https://mempool.test/api", network="mainnet", timeout_seconds=5, transport=httpx.MockTransport(fake))
    answer = client.post("/api/investigations/older-transactions",
                         json={"address": SYNTHETIC_ADDRESS, "after_txid": CURSOR, "pages": 2, "evidence_id": "eo1"})
    assert answer.status_code == 200
    data = answer.json()
    # One transaction, and the page was not full, so the oldest history is reached.
    assert data["transactions"] == 1 and data["complete"] and data["next_cursor"] == ""
    types = {node["type"] for node in data["graph"]["nodes"]}
    assert types == {"transaction", "address"}
    edges = data["graph"]["edges"]
    assert any(e["id"].startswith(f"{OLDER}:in:") and e["source"].endswith(SYNTHETIC_ADDRESS) for e in edges)
    tx_id = next(node["id"] for node in data["graph"]["nodes"] if node["type"] == "transaction")
    assert any(e["id"].startswith(f"{OLDER}:out:") and e["source"] == tx_id for e in edges)
    assert all(e["evidence_ids"] == ["eo1"] for e in edges)
    assert data["evidence"]["id"] == "eo1"


def test_bad_cursor_is_rejected():
    assert client.post("/api/investigations/older-transactions",
                       json={"address": SYNTHETIC_ADDRESS, "after_txid": "xyz"}).status_code == 422


def test_ethereum_older_activity_reads_up_to_the_block(tmp_path):
    from app.intelligence.index import IndicatorIndex, get_indicator_index
    from app.providers.registry import get_ethereum_provider
    from tests import test_ethereum as eth_fakes

    state = {}
    app.dependency_overrides[get_ethereum_provider] = lambda: eth_fakes.provider(state)
    app.dependency_overrides[get_indicator_index] = lambda: IndicatorIndex(tmp_path)
    try:
        answer = client.post("/api/investigations/older-transactions",
                             json={"chain": "ethereum", "address": eth_fakes.ADDR, "cursor": "150", "evidence_id": "eo1"})
    finally:
        app.dependency_overrides.pop(get_ethereum_provider, None)
        app.dependency_overrides.pop(get_indicator_index, None)
    assert answer.status_code == 200, answer.text
    data = answer.json()
    assert data["chain"] == "ethereum" and data["transactions"] == 2 and data["complete"]
    tx_nodes = [n for n in data["graph"]["nodes"] if n["type"] == "transaction"]
    assert all(n["details"]["block_time"] for n in tx_nodes)
    assert all(e["evidence_ids"] == ["eo1"] for e in data["graph"]["edges"])


def test_solana_older_activity_reads_before_the_signature(tmp_path):
    from app.intelligence.index import IndicatorIndex, get_indicator_index
    from app.providers.registry import get_solana_provider
    from tests import test_solana as sol_fakes

    calls = []
    p = sol_fakes.provider(calls)
    app.dependency_overrides[get_solana_provider] = lambda: p
    app.dependency_overrides[get_indicator_index] = lambda: IndicatorIndex(tmp_path)
    try:
        answer = client.post("/api/investigations/older-transactions",
                             json={"chain": "solana", "address": sol_fakes.ME, "cursor": sol_fakes.SIG1, "evidence_id": "eo2"})
    finally:
        app.dependency_overrides.pop(get_solana_provider, None)
        app.dependency_overrides.pop(get_indicator_index, None)
    assert answer.status_code == 200, answer.text
    data = answer.json()
    assert data["chain"] == "solana" and data["transactions"] >= 1 and data["complete"]
    assert "getSignaturesForAddress" in calls
    assert data["evidence"]["id"] == "eo2"


def test_ethereum_cursor_must_be_a_block_number():
    from app.providers.registry import get_ethereum_provider
    from tests import test_ethereum as eth_fakes

    app.dependency_overrides[get_ethereum_provider] = lambda: eth_fakes.provider()
    try:
        assert client.post("/api/investigations/older-transactions",
                           json={"chain": "ethereum", "address": eth_fakes.ADDR, "cursor": "abc"}).status_code == 422
    finally:
        app.dependency_overrides.pop(get_ethereum_provider, None)
