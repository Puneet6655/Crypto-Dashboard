"""Milestone 5 paging: GET /api/bitcoin/address/{address}/transactions?after_txid=..."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.bitcoin_mempool import MempoolSpaceProvider
from app.providers.registry import get_bitcoin_provider
from app.store import get_store
from tests.fixtures.mempool_synthetic import SYNTHETIC_ADDRESS, TX_RESPONSE
from tests.test_bitcoin_lookup import use_fake_store

client = TestClient(app)
FIRST, SECOND = "1" * 64, "2" * 64
seen: list[str] = []


@pytest.fixture(autouse=True)
def _restore_overrides():
    """Do not leave the fake provider and store behind for later test files."""
    yield
    app.dependency_overrides.pop(get_bitcoin_provider, None)
    app.dependency_overrides.pop(get_store, None)


def fake(request: httpx.Request) -> httpx.Response:
    seen.append(request.url.path)
    if request.url.path == f"/api/address/{SYNTHETIC_ADDRESS}/txs":
        return httpx.Response(200, json=[{**TX_RESPONSE, "txid": FIRST}])
    if request.url.path == f"/api/address/{SYNTHETIC_ADDRESS}/txs/chain/{FIRST}":
        return httpx.Response(200, json=[{**TX_RESPONSE, "txid": SECOND}])
    if request.url.path == f"/api/address/{SYNTHETIC_ADDRESS}/txs/chain/{SECOND}":
        return httpx.Response(200, json=[])
    return httpx.Response(404, text="not found")


def use_provider():
    app.dependency_overrides[get_bitcoin_provider] = lambda: MempoolSpaceProvider(
        base_url="https://mempool.test/api", network="mainnet", timeout_seconds=5, transport=httpx.MockTransport(fake))


def test_pages_follow_after_txid_until_empty():
    use_provider()
    use_fake_store()
    first = client.get(f"/api/bitcoin/address/{SYNTHETIC_ADDRESS}/transactions").json()
    assert [t["txid"] for t in first] == [FIRST]
    second = client.get(f"/api/bitcoin/address/{SYNTHETIC_ADDRESS}/transactions", params={"after_txid": FIRST}).json()
    assert [t["txid"] for t in second] == [SECOND]
    assert client.get(f"/api/bitcoin/address/{SYNTHETIC_ADDRESS}/transactions", params={"after_txid": SECOND}).json() == []


def test_bad_paging_input_is_rejected_before_calling_the_provider():
    use_provider()
    seen.clear()
    assert client.get(f"/api/bitcoin/address/{SYNTHETIC_ADDRESS}/transactions", params={"after_txid": "xyz"}).status_code == 422
    assert client.get("/api/bitcoin/address/not-an-address/transactions").status_code == 422
    assert seen == []
