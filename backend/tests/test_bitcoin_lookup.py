"""Offline tests: a fake transport replays the synthetic fixture instead of calling the internet."""
import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.providers.bitcoin_mempool import MempoolSpaceProvider
from app.providers.registry import get_bitcoin_provider
from tests.fixtures.mempool_synthetic import ADDRESS_RESPONSE, SYNTHETIC_ADDRESS, SYNTHETIC_TXID, TX_RESPONSE


def fake_mempool(request: httpx.Request) -> httpx.Response:
    """Plays the part of mempool.space. Returns a canned response for each known path."""
    path = request.url.path
    if path == f"/api/address/{SYNTHETIC_ADDRESS}":
        return httpx.Response(200, json=ADDRESS_RESPONSE)
    if path == f"/api/tx/{SYNTHETIC_TXID}":
        return httpx.Response(200, json=TX_RESPONSE)
    if path == "/api/blocks/tip/height":
        return httpx.Response(200, text="800123")
    if path == "/api/tx/" + "b" * 64:
        return httpx.Response(429, text="Too Many Requests")
    return httpx.Response(400, text="Invalid Bitcoin address")


def use_fake_provider():
    app.dependency_overrides[get_bitcoin_provider] = lambda: MempoolSpaceProvider(
        base_url="https://mempool.test/api", network="mainnet", timeout_seconds=5,
        transport=httpx.MockTransport(fake_mempool),
    )


client = TestClient(app)


def test_health():
    assert client.get("/api/health").json()["status"] == "ok"


def test_address_lookup_separates_confirmed_and_unconfirmed():
    use_fake_provider()
    body = client.get(f"/api/bitcoin/address/{SYNTHETIC_ADDRESS}").json()
    assert body["entity_id"] == f"bitcoin:mainnet:address:{SYNTHETIC_ADDRESS}"
    assert body["confirmed"]["balance_sats"] == 100000
    assert body["unconfirmed"]["tx_count"] == 1
    assert body["provenance"]["provider"] == "mempool.space"


def test_transaction_lookup_keeps_all_inputs_and_outputs():
    use_fake_provider()
    body = client.get(f"/api/bitcoin/tx/{SYNTHETIC_TXID}").json()
    assert body["status"] == "confirmed"
    assert len(body["inputs"]) == 1 and len(body["outputs"]) == 2
    assert body["fee_sats"] == 1200


def test_invalid_address_is_rejected_before_calling_provider():
    response = client.get("/api/bitcoin/address/not-an-address")
    assert response.status_code == 422


def test_rate_limit_is_reported_as_429():
    use_fake_provider()
    response = client.get("/api/bitcoin/tx/" + "b" * 64)
    assert response.status_code == 429
    assert "rate limit" in response.json()["detail"]


def test_provider_health_reports_tip_height():
    use_fake_provider()
    assert client.get("/api/bitcoin/health").json()["tip_height"] == 800123

class FakeStore:
    """Records what the routes ask to save, instead of using a real database."""

    enabled = True

    def __init__(self):
        self.addresses, self.transactions, self.investigations, self.health = [], [], [], []

    def save_address(self, summary):
        self.addresses.append(summary)

    def save_transactions(self, transactions):
        self.transactions.extend(transactions)

    def save_investigation(self, result):
        self.investigations.append(result)

    def save_health(self, health):
        self.health.append(health)

    def get_investigation(self, investigation_id):
        found = [r for r in self.investigations if r.investigation_id == investigation_id]
        return found[0].model_dump(mode="json") if found else None


def use_fake_store() -> FakeStore:
    from app.store import get_store

    store = FakeStore()
    app.dependency_overrides[get_store] = lambda: store
    return store


def test_lookups_are_saved():
    use_fake_provider()
    store = use_fake_store()
    client.get(f"/api/bitcoin/address/{SYNTHETIC_ADDRESS}")
    client.get(f"/api/bitcoin/tx/{SYNTHETIC_TXID}")
    client.get("/api/bitcoin/health")
    assert store.addresses[0].address == SYNTHETIC_ADDRESS
    assert store.transactions[0].txid == SYNTHETIC_TXID
    assert store.health[0].tip_height == 800123


def test_failed_lookup_is_not_saved():
    use_fake_provider()
    store = use_fake_store()
    client.get("/api/bitcoin/tx/" + "b" * 64)
    assert store.transactions == []


def test_investigation_is_saved_and_can_be_reopened():
    use_fake_provider()
    store = use_fake_store()
    body = client.post("/api/investigations", json={
        "mode": "crypto_investigation", "input_type": "bitcoin_txid", "value": SYNTHETIC_TXID, "method": "live_lookup",
    }).json()
    assert store.investigations[0].investigation_id == body["investigation_id"]
    assert store.transactions[0].txid == SYNTHETIC_TXID
    reopened = client.get(f"/api/investigations/{body['investigation_id']}").json()
    assert reopened["graph"] == body["graph"]
